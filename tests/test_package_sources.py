"""Verify the source-only export boundary using a disposable consumer root."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from codex_team import executor


class SourceExportTests(unittest.TestCase):
    def test_export_excludes_consumer_runtime_and_preserves_executor_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tools').mkdir()
            original = Path(executor.__file__).read_text()
            rules = executor.CONTROLLER_RULES.replace(
                '## Segurança ao executar ferramentas Node',
                '## Contrato protegido do canvas\n\nPRIVATE_CONSUMER_CONTRACT\n\n'
                '## Segurança ao executar ferramentas Node')
            original = original.replace(repr(executor.CONTROLLER_RULES), repr(rules))
            (root / 'tools/team_executor.py').write_text(original)
            (root / 'tools/test_team_executor.py').write_text('import team_executor as executor\n')
            for name in ('AGENTS.md', 'credentials', '.codex-team/logs/private.txt'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('PRIVATE_CONSUMER_DATA')
            output = root / 'export'
            script = Path(__file__).resolve().parents[1] / 'tools/package_codex_team.py'
            command = [sys.executable, '-B', str(script), '--source-root', str(root), '--output', str(output)]
            subprocess.run(command, check=True, capture_output=True)
            expected = {'src/codex_team/executor.py', 'src/codex_team/__init__.py',
                        'src/codex_team/__main__.py', 'templates/AGENTS.md',
                        'tests/test_team_executor.py', 'pyproject.toml', 'setup.py',
                        'MANIFEST.in', 'README.md', 'PROVENANCE.json'}
            self.assertEqual({str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()}, expected)
            for path in output.rglob('*'):
                if path.is_file():
                    self.assertNotIn('PRIVATE_CONSUMER_', path.read_text())
            packaged = (output / 'src/codex_team/executor.py').read_text()
            def behavior(source):
                tree = ast.parse(source)
                for node in tree.body:
                    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'CONTROLLER_RULES':
                        node.value = ast.Constant('')
                return ast.dump(tree, include_attributes=False)
            self.assertEqual(behavior(original), behavior(packaged))
            provenance = json.loads((output / 'PROVENANCE.json').read_text())
            self.assertTrue(provenance['behavior_ast_identical'])
            sentinel = output / 'keep.txt'
            sentinel.write_text('preserved')
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(sentinel.read_text(), 'preserved')
