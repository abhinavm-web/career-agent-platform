import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('repository_privacy', Path(__file__).parents[1] / 'scripts/check_repository_privacy.py')
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


class RepositoryPrivacyTests(unittest.TestCase):
    def test_private_paths_and_credentials_are_detected_without_values(self):
        for path in ('.local/workspace.json', 'private/profile.json', 'data/filings.csv',
                     'resume.pdf', 'candidate.docx', 'source.xlsx', '.env.production'):
            self.assertIn('private data or credential file', privacy.violations(path, b'fictional'))
        key = b'sk-ant-' + b'a' * 45
        self.assertEqual(privacy.violations('settings.py', key), ['provider credential'])
        literal = b'api_key = \"' + b'a1B2c3D4' * 6 + b'\"'
        self.assertEqual(privacy.violations('settings.py', literal), ['literal credential assignment'])
        self.assertNotIn(key.decode(), str(privacy.violations('settings.py', key)))
        self.assertEqual(privacy.violations('settings.py', b'-----BEGIN ' + b'PRIVATE KEY-----'), ['private key'])

    def test_fictional_examples_and_environment_references_are_allowed(self):
        self.assertEqual(privacy.violations('.env.example', b'ANTHROPIC_API_KEY=YOUR_API_KEY'), [])
        self.assertEqual(privacy.violations('examples/profile.example.json', b'{"name":"Fictional Candidate"}'), [])
        self.assertEqual(privacy.violations('app.py', b'os.environ["ANTHROPIC_API_KEY"]'), [])
