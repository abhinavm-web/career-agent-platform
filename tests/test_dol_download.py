"""Offline tests for bounded DOL source acquisition."""
import hashlib
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from career_agent.public_sources import SourceUnavailable, public_download

URL = 'https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx'
HOSTS = {'www.dol.gov', 'dol.gov'}
DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]


class DOLDownloadTests(unittest.TestCase):
    def test_private_dns_and_untrusted_host_never_connect(self):
        with tempfile.TemporaryDirectory() as tmp, patch('career_agent.public_sources._PinnedHTTPS') as conn:
            target = Path(tmp) / 'download.xlsx'
            with self.assertRaises(SourceUnavailable):
                public_download('https://evil.invalid/file', HOSTS, target, maximum=10)
            private = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
            with patch('career_agent.public_sources.socket.getaddrinfo', return_value=private), self.assertRaises(SourceUnavailable):
                public_download(URL, HOSTS, target, maximum=10)
            conn.assert_not_called()

    def test_streams_hash_and_rejects_redirect_oversize_and_truncation(self):
        with tempfile.TemporaryDirectory() as tmp, patch('career_agent.public_sources.socket.getaddrinfo', return_value=DNS), patch('career_agent.public_sources._PinnedHTTPS') as constructor:
            conn = constructor.return_value
            response = conn.getresponse.return_value
            response.status = 302
            with self.assertRaisesRegex(SourceUnavailable, 'HTTP 302'):
                public_download(URL, HOSTS, Path(tmp) / 'redirect', maximum=4)
            self.assertEqual(conn.request.call_count, 1)
            headers = conn.request.call_args.kwargs['headers']
            self.assertNotIn('Cookie', headers)
            self.assertNotIn('Authorization', headers)
            response.status = 200
            response.getheader.return_value = '5'
            with self.assertRaisesRegex(SourceUnavailable, 'limit'):
                public_download(URL, HOSTS, Path(tmp) / 'oversized', maximum=4)
            response.getheader.return_value = None
            response.read1.side_effect = [b'12345']
            with self.assertRaisesRegex(SourceUnavailable, 'limit'):
                public_download(URL, HOSTS, Path(tmp) / 'oversized-stream', maximum=4)
            response.getheader.return_value = '5'
            response.read1.side_effect = [b'123', b'']
            with self.assertRaisesRegex(SourceUnavailable, 'incomplete'):
                public_download(URL, HOSTS, Path(tmp) / 'truncated', maximum=8)
            response.getheader.return_value = '4'
            response.read1.side_effect = [b'12', b'34', b'']
            progress = []
            result = public_download(URL, HOSTS, Path(tmp) / 'complete', maximum=4,
                                     progress=lambda phase, count: progress.append((phase, count)))
            self.assertEqual(result, {'bytes': 4, 'sha256': hashlib.sha256(b'1234').hexdigest()})
            self.assertEqual(progress[-1], ('downloading', 4))
            self.assertEqual((Path(tmp) / 'complete').read_bytes(), b'1234')


if __name__ == '__main__':
    unittest.main()
