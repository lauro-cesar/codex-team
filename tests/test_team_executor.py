"""Offline tests: temporary legacy DB, fake CLI events, no tmux/model/network."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from codex_team import executor


class Input(io.StringIO):
    def close(self):
        self.saved = self.getvalue()
        super().close()


class ExecutorTests(unittest.TestCase):
    def test_rename_moves_real_submodule_and_preserves_tasks_and_checkout(self):
        self.root.joinpath('repos/app').rmdir()
        origin = self.root / 'origin'
        subprocess.run(['git', 'init', '-q', str(origin)], check=True)
        subprocess.run(['git', '-C', str(origin), '-c', 'user.name=Test',
                        '-c', 'user.email=test@example.invalid', 'commit', '-q',
                        '--allow-empty', '-m', 'bootstrap'], check=True)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'protocol.file.allow=always',
                        'submodule', 'add', '-q', str(origin), 'repos/app'], check=True)
        head = executor.capture(['git', '-C', self.root / 'repos/app', 'rev-parse', 'HEAD'])
        (self.root / 'repos/app/local.txt').write_text('preserved local work')
        (self.state / 'results/legacy.md').write_text('original report')
        with patch.object(self.team, 'pane_alive', return_value=False), \
             patch.object(self.team, 'launch_worker') as launch:
            result = self.team.rename('app', 'renamed')
        launch.assert_called_once_with('renamed')
        self.assertTrue(result['history_preserved'])
        self.assertFalse((self.root / 'repos/app').exists())
        self.assertEqual((self.root / 'repos/renamed/local.txt').read_text(), 'preserved local work')
        self.assertEqual(executor.capture(['git', '-C', self.root / 'repos/renamed',
                                          'rev-parse', 'HEAD']), head)
        self.assertEqual(executor.capture(['git', '-C', self.root, 'config', '-f', '.gitmodules',
                                          '--get', 'submodule.repos/app.path']), 'repos/renamed')
        self.assertEqual(self.team.task('legacy')['agent'], 'renamed')
        self.assertEqual(self.team.task('legacy')['result'], 'original report')
        self.assertEqual(self.team.agent('renamed')['thread'], 'legacy-thread')
        with self.team.db() as db:
            self.assertEqual(db.execute('SELECT old_name,new_name FROM renamed_agents').fetchone()[:],
                             ('app', 'renamed'))

    def test_rename_refuses_queued_or_running_tasks_before_mutation(self):
        task = self.enqueue()
        for status in ('queued', 'pending', 'running'):
            with self.team.db() as db:
                db.execute('UPDATE tasks SET status=? WHERE id=?', (status, task['id']))
            with patch.object(executor, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'tarefa ativa'):
                    self.team.rename('app', 'renamed')
                run.assert_not_called()
        self.assertEqual(self.team.agent('app')['name'], 'app')

    def test_rename_can_preserve_repository_path_for_existing_build_references(self):
        source = self.root / 'repos/app'
        (source / '.git').write_text('gitdir: preserved-metadata')
        (source / 'local.txt').write_text('existing build input')
        with patch.object(self.team, 'pane_alive', return_value=False), \
             patch.object(self.team, 'launch_worker'), patch.object(executor, 'run') as run:
            result = self.team.rename('app', 'frontend_app', keep_repository_path=True)
            run.assert_not_called()
        self.assertEqual(result['repository'], str(source))
        self.assertEqual(self.team.repository_path('frontend_app'), source)
        self.assertEqual(self.team.task('legacy')['agent'], 'frontend_app')
        self.assertEqual((source / 'local.txt').read_text(), 'existing build input')
        self.assertFalse((self.root / 'repos/frontend_app').exists())

    def test_repository_path_refuses_mapping_outside_repos(self):
        settings = self.team.settings()
        settings['agent_workdirs'] = {'app': '../outside'}
        self.team.save_settings(settings)
        with self.assertRaisesRegex(ValueError, 'fora de repos'):
            self.team.repository_path('app')

    def test_rename_refuses_existing_destination_before_mutation(self):
        (self.root / 'repos/renamed').mkdir()
        with patch.object(executor, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'destino já existe'):
                self.team.rename('app', 'renamed')
            run.assert_not_called()

    def test_rename_refuses_invalid_name_before_mutation(self):
        with patch.object(executor, 'run') as run:
            with self.assertRaises(ValueError):
                self.team.rename('app', '../other')
            run.assert_not_called()

    def test_remove_preserves_repository_and_history_and_cancels_queue(self):
        queued = self.enqueue()
        with patch.object(self.team, 'pane_alive', return_value=False):
            result = self.team.remove('app', 'User retired this owner')
        self.assertTrue(result['history_preserved'])
        self.assertTrue((self.root / 'repos/app').is_dir())
        self.assertEqual(self.team.task('legacy')['status'], 'done')
        self.assertEqual(self.team.task(queued['id'])['status'], 'interrupted')
        with self.assertRaisesRegex(ValueError, 'desconhecido'):
            self.team.agent('app')
        with self.team.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM removed_agents').fetchone()[0], 1)

    def test_remove_refuses_running_owner(self):
        with self.team.db() as db:
            db.execute("UPDATE tasks SET status='running' WHERE id='legacy'")
        with patch.object(executor, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'em execução'):
                self.team.remove('app', 'User request')
            run.assert_not_called()
        self.assertEqual(self.team.agent('app')['name'], 'app')
        with self.assertRaisesRegex(ValueError, 'desativado'):
            self.team.enqueue('app', 'Do not admit work while retiring')

    def test_remove_refuses_pane_with_different_identity(self):
        with patch.object(self.team, 'pane_alive', return_value=True), \
             patch.object(executor, 'capture', return_value='another:owner'), \
             patch.object(executor, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Identidade'):
                self.team.remove('app', 'User request')
            run.assert_not_called()
        self.assertEqual(self.team.agent('app')['name'], 'app')

    def test_request_receipt_survives_wake_failure_and_replay(self):
        with patch.object(self.team, 'wake', side_effect=OSError('wake failed')):
            with self.assertRaises(OSError):
                self.team.enqueue('app', 'work', if_idle=True, request_id='discord:123')
        receipt = self.team.task_receipt('discord:123')
        self.assertIsNotNone(receipt['task_id'])
        with patch.object(self.team, 'session_alive', return_value=False):
            self.assertEqual(self.team.enqueue('app', 'work', if_idle=True,
                                             request_id='discord:123'), receipt['task_id'])
        with self.team.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tasks WHERE message='work'").fetchone()[0], 1)

    def test_request_id_cannot_change_payload(self):
        self.team.enqueue('app', 'first', request_id='discord-123')
        with self.assertRaisesRegex(ValueError, 'outro pedido'):
            self.team.enqueue('app', 'changed', request_id='discord-123')
        self.assertIsNone(self.team.task_receipt('absent')['task_id'])

    def test_if_idle_refuses_other_producers_pending_and_running(self):
        task = self.enqueue()
        for state in ('queued', 'pending', 'running'):
            with self.team.db() as db:
                db.execute('UPDATE tasks SET status=? WHERE id=?', (state, task['id']))
            with self.assertRaisesRegex(ValueError, 'tarefa ativa'):
                self.team.enqueue('app', 'new', if_idle=True, request_id='discord-new')
            self.assertIsNone(self.team.task_receipt('discord-new')['task_id'])

    def test_concurrent_if_idle_accepts_only_one_request(self):
        from concurrent.futures import ThreadPoolExecutor
        def submit(i):
            try:
                return self.team.enqueue('app', 'work', if_idle=True, request_id=f'discord-{i}')
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(sum(value is not None for value in results), 1)

    def test_request_rejected_when_stopping_or_pane_dead(self):
        with patch.object(self.team, 'pane_alive', return_value=False):
            with self.assertRaises(ValueError):
                self.team.enqueue('app', 'work', if_idle=True, request_id='dead')
        config = self.team.settings()
        config['stopping'] = True
        self.team.save_settings(config)
        with self.assertRaises(ValueError):
            self.team.enqueue('app', 'work', if_idle=True, request_id='stopping')
        self.assertIsNone(self.team.task_receipt('dead')['task_id'])
        self.assertIsNone(self.team.task_receipt('stopping')['task_id'])

    def test_interrupt_rejects_finished_task_without_signal(self):
        with patch.object(executor.os, 'kill') as kill:
            with self.assertRaisesRegex(ValueError, 'em execução'):
                self.team.interrupt('legacy', 'new instruction')
            kill.assert_not_called()

    def test_interrupt_waits_for_running_tool_without_signal(self):
        with self.team.db() as db:
            db.execute("UPDATE tasks SET status='running' WHERE id='legacy'")
        (self.state / 'logs/legacy.jsonl').write_text(json.dumps({
            'type': 'item.started', 'item': {'id': 'tool1', 'type': 'command_execution'}}) + '\n')
        with patch.object(executor.os, 'kill') as kill, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.team.interrupt('legacy', 'new instruction'), 2)
            kill.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / '.codex-team'
        for folder in ('logs', 'results', 'wake'):
            (self.state / folder).mkdir(parents=True)
        (self.root / 'repos/app').mkdir(parents=True)
        (self.state / 'settings.json').write_text(json.dumps({'session': 'test', 'stopping': False}))
        self.team = executor.Team(self.root)
        with self.team.db() as db:
            db.executescript('''
                CREATE TABLE agents(name TEXT PRIMARY KEY,repo TEXT,branch TEXT,thread TEXT,pane TEXT,status TEXT);
                CREATE TABLE tasks(id TEXT PRIMARY KEY,agent TEXT,message TEXT,status TEXT,created TEXT,started TEXT,finished TEXT,error TEXT);
                INSERT INTO agents VALUES('app','repo',NULL,'legacy-thread','%1','idle');
                INSERT INTO tasks VALUES('legacy','app','old request','done','old',NULL,NULL,NULL);
            ''')
        self.team.migrate()
        self.patches = [patch.object(self.team, 'session_alive', return_value=True),
                        patch.object(self.team, 'pane_alive', return_value=True),
                        patch.object(self.team, 'wake')]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def enqueue(self, **kwargs):
        task_id = self.team.enqueue('app', 'Do the authorized work', **kwargs)
        return self.team.task(task_id, full=True)

    def execute(self, task, usage=None, failed=False, thread='thread-one'):
        usage = usage or dict(input_tokens=100, cached_input_tokens=60, output_tokens=10, reasoning_output_tokens=3)
        events = [{'type': 'thread.started', 'thread_id': thread},
                  {'type': 'item.started', 'item': {'type': 'agent_message', 'text': 'report'}},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'report'}},
                  {'type': 'turn.completed', 'usage': usage}]
        if failed:
            events.append({'type': 'turn.failed', 'error': {'message': 'explicit failure'}})
        process = type('Process', (), {})()
        process.stdin = Input()
        process.stdout = iter(json.dumps(e) + '\n' for e in events)
        process.wait = lambda: 1 if failed else 0
        def spawn(command, **kwargs):
            self.command = command
            Path(command[command.index('-o') + 1]).write_text('report')
            return process
        with patch.object(executor.subprocess, 'Popen', side_effect=spawn), contextlib.redirect_stdout(io.StringIO()) as output:
            self.team.execute('app', task)
        self.console = output.getvalue()
        self.prompt = process.stdin.saved
        return self.team.task(task['id'], full=True)

    def test_migration_preserves_legacy_and_is_idempotent(self):
        self.team.migrate()
        legacy = self.team.task('legacy', full=True)
        self.assertEqual(legacy['message'], 'old request')
        self.assertEqual(self.team.agent('app')['thread'], 'legacy-thread')
        self.assertIsNone(legacy['usage'])

    def test_pending_poll_does_not_repeat_paths_or_metadata(self):
        task = self.enqueue()
        self.assertEqual(self.team.task(task['id']), {'id': task['id'], 'status': 'queued'})
        self.assertIn('message', self.team.task(task['id'], full=True))

    def test_independent_tasks_do_not_resume_legacy_or_previous_session(self):
        first = self.execute(self.enqueue())
        self.assertNotIn('resume', self.command)
        self.assertIn('model_reasoning_effort="low"', self.command)
        second = self.enqueue()
        command, _, thread, baseline = self.team.invocation('app', second)
        self.assertNotIn('resume', command)
        self.assertIsNone(thread)
        self.assertEqual(baseline, {})
        self.assertNotEqual(first['delivery'], second['delivery'])
        self.assertEqual(self.console.count('report'), 1)

    def test_continuation_resumes_same_delivery_and_uses_delta(self):
        first = self.execute(self.enqueue(model='chosen-model', effort='medium'))
        followup = self.enqueue(parent_task=first['id'])
        self.assertEqual(followup['delivery'], first['delivery'])
        self.assertEqual(followup['model'], 'chosen-model')
        self.assertEqual(followup['effort'], 'medium')
        second = self.execute(followup, usage=dict(input_tokens=150, cached_input_tokens=90, output_tokens=30, reasoning_output_tokens=5))
        self.assertIn('resume', self.command)
        self.assertIn('thread-one', self.command)
        self.assertNotIn('Você é responsável', self.prompt)
        self.assertEqual(second['usage']['input_tokens'], 50)
        self.assertEqual(second['usage']['output_tokens'], 20)
        totals = self.team.usage()
        self.assertEqual(totals['tokens']['input_tokens'], 150)
        self.assertEqual(totals['unmeasured_tasks'], 1)  # Legacy isn't zero usage.

    def test_only_latest_continuation_can_be_used(self):
        first = self.execute(self.enqueue())
        self.enqueue(parent_task=first['id'])
        with self.assertRaisesRegex(ValueError, 'mais recente'):
            self.enqueue(parent_task=first['id'])

    def test_foreign_or_running_continuation_rejected(self):
        first = self.enqueue()
        with self.assertRaisesRegex(ValueError, 'encerrada'):
            self.enqueue(parent_task=first['id'])
        first = self.execute(first)
        with self.team.db() as db:
            db.execute("UPDATE tasks SET agent='other' WHERE id=?", (first['id'],))
        with self.assertRaisesRegex(ValueError, 'mesmo responsável'):
            self.enqueue(parent_task=first['id'])

    def test_defaults_are_frozen_and_overrides_do_not_change_global_config(self):
        self.team.configure(model='economical-model', effort='low')
        task = self.enqueue(effort='high')
        self.team.configure(model='different-model', effort='medium')
        self.assertEqual(task['model'], 'economical-model')
        self.assertEqual(task['effort'], 'high')
        self.assertEqual(self.team.configure()['default_model'], 'different-model')

    def test_reports_and_status_compact_by_default_but_full_is_lossless(self):
        task = self.enqueue()
        result = self.execute(task)
        report = 'long report ' * 1000
        (self.state / 'results' / (task['id'] + '.md')).write_text(report)
        short = self.team.task(task['id'])
        self.assertNotIn('message', short)
        self.assertTrue(short['result_truncated'])
        self.assertEqual(self.team.task(task['id'], full=True)['result'], report)
        self.assertEqual(self.team.snapshot()['tasks'], [])
        self.assertEqual(len(self.team.snapshot(full=True)['tasks']), 2)
        self.assertIn('done', self.team.snapshot()['task_counts'])

    def test_counter_reset_and_missing_metrics_are_not_counted_as_zero(self):
        first = self.execute(self.enqueue())
        second = self.execute(self.enqueue(parent_task=first['id']), usage=dict(input_tokens=10, cached_input_tokens=2, output_tokens=1))
        self.assertIsNone(second['usage'])
        self.assertIsNotNone(second['usage_reported'])
        self.assertEqual(self.team.usage()['measured_tasks'], 1)
        self.assertIsNone(executor.token_counts({'input_tokens': True}))
        self.assertIsNone(executor.usage_delta(None, {}))

    def test_failure_remains_failed_even_with_report_and_usage(self):
        result = self.execute(self.enqueue(), failed=True)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('explicit failure', result['error'])
        self.assertIsNotNone(result['usage'])

    def test_bounded_context_and_literal_model_argument(self):
        with self.assertRaisesRegex(ValueError, '8000'):
            self.enqueue(context='x' * 8001)
        task = self.enqueue(context='Paths and decisions only', model='model-with-literal-$(text)')
        self.execute(task)
        self.assertIn('Paths and decisions only', self.prompt)
        self.assertIn('model-with-literal-$(text)', self.command)

    def test_wait_preserves_failure_and_pending_exit_codes_without_echoing_request(self):
        task = self.enqueue()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = executor.main(['--root', str(self.root), 'wait', task['id'], '--timeout', '0'])
        self.assertEqual(code, 2)
        self.assertNotIn('Do the authorized work', output.getvalue())
        self.execute(task, failed=True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(executor.main(['--root', str(self.root), 'wait', task['id'], '--timeout', '0']), 1)


if __name__ == '__main__':
    unittest.main()
