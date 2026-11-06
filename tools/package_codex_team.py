#!/usr/bin/env python3
"""Package the current executor and generic coordination rules; no live changes."""
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path


ROOT = None  # Explicit source root; never discover a consumer runtime.


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    original = (ROOT / 'tools/team_executor.py').read_text()
    tree = ast.parse(original)
    assignments = {n.targets[0].id: n for n in tree.body
                   if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
    version = ast.literal_eval(assignments['VERSION'].value)
    old_rules = ast.literal_eval(assignments['CONTROLLER_RULES'].value)
    # A consumer project's AGENTS.md is not a distributable generic template.
    # Keep the canonical rules embedded in the executor, independent of this host.
    rules = old_rules
    if '## Contrato protegido do canvas\n' in rules:
        start = rules.index('## Contrato protegido do canvas\n')
        end = rules.index('## Segurança ao executar ferramentas Node', start)
        rules = rules[:start] + rules[end:]
    rule_node = assignments['CONTROLLER_RULES']
    lines = original.splitlines(keepends=True)
    packaged = ''.join(lines[:rule_node.lineno-1]) + 'CONTROLLER_RULES = ' + repr(rules) + '\n'
    packaged += ''.join(lines[rule_node.end_lineno:])
    # Only the generated instructions may differ from the live executor.
    def behavior(source):
        parsed = copy.deepcopy(ast.parse(source))
        for node in parsed.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'CONTROLLER_RULES':
                node.value = ast.Constant('')
        return ast.dump(parsed, include_attributes=False)
    assert behavior(original) == behavior(packaged)
    assert 'docs/contrato_canvas_loop.md' not in rules
    project = output
    package = project / 'src/codex_team'
    package.mkdir(parents=True)
    (package / 'executor.py').write_text(packaged)
    (package / '__init__.py').write_text('__version__ = ' + repr(version) + '\n')
    (package / '__main__.py').write_text('from .executor import entrypoint\nraise SystemExit(entrypoint())\n')
    (project / 'templates').mkdir()
    (project / 'templates/AGENTS.md').write_text(rules)
    (project / 'tests').mkdir()
    tests = (ROOT / 'tools/test_team_executor.py').read_text().replace(
        'import team_executor as executor', 'from codex_team import executor')
    (project / 'tests/test_team_executor.py').write_text(tests)
    (project / 'pyproject.toml').write_text('''[build-system]
requires = ["setuptools>=61", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "codex-team"
version = "''' + version + '''"
description = "Local tmux team controller with compact context and durable task queues"
readme = "README.md"
requires-python = ">=3.10"
dependencies = []

[project.scripts]
codex-team = "codex_team.executor:entrypoint"
codex-team-start = "codex_team.executor:start_entrypoint"

[tool.setuptools.packages.find]
where = ["src"]
''')
    (project / 'setup.py').write_text('from setuptools import setup\nsetup()\n')
    (project / 'MANIFEST.in').write_text('include PROVENANCE.json\nrecursive-include templates *.md\nrecursive-include tests *.py\n')
    readme = '''# codex-team ''' + version + '''

Executor local para equipes Codex com tmux, submódulos Git e filas persistentes.
Requer Python 3.10+, Git, tmux e a CLI Codex configurada, em sistema POSIX.
O pacote Python não tem dependências de runtime além da biblioteca padrão.

## Instalar a partir das fontes

```sh
python3 -m pip install .
codex-team --version
codex-team --root ./novo-time start --no-attach
```

Em instalações Python gerenciadas pelo sistema, use um venv ou pipx e mantenha
seu bin/ no PATH. Não é necessário ignorar as proteções do gerenciador Python.
`codex-team-start --root ./novo-time --no-attach` é um atalho equivalente.
O primeiro start exige uma pasta nova ou vazia. Depois use ./team na raiz criada.

## Configurações de economia incluídas

- Sessão nova por entrega; `--continue ID` retoma somente a mesma entrega.
- Esforço padrão low; modelo herdado do Codex, sem troca automática ou retry pago.
- `--context-file` fornece resumo de até 8000 caracteres sem chamada extra ao modelo.
- JSON compacto; status com tarefas ativas e contadores; result/wait compactos.
- Resultados pendentes mostram somente ID/estado; relatórios limitados e --full explícito.
- Relatórios de até 100 palavras, contratos/evidências em arquivo e tarefas agrupadas.
- Usage por delta cumulativo de sessão, separando métricas sem dupla contagem.
- Agentes ociosos aguardam em FIFO sem chamadas ao modelo.
- Terminal ocioso limpo com a frase Aguardando tarefas.; logs/resultados persistem.
- Consulta do esquema já migrado sem criar lock de escrita desnecessário.

```sh
./team config --effort low
./team task backend --file tarefa.md --context-file contexto.md
./team task backend --continue ID --message 'Correção da mesma entrega'
./team status
./team wait ID --timeout 50
./team result ID
./team usage --task ID
```

Não há economia percentual garantida: compare medições equivalentes de usage.
Cache integra entrada; raciocínio integra saída. Não some essas partes novamente.
As instruções genéricas mantêm autorizações operacionais, isolamento Node e os
checks obrigatórios de cada projeto; redução de contexto não dispensa validação.

## Times existentes

Instalar este pacote atualiza a CLI de instalação. Cada time existente usa sua
própria cópia em tools/team_executor.py, preservada pela instalação. Atualizar outra raiz exige preservar seu estado/instruções, encerrar as entregas
com ./team stop e atualizar sua cópia de forma coordenada antes de start.
O pacote contém fontes selecionadas, testes e regras genéricas; dados, sessões,
repositórios consumidores e configurações privadas não são entradas do build.

## Testes offline do pacote fonte

```sh
PYTHONPATH=src python3 -B -m unittest discover -s tests
```

Os testes usam banco temporário e eventos simulados; não chamam tmux/modelos/rede.
PROVENANCE.json identifica hashes reais da origem, do executor empacotado e das
instruções. Somente CONTROLLER_RULES foi adaptado para o template genérico atual;
o restante da AST do executor é idêntico à origem.
'''
    (project / 'README.md').write_text(readme)
    provenance = {'version': version, 'source': 'tools/team_executor.py',
        'source_sha256': digest(original.encode()), 'packaged_sha256': digest(packaged.encode()),
        'generic_rules_sha256': digest(rules.encode()), 'behavior_ast_identical': True,
        'default_effort': 'low', 'default_model': None,
        'configuration_policy': 'fresh task sessions; explicit --continue; compact output',
        'source_tests_sha256': digest((ROOT/'tools/test_team_executor.py').read_bytes())}
    (project / 'PROVENANCE.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps(provenance, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True,
                        help='Root containing reviewed tools/team_executor.py and tests')
    parser.add_argument('--output', type=Path, required=True, help='New source directory')
    args = parser.parse_args()
    ROOT = args.source_root.resolve()
    build(args.output)
