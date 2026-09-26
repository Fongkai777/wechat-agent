import os
from pathlib import Path
import subprocess
import sys
import unittest


class CheckoutTests(unittest.TestCase):
    def test_voice_helpers_run_without_an_installed_project_or_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH="", PYTHONNOUSERSITE="1")
        for name in ("transcribe_voices.py", "voice_batch_prepare.py", "voice_batch_save.py"):
            with self.subTest(script=name):
                result = subprocess.run(
                    [sys.executable, str(root / "scripts" / name), "--help"],
                    cwd=root, env=env, capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)
