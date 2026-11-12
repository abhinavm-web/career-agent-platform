"""Launcher behavior without installing packages or starting a server."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'launch.py'
spec = importlib.util.spec_from_file_location('career_agent_launcher', SCRIPT)
launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = self.root / '.venv'
        self.python = self.env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        root = patch.object(launcher, 'ROOT', self.root); root.start(); self.addCleanup(root.stop)

    def create_python(self):
        self.python.parent.mkdir(parents=True, exist_ok=True)
        self.python.touch()

    def test_new_environment_install_and_start_preserves_custom_workspace_paths(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[1:3] == ['-m', 'venv']: self.create_python()
            return SimpleNamespace(returncode=0)
        with patch.object(launcher.subprocess, 'run', side_effect=run):
            self.assertEqual(launcher.main(['--db', 'private/my workspace.sqlite3', '--packet-dir', 'private/packets', '--port', '8766']), 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][0][1:3], ['-m', 'venv'])
        self.assertIn('[web,jobspy,resume,lca,backup,ai]', calls[1][0][-1])
        self.assertEqual(calls[2][0][1:], ['-m', 'career_agent.cli', '--db', 'private/my workspace.sqlite3', 'serve', '--packet-dir', 'private/packets', '--port', '8766'])
        self.assertTrue(all(call[1]['cwd'] == self.root for call in calls))

    def test_existing_environment_can_start_without_installing(self):
        self.create_python()
        with patch.object(launcher.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(launcher.main(['--skip-install']), 0)
        self.assertEqual(run.call_count, 1)
        self.assertIn('career_agent.cli', run.call_args.args[0])

    def test_pdf_install_is_explicit(self):
        self.create_python()
        with patch.object(launcher.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(launcher.main(['--with-pdf']), 0)
        calls = [call.args[0] for call in run.call_args_list]
        self.assertIn(',pdf]', calls[0][-1])
        self.assertEqual(calls[1][1:], ['-m', 'playwright', 'install', 'chromium'])

    def test_partial_environment_and_failed_install_never_start_or_delete(self):
        self.env.mkdir(); marker = self.env / 'keep'; marker.write_text('fictional')
        with patch.object(launcher.subprocess, 'run') as run:
            self.assertEqual(launcher.main([]), 1)
            run.assert_not_called()
        self.assertTrue(marker.exists())
        self.create_python()
        with patch.object(launcher.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'pip')) as run:
            self.assertEqual(launcher.main([]), 1)
            self.assertEqual(run.call_count, 1)
        self.assertTrue(marker.exists())

    def test_invalid_flags_fail_before_creating_an_environment(self):
        for args in (['--port', '0'], ['--skip-install', '--with-pdf'], ['--db', ''], ['--db', 'existing.sqlite3']):
            with self.subTest(args=args), self.assertRaises(SystemExit): launcher.main(args)
        self.assertFalse(self.env.exists())
