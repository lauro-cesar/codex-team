#!/usr/bin/env python3
"""Local Codex team: tmux display, Git submodules and durable task queues."""
import argparse
from collections import Counter
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

VERSION = '0.3.0'

SLUG = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$')
TERMINAL = {'done', 'failed', 'interrupted'}
EFFORTS = ('minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')
TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens',
                'reasoning_output_tokens', 'cache_write_input_tokens')


def token_counts(value):
    if not isinstance(value, dict):
        return None
    if any(type(value.get(k)) is not int or value[k] < 0
           for k in ('input_tokens', 'cached_input_tokens', 'output_tokens')):
        return None
    return {k: value[k] for k in TOKEN_FIELDS if type(value.get(k)) is int and value[k] >= 0}


def usage_delta(reported, baseline):
    """CLI usage is cumulative per thread; never sum snapshots as task usage."""
    if reported is None or baseline is None:
        return None
    if any(reported[k] < baseline.get(k, 0) for k in reported):
        return None  # Counter reset/schema change: retain raw data, no invented delta.
    result = {k: v - baseline.get(k, 0) for k, v in reported.items()}
    if result['cached_input_tokens'] > result['input_tokens']:
        return None
    result['uncached_input_tokens'] = result['input_tokens'] - result['cached_input_tokens']
    return result


def excerpt(value, limit=1600):
    return value if len(value) <= limit else value[:limit] + '\n[truncado; use result --full ou o arquivo indicado]'


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, text=True, **kwargs)


def capture(args):
    return run(args, stdout=subprocess.PIPE).stdout.strip()


def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(',', ':')), flush=True)


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def show_idle_terminal():
    """Clear only the worker display; durable task logs/results stay intact."""
    if sys.stdout.isatty():
        command = ['cmd', '/c', 'cls'] if os.name == 'nt' else ['clear']
        try:
            cleared = subprocess.run(command, stderr=subprocess.DEVNULL, check=False).returncode == 0
        except OSError:
            cleared = False
        if not cleared:
            sys.stdout.write('\033[H\033[2J\033[3J')
    print('Aguardando tarefas.', flush=True)


