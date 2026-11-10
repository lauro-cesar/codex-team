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

[tool.setuptools]
include-package-data = false

[tool.setuptools.packages.find]
where = ["src"]
include = ["codex_team"]
namespaces = false
''')
    (project / 'setup.py').write_text('from setuptools import setup\nsetup()\n')
    (project / 'MANIFEST.in').write_text('include PROVENANCE.json\ninclude templates/AGENTS.md\ninclude tests/test_team_executor.py\n')
    readme = '''# codex-team ''' + version + '''
Local executor for Codex teams with tmux, Git submodules and durable task queues.
Requires Python 3.10+, Git, tmux and a configured Codex CLI on a POSIX system.
The Python package has no runtime dependencies beyond the standard library.

## Install from source

```sh
python3 -m pip install .
codex-team --version
codex-team --root ./new-team start --no-attach
```

For system-managed Python installations, use a venv or pipx and keep its bin/
directory on PATH. There is no need to bypass Python package manager protections.
`codex-team-start --root ./new-team --no-attach` is an equivalent shortcut.
The first start requires a new or empty directory. Then use ./team in the created root.

## Included cost-saving settings

- A new session per deliverable; `--continue ID` resumes only the same deliverable.
- Default effort is low; the model is inherited from Codex, with no automatic switch or paid retry.
- `--context-file` supplies a summary of up to 8000 characters without an extra model call.
- Compact JSON; status includes active tasks and counters; compact result/wait output.
- Pending results show only ID/state; reports are bounded, with explicit --full output.
- Reports of up to 100 words, contracts/evidence in files and grouped tasks.
- Usage is measured by cumulative session deltas, separating metrics without double counting.
- Idle agents wait on a FIFO without model calls.
- Idle terminals are cleared and display the waiting-for-tasks message; logs/results persist.
- Already migrated schemas are queried without an unnecessary write lock.

```sh
./team config --effort low
./team task backend --file task.md --context-file context.md
./team task backend --continue ID --message 'Correction to the same deliverable'
./team status
./team wait ID --timeout 50
./team result ID
./team usage --task ID
```

No percentage savings are guaranteed: compare equivalent usage measurements.
Cached tokens are part of input; reasoning tokens are part of output. Do not add them again.
The generic instructions preserve operational authorization, Node isolation and each
project's required checks; reducing context does not waive validation.

## Existing teams

Installing this package updates the installation CLI. Each existing team uses its
own copy of tools/team_executor.py, which installation preserves. Updating another
root requires preserving its state/instructions, finishing deliveries with ./team stop
and coordinating the update to its copy before start.
The package contains selected sources, tests and generic rules; data, sessions,
consumer repositories and private configuration are excluded from build inputs.

## Offline source package tests

```sh
PYTHONPATH=src python3 -B -m unittest discover -s tests
```

Tests use temporary databases and simulated events; they do not call tmux, models or the network.
PROVENANCE.json records actual hashes of the source, packaged executor and instructions.
Only CONTROLLER_RULES was adapted to the current generic template during extraction;
the rest of the executor AST is identical to the source used for that extraction.
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
