import json
import unittest
from unittest.mock import patch
import httpx
from career_agent import ai


class LocalModelInventoryTests(unittest.TestCase):
    def setUp(self):
        self.route = {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}
        self.fingerprint = ai.route_fingerprint('local', self.route)
        self.routes = patch('career_agent.ai.routes', return_value={'local': self.route})
        self.routes.start()
        self.addCleanup(self.routes.stop)

    def client(self, handler):
        class Stream(httpx.SyncByteStream):
            def __init__(self, content): self.content = content
            def __iter__(self): yield self.content
        def streaming(request):
            response = handler(request)
            return httpx.Response(response.status_code, headers=response.headers, stream=Stream(response.content))
        return httpx.Client(transport=httpx.MockTransport(streaming), trust_env=False, follow_redirects=False)

    def test_explicit_request_sends_only_local_metadata_and_deduplicates(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={'data': [{'id': 'fictional'}, {'id': 'fictional'}, {'id': '<script>demo</script>'}]})
        with patch('career_agent.ai._inventory_transport', side_effect=lambda: self.client(handler)):
            result = ai.local_model_inventory('local', self.fingerprint)
        self.assertEqual(result['models'], ['fictional', '<script>demo</script>'])
        self.assertTrue(result['configured_listed'])
        self.assertEqual(str(seen[0].url), 'http://127.0.0.1:11434/v1/models')
        self.assertEqual(seen[0].method, 'GET')
        self.assertEqual(seen[0].content, b'')

    def test_rejects_stale_or_cloud_configuration_without_network(self):
        with patch('career_agent.ai._inventory_transport') as transport:
            with self.assertRaises(ValueError): ai.local_model_inventory('local', 'stale')
            with self.assertRaises(ValueError): ai.local_model_inventory('missing', self.fingerprint)
            cloud = {'provider': 'anthropic', 'model': 'fictional'}
            with patch('career_agent.ai.routes', return_value={'cloud': cloud}):
                with self.assertRaises(ValueError): ai.local_model_inventory('cloud', ai.route_fingerprint('cloud', cloud))
            transport.assert_not_called()

    def test_incomplete_list_does_not_claim_configured_model_absent(self):
        payload = {'data': [{'id': 'other'}] * 201}
        with patch('career_agent.ai._inventory_transport', side_effect=lambda: self.client(lambda _: httpx.Response(200, json=payload))):
            result = ai.local_model_inventory('local', self.fingerprint)
        self.assertTrue(result['incomplete'])
        self.assertIsNone(result['configured_listed'])

    def test_rejects_untrusted_errors_and_oversized_responses(self):
        responses = [httpx.Response(302, headers={'location': 'http://example.invalid'}),
                     httpx.Response(401, text='fictional-secret'),
                     httpx.Response(200, headers={'content-type': 'application/json'}, content=b'x' * (512 * 1024 + 1)),
                     httpx.Response(200, headers={'content-type': 'application/json'}, content=b'not json')]
        for response in responses:
            with self.subTest(status=response.status_code), patch('career_agent.ai._inventory_transport', side_effect=lambda: self.client(lambda _: response)):
                with self.assertRaises(ValueError) as error: ai.local_model_inventory('local', self.fingerprint)
                self.assertNotIn('fictional-secret', str(error.exception))
