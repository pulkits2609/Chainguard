"""Include the application-side Node adapter regressions in the shared test runner."""
from pathlib import Path
import shutil
import subprocess
import unittest


class EventAdapterTests(unittest.TestCase):
    def test_node_operational_adapter_regressions(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is required for the application-side event adapter checks')
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, '--test', 'adapters/event_adapter.test.mjs'], cwd=root,
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
