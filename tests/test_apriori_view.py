import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AprioriViewTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for the Apriori view tests')
    def test_plain_language_view_helpers(self):
        result = subprocess.run(
            ['node', '--test', 'tests/apriori_view.test.js'],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