class Team:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.state = self.root / '.codex-team'
        self.config_path = self.state / 'settings.json'

    def settings(self):
        if not self.config_path.exists():
            raise ValueError('Time não inicializado. Execute start primeiro.')
        return json.loads(self.config_path.read_text())

    def repository_path(self, name):
        relative = self.settings().get('agent_workdirs', {}).get(name, 'repos/' + name)
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root / 'repos'):
            raise ValueError('Diretório do responsável fora de repos/.')
        return path

    @contextlib.contextmanager
    def lock(self, name='topology', nonblocking=False):
        with (self.state / (name + '.lock')).open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
            yield

    @contextlib.contextmanager
    def db(self):
        conn = sqlite3.connect(self.state / 'team.sqlite3', timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def migrate(self):
        """Additive migration: preserve old tasks and per-agent legacy threads."""
        self.settings()
        with self.db() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] >= 2:
                return
        with self.lock():
            with self.db() as db:
                columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
                for column in ('parent_task', 'delivery', 'thread', 'model', 'effort',
                               'context', 'usage_baseline', 'usage_reported', 'usage'):
                    if column not in columns:
                        db.execute(f'ALTER TABLE tasks ADD COLUMN {column} TEXT')
                db.execute('PRAGMA user_version=2')

    def configure(self, model=None, effort=None):
        with self.lock():
            config = self.settings()
            if model is not None:
                if not model.strip() or model.startswith('-') or any(c in model for c in '\n\r\x00'):
                    raise ValueError('Modelo inválido.')
                config['default_model'] = None if model == 'inherit' else model
            if effort is not None:
                if effort not in EFFORTS:
                    raise ValueError('Esforço inválido.')
                config['default_effort'] = effort
            self.save_settings(config)
        return {'default_model': config.get('default_model'),
                'default_effort': config.get('default_effort', 'low'),
                'session_policy': 'new-task; --continue TASK_ID to resume'}

    def init(self, session=None, layout='panes'):
        if self.config_path.exists():
            return
        session = session or 'codex-team-' + hashlib.sha256(str(self.root).encode()).hexdigest()[:10]
        if not SLUG.fullmatch(session):
            raise ValueError('Nome da sessão inválido. Use letras, números, hífen e sublinhado (até 48 caracteres).')
        if self.root.exists() and any(self.root.iterdir()):
            raise ValueError('Use uma pasta nova ou vazia para a raiz do time; nenhum repositório existente será convertido.')
        self.root.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(mode=0o700)
        for directory in ['bin', 'logs', 'results', 'wake']:
            (self.state / directory).mkdir()
        run(['git', 'init', self.root], stdout=subprocess.DEVNULL)
        self.config_path.write_text(json.dumps({'session': session, 'layout': layout, 'stopping': False,
                                               'default_effort': 'low'}))
        (self.root / '.gitignore').write_text('.codex-team/\n')
        (self.root / 'repos').mkdir()
        (self.root / 'tools').mkdir()
        destination = self.root / 'tools/team_executor.py'
        shutil.copy2(Path(__file__).resolve(), destination)
        wrapper = '#!/usr/bin/env bash\nset -euo pipefail\nROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)\nexec python3 -B "$ROOT/tools/team_executor.py" --root "$ROOT" "$@"\n'
        (self.root / 'team').write_text(wrapper)
        (self.root / 'team').chmod(0o755)
        (self.root / 'AGENTS.md').write_text(CONTROLLER_RULES)
        (self.root / 'README.md').write_text('# Equipe Codex\n\nLeia AGENTS.md. Use `./team --help` para gerenciar o time.\n'
            'Os projetos em repos/ são submódulos; não há commits ou pushes automáticos.\n'
            'O runtime local fica em .codex-team/ (ignorado pelo Git).\n')
        with self.db() as db:
            db.executescript('''
                CREATE TABLE agents(name TEXT PRIMARY KEY, repo TEXT NOT NULL, branch TEXT,
                    thread TEXT, pane TEXT, status TEXT NOT NULL DEFAULT 'stopped');
                CREATE TABLE tasks(id TEXT PRIMARY KEY, agent TEXT NOT NULL, message TEXT NOT NULL,
                    status TEXT NOT NULL, created TEXT NOT NULL, started TEXT, finished TEXT, error TEXT);
            ''')

    def save_settings(self, data):
        temp = self.state / 'settings.tmp'
        temp.write_text(json.dumps(data, indent=2))
        temp.replace(self.config_path)

    def agent(self, name):
        with self.db() as db:
            row = db.execute('SELECT * FROM agents WHERE name=?', (name,)).fetchone()
        if row is None:
            raise ValueError(f'Agente desconhecido: {name}')
        return dict(row)

    def session_alive(self):
        return subprocess.run(['tmux', 'has-session', '-t', self.settings()['session']],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

    def pane_alive(self, pane):
        if not pane or not re.fullmatch(r'%\d+', pane):
            return False
        result = subprocess.run(['tmux', 'display-message', '-p', '-t', pane,
                                 '#{pane_dead}'], capture_output=True, text=True)
        return result.returncode == 0 and result.stdout.strip() == '0'

    def create_controller(self):
        config = self.settings()
        alive = self.session_alive()
        if alive:
            marker = subprocess.run(['tmux', 'show-options', '-v', '-t', config['session'],
                                     '@codex-team-root'], capture_output=True, text=True)
            if marker.stdout.strip() != str(self.root):
                raise ValueError('O nome da sessão tmux já está em uso por outro time ou processo.')
        if alive and self.pane_alive(config.get('controller_pane')):
            return
        command = shlex.join([sys.executable, '-B', str(self.root / 'tools/team_executor.py'),
                              '--root', str(self.root), '_controller'])
        if alive:
            pane = capture(['tmux', 'new-window', '-d', '-P', '-F', '#{pane_id}',
                            '-t', config['session'] + ':', '-n', 'controlador', '-c', self.root, command])
        else:
            pane = capture(['tmux', 'new-session', '-d', '-P', '-F', '#{pane_id}',
                            '-s', config['session'], '-n', 'equipe', '-c', self.root, command])
        config['controller_pane'] = pane
        self.save_settings(config)
        run(['tmux', 'set-option', '-t', config['session'], '@codex-team-root', str(self.root)])
        run(['tmux', 'set-option', '-t', config['session'], 'mouse', 'on'])
        run(['tmux', 'set-option', '-t', config['session'], 'pane-border-status', 'top'])
        run(['tmux', 'set-option', '-t', config['session'], 'pane-border-format', ' #{pane_title} '])
        run(['tmux', 'select-pane', '-t', pane, '-T', 'Controlador'])

    def add(self, name, repo, branch=None):
        validate_agent(name, repo, branch)
        with self.lock():
            if self.settings()['stopping']:
                raise ValueError('O time está parando. Execute start para reabrir a entrada de tarefas.')
            if not self.session_alive():
                raise ValueError('Sessão tmux indisponível. Execute start antes de adicionar agentes.')
            with self.db() as db:
                old = db.execute('SELECT * FROM agents WHERE name=?', (name,)).fetchone()
            if old:
                if old['repo'] != repo or old['branch'] != branch:
                    raise ValueError('Este nome já está associado a outro repositório ou branch.')
            else:
                target = self.root / 'repos' / name
                if target.exists():
                    raise ValueError(f'O destino já existe; revise antes de cadastrar: {target}')
                args = ['git', '-C', str(self.root), 'submodule', 'add']
                if branch:
                    args += ['--branch', branch]
                args += ['--', repo, 'repos/' + name]
                run(args)
                with self.db() as db:
                    db.execute('INSERT INTO agents(name,repo,branch) VALUES(?,?,?)', (name, repo, branch))
            self.launch_worker(name)
        return self.agent(name)

    def launch_worker(self, name):
        row = self.agent(name)
        if self.pane_alive(row['pane']):
            # A pane id can be reused by a fresh server; verify its identity too.
            result = subprocess.run(['tmux', 'show-options', '-p', '-v', '-t', row['pane'],
                                     '@codex-team-agent'], capture_output=True, text=True)
            marker = result.stdout.strip()
            if marker == str(self.root) + ':' + name:
                return
        fifo = self.state / 'wake' / name
        if not fifo.exists():
            os.mkfifo(fifo, mode=0o600)
        config = self.settings()
        command = shlex.join([sys.executable, '-B', str(self.root / 'tools/team_executor.py'), '--root', str(self.root), '_worker', name])
        if config['layout'] == 'panes':
            args = ['tmux', 'split-window', '-d', '-P', '-F', '#{pane_id}',
                    '-t', config['controller_pane'], '-c', self.repository_path(name), command]
            result = subprocess.run([str(a) for a in args], capture_output=True, text=True)
        else:
            result = None
        if result is None or result.returncode:
            pane = capture(['tmux', 'new-window', '-d', '-P', '-F', '#{pane_id}',
                            '-t', config['session'] + ':', '-n', name, '-c', self.repository_path(name), command])
        else:
            pane = result.stdout.strip()
            run(['tmux', 'select-layout', '-t', config['controller_pane'], 'tiled'], stdout=subprocess.DEVNULL)
        run(['tmux', 'select-pane', '-t', pane, '-T', name])
        run(['tmux', 'set-option', '-p', '-t', pane, '@codex-team-agent', str(self.root) + ':' + name])
        with self.db() as db:
            db.execute('UPDATE agents SET pane=? WHERE name=?', (pane, name))

    def remove(self, name, reason):
        """Unregister an idle owner; preserve its repository and all task history."""
        with self.lock():
            row = self.agent(name)
            config = self.settings()
            config['retiring_agents'] = sorted(set(config.get('retiring_agents', [])) | {name})
            self.save_settings(config)
            with self.db() as db:
                if db.execute("SELECT 1 FROM tasks WHERE agent=? AND status='running'", (name,)).fetchone():
                    raise ValueError('Responsável em execução; interrompa a tarefa e aguarde result antes de remover.')
            if self.pane_alive(row['pane']):
                marker = capture(['tmux', 'show-options', '-p', '-v', '-t', row['pane'], '@codex-team-agent'])
                if marker != str(self.root) + ':' + name:
                    raise ValueError('Identidade do painel divergente; nenhum painel removido.')
                run(['tmux', 'kill-pane', '-t', row['pane']])
            with self.db() as db:
                db.execute('CREATE TABLE IF NOT EXISTS removed_agents(name TEXT, removed TEXT, reason TEXT, registration TEXT)')
                db.execute('INSERT INTO removed_agents VALUES(?,?,?,?)',
                           (name, now(), reason, json.dumps(row)))
                db.execute("UPDATE tasks SET status='interrupted',finished=?,error=? WHERE agent=? AND status IN ('queued','pending')",
                           (now(), reason, name))
                db.execute('DELETE FROM agents WHERE name=?', (name,))
        return {'removed': name, 'repository_preserved': True, 'history_preserved': True}

    def rename(self, old_name, new_name, keep_repository_path=False):
        """Rename an idle owner and submodule without losing task identities."""
        with self.lock():
            row = self.agent(old_name)
            validate_agent(new_name, row['repo'], row['branch'])
            if old_name == new_name:
                raise ValueError('Os nomes devem ser diferentes.')
            source = self.repository_path(old_name)
            target = source if keep_repository_path else self.root / 'repos' / new_name
            original_config = self.settings()
            with self.db() as db:
                if row['status'] != 'idle' or db.execute(
                    "SELECT 1 FROM tasks WHERE agent=? AND status IN ('queued','pending','running') LIMIT 1",
                    (old_name,),
                ).fetchone():
                    raise ValueError('Responsável possui tarefa ativa; aguarde a conclusão antes de renomear.')
                if db.execute('SELECT 1 FROM agents WHERE name=?', (new_name,)).fetchone() or db.execute(
                    'SELECT 1 FROM tasks WHERE agent=? LIMIT 1', (new_name,),
                ).fetchone() or (not keep_repository_path and (target.exists() or target.is_symlink())):
                    raise ValueError('Nome ou destino já existe; nenhum responsável alterado.')
            if source.is_symlink() or not source.is_dir() or not (source / '.git').is_file():
                raise ValueError('Origem não é um submódulo regular; nenhum responsável alterado.')
            alive = self.pane_alive(row['pane'])
            if alive and capture(['tmux', 'show-options', '-p', '-v', '-t', row['pane'],
                                  '@codex-team-agent']) != str(self.root) + ':' + old_name:
                raise ValueError('Identidade do painel divergente; nenhum painel alterado.')
            # git mv updates the gitlink, .gitmodules path and core.worktree.
            # Keep the historical submodule section/gitdir identity intact.
            if source != target:
                run(['git', '-C', self.root, 'mv', '--', source.relative_to(self.root), target.relative_to(self.root)])
            try:
                if alive:
                    run(['tmux', 'kill-pane', '-t', row['pane']])
                with self.db() as db:
                    db.execute('CREATE TABLE IF NOT EXISTS renamed_agents('
                               'old_name TEXT,new_name TEXT,renamed TEXT,registration TEXT)')
                    db.execute('INSERT INTO renamed_agents VALUES(?,?,?,?)',
                               (old_name, new_name, now(), json.dumps(row)))
                    db.execute('UPDATE agents SET name=?,pane=NULL WHERE name=?', (new_name, old_name))
                    db.execute('UPDATE tasks SET agent=? WHERE agent=?', (new_name, old_name))
                    config = json.loads(json.dumps(original_config))
                    paths = config.setdefault('agent_workdirs', {})
                    paths.pop(old_name, None)
                    if keep_repository_path:
                        paths[new_name] = str(source.relative_to(self.root))
                    self.save_settings(config)
            except Exception:
                self.save_settings(original_config)
                if source != target:
                    run(['git', '-C', self.root, 'mv', '--', target.relative_to(self.root), source.relative_to(self.root)])
                if alive:
                    self.launch_worker(old_name)
                raise
            result = {'renamed': old_name, 'name': new_name, 'repository': str(target),
                      'history_preserved': True}
            try:
                self.launch_worker(new_name)
            except (OSError, subprocess.CalledProcessError) as error:
                result['worker_error'] = str(error)
            return result

    def wake(self, name):
        try:
            fd = os.open(self.state / 'wake' / name, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return
        try:
            os.write(fd, b'!')
        finally:
            os.close(fd)

    def enqueue(self, name, message, parent_task=None, model=None, effort=None, context=None,
                if_idle=False, request_id=None):
        if not message.strip():
            raise ValueError('A tarefa não pode ser vazia.')
        if context is not None and len(context) > 8000:
            raise ValueError('Contexto excede 8000 caracteres; use um resumo com caminhos e decisões.')
        if request_id is not None and not re.fullmatch(r'[A-Za-z0-9_:-]{1,128}', request_id):
            raise ValueError('request_id inválido.')
        fingerprint = hashlib.sha256(json.dumps([name, message, parent_task, model, effort,
                                                  context, if_idle], ensure_ascii=False).encode()).hexdigest()
        with self.lock():
            with self.db() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS task_requests(
                    request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, fingerprint TEXT NOT NULL)''')
                previous = db.execute('SELECT * FROM task_requests WHERE request_id=?', (request_id,)).fetchone()
                if previous:
                    if previous['fingerprint'] != fingerprint:
                        raise ValueError('request_id já utilizado para outro pedido.')
                    return previous['task_id']
            config = self.settings()
            if config['stopping']:
                raise ValueError('Time em encerramento; novas tarefas não são aceitas.')
            if name in config.get('retiring_agents', []):
                raise ValueError('Responsável desativado; novas tarefas não são aceitas.')
            row = self.agent(name)
            if if_idle and row['status'] != 'idle':
                raise ValueError('O agente não está ocioso; aguarde a conclusão.')
            if not self.session_alive() or not self.pane_alive(row['pane']):
                raise ValueError('O agente não está aberto. Execute start para restaurar os painéis.')
            parent = self.task(parent_task, full=True) if parent_task else None
            if parent and (parent['agent'] != name or parent['status'] not in TERMINAL or not parent['thread']):
                raise ValueError('--continue exige tarefa encerrada do mesmo responsável com sessão registrada.')
            if model is None:
                model = parent['model'] if parent else config.get('default_model')
            if effort is None:
                effort = (parent['effort'] if parent else None) or config.get('default_effort', 'low')
            if model is not None and (not model.strip() or model.startswith('-') or any(c in model for c in '\n\r\x00')):
                raise ValueError('Modelo inválido.')
            if effort not in EFFORTS:
                raise ValueError('Esforço inválido.')
            task_id = uuid.uuid4().hex
            delivery = (parent['delivery'] or parent['id']) if parent else task_id
            with self.db() as db:
                if if_idle and db.execute("SELECT 1 FROM tasks WHERE agent=? AND status IN ('queued','pending','running') LIMIT 1", (name,)).fetchone():
                    raise ValueError('O agente possui tarefa ativa; aguarde a conclusão.')
                # Prevent two branches from resuming the same mutable CLI thread.
                if parent:
                    latest = db.execute('SELECT id FROM tasks WHERE delivery=? OR id=? ORDER BY rowid DESC LIMIT 1',
                                        (delivery, delivery)).fetchone()
                    if latest['id'] != parent_task:
                        raise ValueError('Continue a tarefa mais recente desta entrega: ' + latest['id'])
                db.execute('''INSERT INTO tasks(id,agent,message,status,created,parent_task,delivery,model,effort,context)
                              VALUES(?,?,?,?,?,?,?,?,?,?)''',
                           (task_id, name, message, 'queued', now(), parent_task, delivery, model, effort, context))
                if request_id is not None:
                    db.execute('INSERT INTO task_requests VALUES(?,?,?)', (request_id, task_id, fingerprint))
        self.wake(name)
        return task_id

    def task_receipt(self, request_id):
        if not re.fullmatch(r'[A-Za-z0-9_:-]{1,128}', request_id):
            raise ValueError('request_id inválido.')
        with self.db() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_requests'").fetchone():
                return {'request_id': request_id, 'task_id': None}
            row = db.execute('SELECT task_id FROM task_requests WHERE request_id=?', (request_id,)).fetchone()
            return {'request_id': request_id, 'task_id': row['task_id'] if row else None}

    def task(self, task_id, full=False, include_result=True):
        with self.db() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise ValueError('Tarefa desconhecida: ' + task_id)
        result = dict(row)
        output = self.state / 'results' / (task_id + '.md')
        result['result_file'] = str(output)
        for key in ('usage_baseline', 'usage_reported', 'usage'):
            if result.get(key):
                result[key] = json.loads(result[key])
        if include_result and output.exists():
            result['result'] = output.read_text()
        if full:
            return result
        if result['status'] not in TERMINAL:
            return {k: result[k] for k in ('id', 'status')}
        compact = {k: result[k] for k in ('id', 'agent', 'status', 'result_file')}
        if result['error']:
            compact['error'] = excerpt(result['error'], 600)
        if 'result' in result:
            compact['result'] = excerpt(result['result'])
            compact['result_truncated'] = len(result['result']) > 1600
        return compact

    def snapshot(self, full=False):
        with self.db() as db:
            agents = [dict(row) for row in db.execute('SELECT * FROM agents ORDER BY name')]
            tasks = [dict(row) for row in db.execute('SELECT id,agent,status,created,error FROM tasks ORDER BY created,rowid')]
        for agent in agents:
            agent['pane_alive'] = self.pane_alive(agent['pane'])
        counts = dict(Counter(task['status'] for task in tasks))
        if not full:
            agents = [{k: a[k] for k in ('name', 'status', 'pane_alive')} for a in agents]
            tasks = [t for t in tasks if t['status'] not in TERMINAL]
        return {'root': str(self.root), 'session': self.settings()['session'],
                'stopping': self.settings()['stopping'], 'agents': agents, 'tasks': tasks,
                'task_counts': counts}

    def usage(self, agent=None, task_id=None, full=False):
        clauses, params = [], []
        if agent:
            self.agent(agent)
            clauses.append('agent=?')
            params.append(agent)
        if task_id:
            self.task(task_id, include_result=False)
            clauses.append('id=?')
            params.append(task_id)
        query = 'SELECT id,agent,status,model,effort,thread,usage,usage_reported FROM tasks'
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        with self.db() as db:
            rows = [dict(r) for r in db.execute(query + ' ORDER BY rowid', params)]
        totals, groups, details = Counter(), {}, []
        measured = 0
        for row in rows:
            usage = json.loads(row['usage']) if row['usage'] else None
            group = groups.setdefault(row['agent'], {'measured_tasks': 0, 'unmeasured_tasks': 0, 'tokens': Counter()})
            if usage is not None:
                totals.update(usage)
                group['tokens'].update(usage)
                group['measured_tasks'] += 1
                measured += 1
            else:
                group['unmeasured_tasks'] += 1
            if full or task_id:
                row['usage'] = usage
                row['usage_reported'] = json.loads(row['usage_reported']) if row['usage_reported'] else None
                details.append(row)
        if task_id and not full:
            row = details[0]
            return {k: row[k] for k in ('id', 'agent', 'status', 'model', 'effort', 'usage')}
        result = {'measured_tasks': measured, 'unmeasured_tasks': len(rows) - measured,
                  'tokens': dict(totals), 'agents': groups,
                  'note': 'Somente deltas medidos; histórico legado/contadores ausentes não contam como zero. '
                          'Reasoning é parte da saída, cache é parte da entrada; não some novamente.'}
        if full or task_id:
            result['tasks'] = details
        return result

    def worker(self, name):
        # Lock spans the complete worker lifetime: one executor per repository.
        with self.lock('worker-' + name, nonblocking=True):
            fd = os.open(self.state / 'wake' / name, os.O_RDWR | os.O_NONBLOCK)
            try:
                with self.db() as db:
                    db.execute("UPDATE tasks SET status='interrupted',finished=?,error=? WHERE agent=? AND status='running'",
                               (now(), 'Executor anterior terminou sem confirmar o resultado. Revise os arquivos antes de reenviar.', name))
                    db.execute("UPDATE agents SET status='idle' WHERE name=?", (name,))
                show_idle_terminal()
                while True:
                    with self.lock():
                        if self.settings()['stopping']:
                            break
                        with self.db() as db:
                            db.execute('BEGIN IMMEDIATE')
                            task = db.execute("SELECT * FROM tasks WHERE agent=? AND status='queued' ORDER BY rowid LIMIT 1", (name,)).fetchone()
                            if task:
                                db.execute("UPDATE tasks SET status='running',started=? WHERE id=?", (now(), task['id']))
                                db.execute("UPDATE agents SET status='running' WHERE name=?", (name,))
                    if task:
                        self.execute(name, dict(task))
                        show_idle_terminal()
                    else:
                        # FIFO blocks while idle; timeout only checks unexpected session shutdown.
                        ready, _, _ = select.select([fd], [], [], 30)
                        if ready:
                            os.read(fd, 4096)
            finally:
                os.close(fd)
                with self.db() as db:
                    db.execute("UPDATE agents SET status='stopped' WHERE name=?", (name,))

    def invocation(self, name, task):
        parent = self.task(task['parent_task'], full=True, include_result=False) if task.get('parent_task') else None
        thread = parent['thread'] if parent else None
        if parent and not thread:
            raise ValueError('Sessão da tarefa anterior indisponível; envie tarefa independente com contexto curto.')
        command = ['codex', '--sandbox', 'workspace-write', '--ask-for-approval', 'never',
                   '-C', str(self.repository_path(name))]
        if task.get('model'):
            command += ['-m', task['model']]
        command += ['-c', 'model_reasoning_effort=' + json.dumps(task.get('effort') or 'low'), 'exec']
        if thread:
            command += ['resume', thread]
        command += ['--json', '-o', str(self.state / 'results' / (task['id'] + '.md')), '-']
        role = '' if thread else (
            f'Você é responsável por {name}. Leia os AGENTS.md aplicáveis, incluindo ../../AGENTS.md. '
            'Edite somente este submódulo; preserve alterações existentes. Commit, push, deploy e '
            'operações destrutivas exigem autorização explícita. Registre bloqueios sem contornar o sandbox. '
            'Node/npm/npx/JavaScript somente em container isolado conforme AGENTS.md.\n\n')
        context = ('Contexto resumido fornecido pelo controlador:\n' + task['context'] + '\n\n') if task.get('context') else ''
        prompt = (role + context + f'Tarefa {task["id"]}:\n{task["message"]}\n\n'
                  'Execute até concluir ou demonstrar bloqueio real. Resposta final até 100 palavras: '
                  'arquivos/contrato, testes, bloqueios e próximo comando. Detalhes e evidências em arquivo. '
                  'Não repita pedido, histórico, contratos, logs ou checks aprovados. '
                  'Atualizações curtas só com informação nova útil ao controlador.')
        baseline = parent.get('usage_reported') if parent else {}
        return command, prompt, thread, baseline

    def execute(self, name, task):
        result_path = self.state / 'results' / (task['id'] + '.md')
        log_path = self.state / 'logs' / (task['id'] + '.jsonl')
        error, reported, baseline = None, None, None
        completed = False
        print(f'\n▶ Tarefa {task["id"]} ({task.get("effort") or "low"})', flush=True)
        try:
            command, prompt, expected_thread, baseline = self.invocation(name, task)
            with self.db() as db:
                db.execute('UPDATE tasks SET usage_baseline=? WHERE id=?',
                           (json.dumps(baseline) if baseline is not None else None, task['id']))
            with log_path.open('w') as log:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=log, text=True, cwd=self.repository_path(name))
                process.stdin.write(prompt)
                process.stdin.close()
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get('type') == 'thread.started':
                        thread = event['thread_id']
                        if expected_thread and thread != expected_thread:
                            baseline = None
                        with self.db() as db:
                            db.execute('UPDATE agents SET thread=? WHERE name=?', (thread, name))
                            db.execute('UPDATE tasks SET thread=?,usage_baseline=? WHERE id=?',
                                       (thread, json.dumps(baseline) if baseline is not None else None, task['id']))
                    elif event.get('type') == 'turn.completed':
                        completed = True
                        reported = token_counts(event.get('usage'))
                    elif event.get('type') in ('turn.failed', 'error'):
                        error = json.dumps(event, ensure_ascii=False)
                    elif event.get('type') == 'item.completed':
                        item = event.get('item', {})
                        if item.get('type') == 'agent_message':
                            print(excerpt(item.get('text', '')), flush=True)
                        elif item.get('type') == 'command_execution':
                            print('$ ' + excerpt(item.get('command', ''), 240), flush=True)
                            # Full tool output stays in the JSONL log, not repeated in the pane.
                code = process.wait()
                if code or not completed or not result_path.exists():
                    error = error or f'Codex terminou com código {code}; conclusão ou resultado ausente. Consulte {log_path}.'
        except (OSError, KeyError, ValueError) as exc:
            error = str(exc)
        status = 'failed' if error else 'done'
        usage = usage_delta(reported, baseline)
        with self.db() as db:
            db.execute('UPDATE tasks SET status=?,finished=?,error=?,usage_reported=?,usage=? WHERE id=?',
                       (status, now(), error, json.dumps(reported) if reported is not None else None,
                        json.dumps(usage) if usage is not None else None, task['id']))
            db.execute("UPDATE agents SET status='idle' WHERE name=?", (name,))
        print(f'\n■ {task["id"]}: {status}' + (f' — {error}' if error else ''), flush=True)

    def interrupt(self, task_id, reason):
        """Cancel only the matching Codex CLI, between tools; never signal services."""
        task = self.task(task_id, full=True, include_result=False)
        if task['status'] != 'running':
            raise ValueError('Interrupção exige tarefa em execução.')
        active = {}
        log_path = self.state / 'logs' / (task_id + '.jsonl')
        for line in log_path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get('item', {})
            if event.get('type') == 'item.started':
                active[item.get('id')] = item.get('type')
            elif event.get('type') == 'item.completed':
                active.pop(item.get('id'), None)
        if any(kind not in ('reasoning', 'agent_message') for kind in active.values()):
            emit({'interrupt_pending': task_id, 'reason': 'Ferramenta em execução; aguarde seu término.'})
            return 2
        expected = str(self.state / 'results' / (task_id + '.md'))
        matches = []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if proc.stat().st_uid != os.getuid():
                    continue
                argv = (proc / 'cmdline').read_bytes().decode().split('\0')
                if ('exec' not in argv or '--json' not in argv or '-o' not in argv
                        or argv[argv.index('-o') + 1] != expected
                        or 'codex' not in Path(argv[0]).name):
                    continue
                if (proc / 'cwd').resolve() != (self.root / 'repos' / task['agent']).resolve():
                    continue
                matches.append(int(proc.name))
            except (OSError, UnicodeError, IndexError):
                continue
        if len(matches) != 1:
            raise ValueError('Não foi identificado um único processo Codex da tarefa; nenhum sinal enviado.')
        folder = self.state / 'interrupts'
        folder.mkdir(exist_ok=True)
        receipt = folder / (task_id + '.json')
        receipt.write_text(json.dumps({'task': task_id, 'pid': matches[0], 'requested': now(),
                                       'reason': reason, 'signal': 'SIGINT'}, ensure_ascii=False) + '\n')
        receipt.chmod(0o600)
        os.kill(matches[0], signal.SIGINT)
        emit({'interrupt_requested': task_id, 'agent': task['agent'], 'reason': reason,
              'scope': 'Codex CLI somente; serviços e processos do projeto não foram sinalizados.'})
        return 0

    def stop(self, timeout):
        with self.lock():
            config = self.settings()
            config['stopping'] = True
            self.save_settings(config)
        with self.db() as db:
            names = [row['name'] for row in db.execute('SELECT name FROM agents')]
        for name in names:
            self.wake(name)
        deadline = time.monotonic() + timeout
        while True:
            busy = []
            for name in names:
                try:
                    with self.lock('worker-' + name, nonblocking=True):
                        pass
                except BlockingIOError:
                    busy.append(name)
            if not busy:
                break
            if time.monotonic() >= deadline:
                emit({'stopping': True, 'waiting_for': busy,
                      'message': 'Nenhum processo foi forçado a parar. Aguarde e execute stop novamente.'})
                return 2
            time.sleep(.25)
        # Workers that were killed externally may have left a running marker.
        with self.db() as db:
            db.execute("UPDATE tasks SET status='interrupted',finished=?,error=? WHERE status='running'",
                       (now(), 'Executor encerrado sem confirmação; revise antes de reenviar.'))
            db.execute("UPDATE agents SET status='stopped'")
        emit({'stopped': True, 'message': 'Responsáveis encerrados. Tarefas pendentes preservadas. '
              'O controlador e a sessão tmux continuam abertos; encerre-os quando o controlador estiver ocioso.'})
        return 0


CONTROLLER_RULES = "# Equipe Codex — instruções de coordenação\n\nNa raiz você é o controlador. Os projetos em repos/ são submódulos Git e têm\nresponsáveis próprios. Dentro de cada submódulo, atue como responsável daquele\nprojeto, respeitando suas instruções locais e o escopo da tarefa recebida.\n\n## Comandos do controlador (execute a partir da raiz)\n\n- `./team add --name NOME --repo URL [--branch BRANCH]`: cadastra um submódulo\n  e abre um responsável. Use nomes únicos com letras, números, hífen ou sublinhado.\n- `./team task NOME --message 'TAREFA'`: nova entrega/sessão; retorna ID.\n  Use `--file caminho` para texto longo e `--context-file caminho` para resumo\n  de decisões/caminhos (até 8000 caracteres), sem chamada extra ao modelo.\n  Use `--continue ID` para correções da mesma entrega já encerrada; indique\n  sempre a tarefa mais recente. `--model MODELO` e `--effort NIVEL` são opcionais.\n- `./team status`: agentes, tarefas ativas e contadores; `--full` inclui histórico.\n- `./team wait ID [ID ...] --timeout 50`: aguarda resultados (retorna 2 se ainda\n  estiverem pendentes). Continue aguardando enquanto houver trabalho necessário.\n- `./team result ID`: relatório compacto, erro e estado; `--full` recupera\n  pedido, contexto e relatório integral. Não trate failed/interrupted\n  como sucesso, nem done como prova de testes aprovados: leia o relatório.\n- `./team usage [--agent NOME | --task ID]`: tokens medidos por tarefa/agente;\n  `--full` inclui detalhes. Histórico legado não é contado como consumo zero.\n- `./team config [--model MODELO] [--effort NIVEL]`: padrões para novas entregas.\n  Esforço padrão `low`; escolha maior esforço quando a complexidade exigir.\n- `./team stop --timeout 50`: impede novas tarefas e deixa a atual terminar;\n  preserva pendentes e não interrompe processos à força.\n\n## Fluxo\n\nAo receber um pedido, defina contratos e dependências, distribua tarefas com\ncritérios de aceitação e acompanhe até concluir ou registrar um bloqueio real.\nOs responsáveis ficam ociosos sem chamadas ao modelo e só executam tarefas da\nfila. Cada entrega inicia sessão própria; continuações explícitas reutilizam a\nsessão e herdam modelo/esforço. As instruções de papel são enviadas na abertura.\nO controlador deve usar wait/result para receber conclusões: não há mensagem\nespontânea para reativar um controlador que já encerrou sua resposta.\nSó o controlador altera arquivos da raiz e coordena atualizações dos gitlinks.\nNão faça commits, pushes, deploys ou ações destrutivas sem autorização do usuário.\nNão altere simultaneamente um submódulo que já possui tarefa em execução.\nNão acrescente outros worktrees ou clones em repos/: todos os projetos devem\nser submódulos. Consulte git status na raiz e nos submódulos antes da integração.\nO histórico e resultados locais ficam em .codex-team/ e não são versionados.\nQuando o usuário pedir um novo responsável, use add com o nome e URL informados,\nsem pedir que ele abra um terminal manualmente. Permissões do sandbox continuam\nválidas; registre ou solicite a permissão necessária quando um comando for negado.\n\n## Economia de contexto e coordenação\n\nEstas regras valem para o controlador e todos os responsáveis, sem reduzir\nvalidações obrigatórias, segurança ou requisitos dos contratos locais.\n\n- Acione somente responsáveis afetados. Envie uma tarefa completa por entrega\n  coerente: objetivo, escopo, contratos/dependências, critérios de aceitação e\n  validações. Agrupe ajustes relacionados; não separe investigação, correção e\n  testes em chamadas distintas quando já estiverem autorizados.\n- Nos encaminhamentos, informe o que muda e referencie arquivos de contrato;\n  evite copiar históricos, logs e regras já presentes nos AGENTS.md aplicáveis.\n- Use status/wait/result compactos; peça `--full` somente quando precisar do\n  histórico ou relatório integral. Erros e truncamentos são sinalizados.\n- Use wait/result para as tarefas da entrega atual; não releia todo o histórico\n  a cada acompanhamento. Confirme pane_alive antes de distribuir trabalho.\n- Busque primeiro com rg em caminhos pertinentes e leia trechos necessários.\n  Exclua dependências, bundles, builds e logs das buscas de código, salvo quando\n  forem o objeto da investigação. Consulte documentação por assunto; evite\n  releituras integrais sem mudança ou dúvida concreta. Leia sempre as instruções\n  aplicáveis e os contratos necessários à tarefa.\n- Mantenha saídas extensas em arquivos locais apropriados, sem segredos; traga\n  apenas resultados, erros relevantes e caminhos das evidências ao contexto.\n  Se houver truncamento, refine a consulta em vez de repetir a saída integral.\n- Relate de forma concisa: comportamento/arquivos alterados, validações com\n  resultados reais, verificações não executadas, bloqueios e dependências.\n  Inclua detalhes adicionais quando necessários para avaliar riscos ou integrar.\n- O controlador verifica contratos, evidências e integração; repita investigação\n  ou testes do responsável somente por falha, mudança posterior, lacuna ou risco\n  identificado. Execute todos os checks obrigatórios e os pertinentes ao impacto;\n  não repita checks aprovados sem motivo novo.\n- Use `--continue ID` durante a mesma entrega; para outro assunto, envie tarefa\n  independente e contexto curto quando necessário. Não retome a sessão histórica\n  inteira do responsável nem abra outra sessão a cada correção relacionada.\n- Escolha modelo/esforço conforme a tarefa e acompanhe `./team usage`. A métrica\n  usa deltas dos contadores cumulativos por sessão; cache integra entrada e\n  raciocínio integra saída. Não some essas partes novamente nem estime economia\n  percentual sem medição comparável. Não há troca automática de modelo/retry.\n\n## Segurança ao executar ferramentas Node\n\nÉ obrigatório executar em container isolado qualquer comando que rode Node.js,\nnpm, npx ou scripts JavaScript, incluindo instalação de dependências e scripts\nde build, teste ou desenvolvimento. Não execute essas ferramentas diretamente\nno host.\n\nUse a imagem/runtime compatível com a versão exigida pelo projeto e monte somente\nos diretórios necessários, com escrita limitada aos artefatos da tarefa. Não\nmonte diretórios pessoais, arquivos de segredos nem o socket do Docker/Podman no\ncontainer. Execute sem privilégios; habilite rede apenas quando a tarefa exigir\ndownload de dependências ou acesso a um serviço explicitamente autorizado.\nRespeite instruções locais mais restritivas e registre qualquer bloqueio de\npermissão em vez de contornar o isolamento.\n"


def validate_agent(name, repo, branch=None):
    if not SLUG.fullmatch(name) or name.lower() in {'controlador', 'controller', 'chefe'}:
        raise ValueError('Nome de agente inválido ou reservado (até 48 caracteres).')
    if not isinstance(repo, str) or not repo or repo.startswith('-') or any(c in repo for c in '\n\r\x00'):
        raise ValueError('URL de repositório inválida.')
    if re.match(r'https?://[^/]*@', repo):
        raise ValueError('Não inclua credenciais na URL; use o login Git/SSH da máquina.')
    if branch is not None and (not isinstance(branch, str) or not branch or branch.startswith('-')
                               or subprocess.run(['git', 'check-ref-format', '--branch', branch],
                                                 capture_output=True).returncode):
        raise ValueError('Branch inválida.')


def load_agents(filename):
    value = json.loads(Path(filename).read_text())
    agents = value.get('agents') if isinstance(value, dict) else value
    if not isinstance(agents, list):
        raise ValueError('O config precisa conter {"agents": [{"name": ..., "repo": ...}]}.')
    names = set()
    for item in agents:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            raise ValueError('Cada agente precisa de name e repo.')
        validate_agent(item['name'], item.get('repo'), item.get('branch'))
        if item['name'].lower() in names:
            raise ValueError('Nome duplicado no config: ' + item['name'])
        names.add(item['name'].lower())
    return agents


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', action='version', version='codex-team ' + VERSION)
    p.add_argument('--root', default='codex-team', help='Team root directory (default: ./codex-team)')
    subs = p.add_subparsers(dest='command', required=True)
    start = subs.add_parser('start', help='Initialize or resume the team')
    start.add_argument('--root', default=argparse.SUPPRESS, help='Team root directory (default: ./codex-team)')
    start.add_argument('--version', action='version', version='codex-team ' + VERSION)
    start.add_argument('--agents', help='Agent JSON; use ./config.json if present')
    start.add_argument('--session')
    start.add_argument('--layout', choices=['panes', 'windows'], default='panes')
    start.add_argument('--no-attach', action='store_true')
    add = subs.add_parser('add', help='Add an agent/submodule')
    add.add_argument('--name', required=True)
    add.add_argument('--repo', required=True)
    add.add_argument('--branch')
    remove = subs.add_parser('remove', help='Remove an idle agent from the registry, preserving repository and history')
    remove.add_argument('name')
    remove.add_argument('--reason', required=True)
    rename = subs.add_parser('rename', help='Rename an idle agent and submodule, preserving history')
    rename.add_argument('old_name')
    rename.add_argument('new_name')
    rename.add_argument('--keep-repository-path', action='store_true',
                        help='Keep the submodule path and existing local references')
    task = subs.add_parser('task', help='Queue a task for an agent')
    task.add_argument('name')
    messages = task.add_mutually_exclusive_group(required=True)
    messages.add_argument('--message')
    messages.add_argument('--file', type=Path)
    task.add_argument('--continue', dest='parent_task', metavar='TASK_ID', help='Resume the latest task for the same deliverable')
    task.add_argument('--context-file', type=Path, help='Optional summary, up to 8000 characters; no extra model call')
    task.add_argument('--model', help='Model for this task; defaults to the team or Codex setting')
    task.add_argument('--effort', choices=EFFORTS, help='Default: low; continuation inherits the previous task setting')
    task.add_argument('--if-idle', '--require-idle', dest='if_idle', action='store_true', help='Atomically reject a target with an active task')
    task.add_argument('--request-id', help='Idempotent dispatch key')
    receipt = subs.add_parser('task-receipt', help='Read-only dispatch receipt lookup')
    receipt.add_argument('request_id')
    subs.add_parser('example-config', help='Print example JSON (replace the URLs)')
    status = subs.add_parser('status', help='Agents and active tasks; history only with --full')
    status_modes = status.add_mutually_exclusive_group()
    status_modes.add_argument('--full', action='store_true')
    status_modes.add_argument('--summary', action='store_true', help='Summary (default)')
    result = subs.add_parser('result')
    result.add_argument('task_id')
    result.add_argument('--full', action='store_true', help='Include request, context, metadata and report without truncation')
    wait = subs.add_parser('wait')
    wait.add_argument('task_ids', nargs='+')
    wait.add_argument('--timeout', type=float, default=50)
    wait.add_argument('--full', action='store_true')
    usage = subs.add_parser('usage', help='Usage by task and agent, without duplicating cumulative counters')
    usage.add_argument('--agent')
    usage.add_argument('--task', dest='task_id')
    usage.add_argument('--full', action='store_true', help='Include all tasks and recorded raw counters')
    config = subs.add_parser('config', help='View/set defaults for new tasks')
    config.add_argument('--model', help='Default model; inherit restores the Codex default')
    config.add_argument('--effort', choices=EFFORTS)
    stop = subs.add_parser('stop')
    stop.add_argument('--timeout', type=float, default=50)
    interrupt = subs.add_parser('interrupt', help='Interrupt a task between tools, without signaling services')
    interrupt.add_argument('task_id')
    interrupt.add_argument('--reason', required=True)
    subs.add_parser('attach')
    subs.add_parser('_controller', help=argparse.SUPPRESS)
    worker = subs.add_parser('_worker', help=argparse.SUPPRESS)
    worker.add_argument('name')
    return p


def attach(team):
    if not team.session_alive():
        raise ValueError('Sessão tmux indisponível. Execute start.')
    if os.environ.get('TMUX'):
        run(['tmux', 'switch-client', '-t', team.settings()['session']])
    else:
        os.execvp('tmux', ['tmux', 'attach-session', '-t', team.settings()['session']])


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == 'example-config':
        emit({'agents': [
            {'name': 'publicapi', 'repo': 'git@github.com:SUA_ORGANIZACAO/publicapi.git'},
            {'name': 'anunciantes', 'repo': 'git@github.com:SUA_ORGANIZACAO/flutter_anunciantes.git'},
            {'name': 'parceiros', 'repo': 'git@github.com:SUA_ORGANIZACAO/flutter_parceiros.git'},
        ]})
        return 0
    team = Team(args.root)
    if args.command != 'start':
        team.migrate()
    if args.command == 'start':
        for cmd in ('git', 'tmux', 'codex'):
            if not shutil.which(cmd):
                raise ValueError('Comando necessário não encontrado: ' + cmd)
        filename = args.agents or ('config.json' if Path('config.json').is_file() else None)
        agents = load_agents(filename) if filename else []
        team.init(args.session, args.layout)
        team.migrate()
        with team.lock():
            config = team.settings()
            config['stopping'] = False
            team.save_settings(config)
            team.create_controller()
        for item in agents:
            team.add(item['name'], item['repo'], item.get('branch'))
        with team.lock():
            with team.db() as db:
                names = [row['name'] for row in db.execute('SELECT name FROM agents')]
            for name in names:
                team.launch_worker(name)
        emit({'root': str(team.root), 'session': team.settings()['session'], 'agents': names})
        if not args.no_attach:
            attach(team)
    elif args.command == '_controller':
        # Interactive TUI remains available for the user's instructions.
        command = ['codex', '--sandbox', 'workspace-write', '--ask-for-approval', 'on-request',
                   '-C', str(team.root), 'Você é o controlador deste time. Leia AGENTS.md, '
                   'consulte ./team status e aguarde o pedido do usuário. Gerencie os responsáveis usando ./team.']
        os.execvp(command[0], command)
    elif args.command == '_worker':
        team.worker(args.name)
    elif args.command == 'add':
        emit(team.add(args.name, args.repo, args.branch))
    elif args.command == 'remove':
        emit(team.remove(args.name, args.reason))
    elif args.command == 'rename':
        emit(team.rename(args.old_name, args.new_name, args.keep_repository_path))
    elif args.command == 'task':
        message = args.file.read_text() if args.file else args.message
        context = args.context_file.read_text() if args.context_file else None
        emit({'task_id': team.enqueue(args.name, message, args.parent_task, args.model, args.effort, context,
                                     if_idle=args.if_idle, request_id=args.request_id)})
    elif args.command == 'task-receipt':
        emit(team.task_receipt(args.request_id))
    elif args.command == 'status':
        emit(team.snapshot(full=args.full))
    elif args.command == 'interrupt':
        return team.interrupt(args.task_id, args.reason)
    elif args.command == 'result':
        emit(team.task(args.task_id, full=args.full))
    elif args.command == 'usage':
        emit(team.usage(args.agent, args.task_id, args.full))
    elif args.command == 'config':
        emit(team.configure(args.model, args.effort))
    elif args.command == 'wait':
        deadline = time.monotonic() + max(0, min(args.timeout, 3600))
        while True:
            tasks = [team.task(task_id, full=args.full, include_result=False) for task_id in args.task_ids]
            if all(task['status'] in TERMINAL for task in tasks):
                emit([team.task(task_id, full=args.full) for task_id in args.task_ids])
                return 1 if any(task['status'] != 'done' for task in tasks) else 0
            if time.monotonic() >= deadline:
                emit(tasks)
                return 2
            time.sleep(.25)
    elif args.command == 'stop':
        return team.stop(max(0, args.timeout))
    elif args.command == 'attach':
        attach(team)
    return 0


def entrypoint(argv=None):
    try:
        return main(argv)
    except (ValueError, OSError, subprocess.CalledProcessError, sqlite3.Error) as exc:
        print('Erro: ' + str(exc), file=sys.stderr)
        return 1


def start_entrypoint():
    return entrypoint(['start', *sys.argv[1:]])


if __name__ == '__main__':
    sys.exit(entrypoint())
