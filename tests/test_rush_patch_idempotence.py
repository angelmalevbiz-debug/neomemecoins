from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts' / 'apply_momentum_rush_patch.py'


class RushPatchIdempotenceTests(unittest.TestCase):
    def run_patch(self, content):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'backend').mkdir()
            script = root / 'scripts' / SOURCE.name
            script.write_bytes(SOURCE.read_bytes())
            lab = root / 'backend' / 'strategy_lab.py'
            lab.write_bytes(content)
            result = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=10)
            self.assertEqual(lab.read_bytes(), content)
            self.assertFalse((root / 'strategy-lock.json').exists())
            return result

    def test_repeated_migration_preserves_reviewed_engine(self):
        result = self.run_patch(b"import momentum_rush_brain as rush_brain\n"
                                b"STRATEGIES=[{'id':'MOMENTUM_RUSH_BRAIN'}]\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'already integrated', result.stdout)

    def test_incompatible_partial_migration_refuses_without_writing(self):
        result = self.run_patch(b"STRATEGIES=[{'id':'MOMENTUM_RUSH_BRAIN'}]\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'expected exactly one match', result.stderr)
