"""Offline routing, SDK wire contracts and evidence validation; fictional data only."""
import json
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from career_agent import ai
from career_agent.candidate import FactBank
from test_dashboard import fake_fact_bank, fake_job


class AITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'models.json'
        self.route = {'provider': 'ollama', 'model': 'fictional-model', 'base_url': 'http://127.0.0.1:11434/v1'}
        self.config.write_text(json.dumps({'local': self.route}))
        env = patch.dict(os.environ, {'CAREER_AGENT_AI_CONFIG': str(self.config)})
        env.start(); self.addCleanup(env.stop)
        self.job, self.bank = fake_job(), FactBank.parse(fake_fact_bank())
        self.output = {'requirements': [{'quote': 'Seeking Python and AWS.', 'fact_ids': ['experience-01'], 'reason': 'Potential related experience; verify scope.'}]}

    def test_consent_and_unknown_route_do_not_call_provider(self):
        with patch('career_agent.ai.complete') as complete:
            for route, consent in [('local', False), ('missing', True), ([], True)]:
                with self.assertRaises(ValueError):
                    ai.analyze(self.job, self.bank, route, consent)
            complete.assert_not_called()

    def test_provenance_minimized_payload_and_no_mutations(self):
        with patch('career_agent.ai.complete', return_value=json.dumps(self.output)) as complete:
            result = ai.analyze(self.job, self.bank, 'local', True)
        prompt = complete.call_args.args[1]
        self.assertNotIn(self.bank.name, prompt)
        self.assertNotIn(self.bank.facts[0].source_note, prompt)
        self.assertNotIn(self.job.url, prompt)
        self.assertEqual(result['requirements'][0]['facts'], self.bank.public_claims())
        self.assertEqual(len(result['input_sha256']), 64)
        self.assertEqual(result['requirements'][0]['status'], 'suggested_evidence_needs_review')

    def test_invalid_or_fabricated_evidence_is_rejected(self):
        invalid = ['not JSON', '```json\n{}\n```', '[]', '{}', '{"requirements":null}',
                   json.dumps({'requirements': [{'quote': 'invented', 'fact_ids': [], 'reason': 'unknown'}]}),
                   json.dumps({'requirements': [{'quote': 'Python', 'fact_ids': ['fake-id'], 'reason': 'unknown'}]}),
                   json.dumps({'requirements': [{'quote': 'Python', 'fact_ids': [], 'reason': 'ok', 'approved': True}]}),
                   'x' * 32001]
        for raw in invalid:
            with self.subTest(raw=raw[:70]), patch('career_agent.ai.complete', return_value=raw), self.assertRaises(ValueError):
                ai.analyze(self.job, self.bank, 'local', True)

    def test_missing_evidence_is_unknown(self):
        self.output['requirements'][0]['fact_ids'] = []
        with patch('career_agent.ai.complete', return_value=json.dumps(self.output)):
            self.assertEqual(ai.analyze(self.job, self.bank, 'local', True)['requirements'][0]['status'], 'unknown')

    def test_config_rejects_credentials_and_untrusted_endpoints(self):
        changes = [{'api_key': 'fictional-secret'}, {'provider': []}, {'max_tokens': True}, {'max_tokens': 99999},
                   {'key_env': 'not a variable'}, {'base_url': 'http://localhost:1234/v1'},
                   {'base_url': 'https://example.invalid/v1'}, {'base_url': 'http://127.0.0.1:1234/v1?token=x'},
                   {'provider': 'azure', 'base_url': 'https://example.openai.azure.com.evil.invalid/openai/v1'},
                   {'provider': 'bedrock', 'region': 'invalid'}]
        for change in changes:
            with self.subTest(change=change):
                self.config.write_text(json.dumps({'local': self.route | change}))
                with self.assertRaises(ValueError):
                    ai.routes()

    def test_input_limit_is_not_silently_truncated(self):
        from dataclasses import replace
        with patch('career_agent.ai.complete') as complete, self.assertRaises(ValueError):
            ai.analyze(replace(self.job, description='a' * 32001), self.bank, 'local', True)
        complete.assert_not_called()


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ('anthropic', 'openai', 'boto3', 'httpx')), 'Install optional AI dependencies')
class SDKContracts(unittest.TestCase):
    def test_openai_compatible_providers_and_azure_token_parameter(self):
        import httpx
        original = httpx.Client
        for provider, base in [('ollama', 'http://127.0.0.1:11434/v1'), ('lmstudio', 'http://127.0.0.1:1234/v1'),
                               ('azure', 'https://fictional.openai.azure.com/openai/v1')]:
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(200, json={'id': 'fictional', 'object': 'chat.completion', 'created': 0,
                    'model': 'fictional', 'choices': [{'index': 0, 'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': '{"requirements":[]}'}}]})
            with self.subTest(provider=provider), patch.dict(os.environ, {'AZURE_OPENAI_API_KEY': 'fictional-test-key'}), patch('career_agent.ai._transport', side_effect=lambda: original(transport=httpx.MockTransport(handler))):
                self.assertEqual(ai.complete({'provider': provider, 'model': 'fictional', 'base_url': base}, 'fictional prompt'), '{"requirements":[]}')
            self.assertEqual(len(calls), 1)
            self.assertEqual(str(calls[0].url), base + '/chat/completions')
            body = json.loads(calls[0].content)
            self.assertEqual(body.get('max_completion_tokens' if provider == 'azure' else 'max_tokens'), 1500)
            self.assertNotIn('tools', body)

    def test_anthropic_wire_contract_and_secret_error_redaction(self):
        import httpx
        original = httpx.Client
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'id': 'msg_fictional', 'type': 'message', 'role': 'assistant',
                'model': 'fictional', 'content': [{'type': 'text', 'text': '{"requirements":[]}'}],
                'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})
        route = {'provider': 'anthropic', 'model': 'fictional'}
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fictional-test-key'}), patch('career_agent.ai._transport', side_effect=lambda: original(transport=httpx.MockTransport(handler))):
            self.assertEqual(ai.complete(route, 'fictional prompt'), '{"requirements":[]}')
        self.assertEqual(str(calls[0].url), 'https://api.anthropic.com/v1/messages')
        self.assertEqual(json.loads(calls[0].content)['max_tokens'], 1500)
        calls.clear()
        def failed(request):
            calls.append(request)
            return httpx.Response(429, json={'error': {'type': 'rate_limit_error', 'message': 'PRIVATE PROMPT SECRET'}})
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fictional-test-key'}), patch('career_agent.ai._transport', side_effect=lambda: original(transport=httpx.MockTransport(failed))), self.assertRaises(ValueError) as caught:
            ai.complete(route, 'fictional prompt')
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(len(calls), 1)

    def test_bedrock_converse_contract(self):
        import boto3
        from botocore.stub import Stubber
        client = boto3.client('bedrock-runtime', region_name='us-east-1', aws_access_key_id='fictional', aws_secret_access_key='fictional')
        self.addCleanup(client.close)
        with Stubber(client) as stub, patch('boto3.Session') as session:
            session.return_value.client.return_value = client
            stub.add_response('converse', {'output': {'message': {'role': 'assistant', 'content': [{'text': '{"requirements":[]}'}]}},
                'stopReason': 'end_turn', 'usage': {'inputTokens': 1, 'outputTokens': 1, 'totalTokens': 2}, 'metrics': {'latencyMs': 1}},
                {'modelId': 'fictional', 'system': [{'text': ai.SYSTEM}], 'messages': [{'role': 'user', 'content': [{'text': 'fictional prompt'}]}], 'inferenceConfig': {'maxTokens': 1500}})
            self.assertEqual(ai.complete({'provider': 'bedrock', 'model': 'fictional', 'region': 'us-east-1'}, 'fictional prompt'), '{"requirements":[]}')
            options = session.return_value.client.call_args.kwargs['config']
            self.assertEqual(options.retries['total_max_attempts'], 1)
            self.assertTrue(options.ignore_configured_endpoint_urls)
            stub.assert_no_pending_responses()

