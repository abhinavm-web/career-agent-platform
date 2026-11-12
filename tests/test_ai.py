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

    def test_optional_resume_contact_is_not_sent_in_analysis_or_tailoring(self):
        raw = fake_fact_bank(); raw['resume_contact'] = 'fictional-contact@example.invalid'
        bank = FactBank.parse(raw)
        with patch('career_agent.ai.complete', return_value=json.dumps(self.output)) as complete:
            ai.analyze(self.job, bank, 'local', True)
            self.assertNotIn(raw['resume_contact'], complete.call_args.args[1])
        with patch('career_agent.ai.complete', return_value=json.dumps({'suggestions': []})) as complete:
            ai.tailoring_suggestions(self.job, bank, 'local', True)
            self.assertNotIn(raw['resume_contact'], complete.call_args.args[1])

    def test_resume_suggestions_require_consent_and_preserve_exact_source(self):
        resume = 'EXPERIENCE\nBuilt a fictional Python dashboard.\nContact: demo@example.invalid'
        output = {'suggestions': [{'quote': 'Built a fictional Python dashboard.', 'section': 'experience'}]}
        with patch('career_agent.ai.complete', return_value=json.dumps(output)) as complete:
            for route, consent in [('local', False), ('missing', True)]:
                with self.assertRaises(ValueError): ai.resume_fact_suggestions(route, resume, consent)
            complete.assert_not_called()
            result = ai.resume_fact_suggestions('local', resume, True)
        self.assertEqual(json.loads(complete.call_args.args[1]), {'resume_text': resume})
        self.assertEqual(result['suggestions'], output['suggestions'])
        self.assertIn('does not establish truth', result['notice'])
        self.assertNotIn('demo@example.invalid', json.dumps(result))
        self.assertEqual(complete.call_args.kwargs['system'], ai.RESUME_SYSTEM)

    def test_resume_suggestions_reject_invented_quotes_categories_and_oversize(self):
        resume = 'Built a fictional Python dashboard.'
        row = {'quote': resume, 'section': 'experience'}
        for bad in [row | {'quote': 'invented claim'}, row | {'section': 'citizenship'},
                    row | {'verified': True}, row | {'quote': 'short'}]:
            with self.subTest(bad=bad), patch('career_agent.ai.complete', return_value=json.dumps({'suggestions': [bad]})), self.assertRaises(ValueError):
                ai.resume_fact_suggestions('local', resume, True)
        with patch('career_agent.ai.complete', return_value=json.dumps({'suggestions': [row, row]})), self.assertRaises(ValueError):
            ai.resume_fact_suggestions('local', resume, True)
        with patch('career_agent.ai.complete') as complete, self.assertRaises(ValueError):
            ai.resume_fact_suggestions('local', 'x' * 20001, True)
        complete.assert_not_called()

    def test_tailoring_suggestions_are_cited_but_never_auto_accepted(self):
        output = {'suggestions': [{'fact_id': 'experience-01', 'job_quote': 'Python', 'wording': 'Fictional proposed wording.'}]}
        with patch('career_agent.ai.complete', return_value=json.dumps(output)) as complete:
            with self.assertRaises(ValueError):
                ai.tailoring_suggestions(self.job, self.bank, 'local', False)
            complete.assert_not_called()
            result = ai.tailoring_suggestions(self.job, self.bank, 'local', True)
        prompt = complete.call_args.args[1]
        self.assertNotIn(self.bank.name, prompt)
        self.assertNotIn(self.bank.facts[0].source_note, prompt)
        self.assertEqual(result['suggestions'][0]['original_fact'], self.bank.public_claims()['experience-01'])
        self.assertIn('do not prove', result['notice'])
        schema = complete.call_args.kwargs['json_schema']
        item = schema['properties']['suggestions']['items']['properties']
        self.assertEqual(item['fact_id']['enum'], ['experience-01'])
        self.assertEqual(item['job_quote']['enum'], [self.job.description])

    def test_tailoring_rejects_invented_citations_and_schema(self):
        row = {'fact_id': 'experience-01', 'job_quote': 'Python', 'wording': 'Suggested wording'}
        for bad in [row | {'fact_id': 'invented'}, row | {'job_quote': 'invented'},
                    row | {'wording': ''}, row | {'approved': True}]:
            with self.subTest(row=bad), patch('career_agent.ai.complete', return_value=json.dumps({'suggestions': [bad]})), self.assertRaises(ValueError):
                ai.tailoring_suggestions(self.job, self.bank, 'local', True)
        with patch('career_agent.ai.complete', return_value=json.dumps({'suggestions': [row, row]})), self.assertRaises(ValueError):
            ai.tailoring_suggestions(self.job, self.bank, 'local', True)

    def test_route_diagnostics_require_consent_and_send_only_synthetic_text(self):
        with patch('career_agent.ai.complete', return_value='OK') as complete:
            for route, consent in [('local', False), ('missing', True), ([], True)]:
                with self.assertRaises(ValueError):
                    ai.test_route(route, consent)
            complete.assert_not_called()
            result = ai.test_route('local', True)
            self.assertEqual(complete.call_args.args[1], 'Reply with OK.')
            self.assertEqual(complete.call_args.args[0]['max_tokens'], 128)
            self.assertNotIn('answer', result)
        with patch('career_agent.ai.complete', return_value=' '), self.assertRaises(ValueError):
            ai.test_route('local', True)

    def test_catalog_readiness_never_discloses_key(self):
        self.config.write_text(json.dumps({'cloud': {'provider': 'anthropic', 'model': 'fictional', 'key_env': 'FICTIONAL_TEST_KEY'}}))
        with patch.dict(os.environ, {'FICTIONAL_TEST_KEY': 'do-not-disclose'}):
            result = ai.catalog()
        self.assertEqual(result[0]['credential_status'], 'configured')
        self.assertNotIn('do-not-disclose', json.dumps(result))
        self.assertNotIn('FICTIONAL_TEST_KEY', json.dumps(result))

    def test_profile_chat_requires_consent_and_sends_only_supplied_facts(self):
        claims = self.bank.public_claims()
        with patch('career_agent.ai.complete', return_value='Ask about the role scope.') as complete:
            for route, message, consent in [('local', 'Hello', False), ('missing', 'Hello', True), ('local', '', True), ('local', 'x' * 4001, True)]:
                with self.assertRaises(ValueError):
                    ai.profile_chat(route, message, claims, consent)
            complete.assert_not_called()
            result = ai.profile_chat('local', 'What should I ask?', claims, True)
        payload = json.loads(complete.call_args.args[1])
        self.assertEqual(payload, {'question': 'What should I ask?', 'verified_facts': claims, 'history': []})
        self.assertNotIn(self.bank.name, complete.call_args.args[1])
        self.assertNotIn(self.bank.facts[0].source_note, complete.call_args.args[1])
        self.assertEqual(complete.call_args.kwargs['system'], ai.PROFILE_SYSTEM)
        self.assertEqual(result['answer'], 'Ask about the role scope.')

    def test_profile_chat_rejects_oversized_input_and_empty_output(self):
        with patch('career_agent.ai.complete') as complete, self.assertRaises(ValueError):
            ai.profile_chat('local', 'Question?', {'fact': 'x' * 32000}, True)
        complete.assert_not_called()
        for output in ('', ' ' * 2, 'x' * 32001, None):
            with self.subTest(output=str(output)[:20]), patch('career_agent.ai.complete', return_value=output), self.assertRaises(ValueError):
                ai.profile_chat('local', 'Question?', {}, True)

    def test_profile_chat_validates_and_includes_session_history(self):
        history = [{'role': 'user', 'content': 'Earlier question'}, {'role': 'assistant', 'content': 'Earlier suggestion'}]
        with patch('career_agent.ai.complete', return_value='Another suggestion') as complete:
            ai.profile_chat('local', 'Follow up?', {}, True, history)
            self.assertEqual(json.loads(complete.call_args.args[1])['history'], history)
            for bad in ([history[0]], list(reversed(history)), history * 7,
                        [{'role': 'system', 'content': 'fake'}, history[1]]):
                with self.subTest(history=bad), self.assertRaises(ValueError):
                    ai.profile_chat('local', 'Follow up?', {}, True, bad)

    def test_provenance_minimized_payload_and_no_mutations(self):
        with patch('career_agent.ai.complete', return_value=json.dumps(self.output)) as complete:
            result = ai.analyze(self.job, self.bank, 'local', True)
        prompt = complete.call_args.args[1]
        self.assertNotIn(self.bank.name, prompt)
        schema = complete.call_args.kwargs['json_schema']
        row = schema['properties']['requirements']['items']
        self.assertEqual(row['properties']['fact_ids']['items']['enum'], ['experience-01'])
        self.assertEqual(row['properties']['quote']['enum'], [self.job.description])
        self.assertEqual(set(row['required']), {'quote', 'fact_ids', 'reason'})
        self.assertFalse(row['additionalProperties'])
        self.assertIn('1 preselected posting excerpt(s)', result['notice'])
        self.assertIn('not a complete requirements list', result['notice'])
        self.assertNotIn(self.bank.facts[0].source_note, prompt)
        self.assertNotIn(self.job.url, prompt)
        self.assertEqual(result['requirements'][0]['facts'], self.bank.public_claims())
        self.assertEqual(len(result['input_sha256']), 64)
        self.assertEqual(result['requirements'][0]['status'], 'suggested_evidence_needs_review')

    def test_ollama_scope_notice_discloses_long_posting_limit(self):
        from dataclasses import replace
        description = '\n'.join('Fictional requirement ' + str(i) for i in range(105))
        job = replace(self.job, description=description)
        with patch('career_agent.ai.complete', return_value='{"requirements":[]}') as complete:
            result = ai.analyze(job, self.bank, 'local', True)
        self.assertEqual(len(complete.call_args.kwargs['json_schema']['properties']['requirements']['items']['properties']['quote']['enum']), 100)
        self.assertIn('100 preselected posting excerpt(s)', result['notice'])
        self.assertIn('lines beyond the limit', result['notice'])
        with patch('career_agent.ai.complete', return_value='{"suggestions":[]}'):
            result = ai.tailoring_suggestions(job, self.bank, 'local', True)
        self.assertIn('100 preselected posting excerpt(s)', result['notice'])

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

    def test_single_json_fence_is_accepted_but_evidence_is_still_checked(self):
        raw = '```json\n' + json.dumps(self.output) + '\n```'
        with patch('career_agent.ai.complete', return_value=raw):
            self.assertEqual(len(ai.analyze(self.job, self.bank, 'local', True)['requirements']), 1)
        self.output['requirements'][0]['quote'] = 'invented text'
        for raw in ('```json\n' + json.dumps(self.output) + '\n```', 'Here is JSON:\n' + raw):
            with patch('career_agent.ai.complete', return_value=raw), self.assertRaises(ValueError):
                ai.analyze(self.job, self.bank, 'local', True)

    def test_config_rejects_credentials_and_untrusted_endpoints(self):
        changes = [{'api_key': 'fictional-secret'}, {'provider': []}, {'max_tokens': True}, {'max_tokens': 99999},
                   {'key_env': 'not a variable'}, {'record_history': 'true'}, {'daily_request_limit': True}, {'daily_request_limit': -1}, {'daily_request_limit': 10001}, {'base_url': 'http://localhost:1234/v1'},
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
    def test_http_response_cap_rejects_oversized_or_compressed_without_echo_or_retry(self):
        import httpx
        route = {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}
        requests, reads, closed = [], [], []
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                for index in range(4):
                    reads.append(index)
                    yield b'x' * (400 * 1024)
            def close(self): closed.append(True)
        def oversized(request):
            requests.append(request)
            return httpx.Response(200, headers={'content-type': 'application/json'}, stream=Stream())
        def client(handler):
            return httpx.Client(transport=ai._bounded_transport(httpx.MockTransport(handler)), trust_env=False)
        with patch('career_agent.ai._transport', side_effect=lambda: client(oversized)):
            with self.assertRaises(ValueError) as error: ai.complete(route, 'fictional private input')
        self.assertIn('size limit', str(error.exception))
        self.assertNotIn('fictional private', str(error.exception))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].headers['accept-encoding'], 'identity')
        self.assertEqual(reads, [0, 1, 2])
        self.assertTrue(closed)
        for headers, phrase in [({'content-length': str(2 * 1024 * 1024)}, 'size limit'),
                                ({'content-encoding': 'gzip'}, 'compressed response')]:
            with self.subTest(headers=headers):
                seen = []
                def handler(request):
                    seen.append(request)
                    return httpx.Response(200, headers=headers, stream=Stream())
                with patch('career_agent.ai._transport', side_effect=lambda: client(handler)):
                    with self.assertRaises(ValueError) as error: ai.complete(route, 'fictional private input')
                self.assertIn(phrase, str(error.exception))
                self.assertEqual(len(seen), 1)
                self.assertNotIn('fictional private', str(error.exception))

    def test_oversized_anthropic_and_azure_errors_are_bounded_and_sanitized(self):
        import httpx
        routes = [{'provider': 'anthropic', 'model': 'fictional'},
                  {'provider': 'azure', 'model': 'fictional', 'base_url': 'https://fictional.openai.azure.com/openai/v1'}]
        class Stream(httpx.SyncByteStream):
            def __iter__(self): yield b'private-provider-error'
        for route in routes:
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(429, headers={'content-length': str(2 * 1024 * 1024)}, stream=Stream())
            with self.subTest(provider=route['provider']), patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fictional-key', 'AZURE_OPENAI_API_KEY': 'fictional-key'}), \
                    patch('career_agent.ai._transport', side_effect=lambda: httpx.Client(transport=ai._bounded_transport(httpx.MockTransport(handler)), trust_env=False)):
                with self.assertRaises(ValueError) as error: ai.complete(route, 'fictional private prompt')
                self.assertIn('size limit', str(error.exception))
                self.assertNotIn('private', str(error.exception))
                self.assertEqual(len(seen), 1)

    def test_bounded_transport_accepts_small_sdk_responses_across_providers(self):
        import httpx
        for provider in ('ollama', 'azure', 'anthropic'):
            requests = []
            class Stream(httpx.SyncByteStream):
                def __iter__(self):
                    value = ({'id': 'msg_fictional', 'type': 'message', 'role': 'assistant',
                        'model': 'fictional', 'content': [{'type': 'text', 'text': 'OK'}],
                        'stop_reason': 'end_turn', 'stop_sequence': None,
                        'usage': {'input_tokens': 1, 'output_tokens': 1}} if provider == 'anthropic' else
                        {'id': 'fictional', 'object': 'chat.completion', 'created': 0, 'model': 'fictional',
                         'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'OK'}}]})
                    yield json.dumps(value).encode()
            def handler(request):
                requests.append(request)
                return httpx.Response(200, headers={'content-type': 'application/json'}, stream=Stream())
            route = {'provider': provider, 'model': 'fictional'}
            if provider == 'ollama': route['base_url'] = 'http://127.0.0.1:11434/v1'
            if provider == 'azure': route['base_url'] = 'https://fictional.openai.azure.com/openai/v1'
            with self.subTest(provider=provider), patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fictional-test-key', 'AZURE_OPENAI_API_KEY': 'fictional-test-key'}), \
                    patch('career_agent.ai._transport', side_effect=lambda: httpx.Client(transport=ai._bounded_transport(httpx.MockTransport(handler)), trust_env=False)):
                self.assertEqual(ai.complete(route, 'fictional prompt'), 'OK')
                self.assertEqual(requests[0].headers['accept-encoding'], 'identity')
                self.assertEqual(len(requests), 1)

    def test_provider_reported_model_is_bounded_untrusted_metadata(self):
        import httpx
        route = {'provider': 'lmstudio', 'model': 'alias', 'base_url': 'http://127.0.0.1:1234/v1'}
        for label, expected in [('vendor/resolved-model:1', 'vendor/resolved-model:1'),
                                ('private information with spaces', None), ('x' * 201, None)]:
            def handler(request):
                return httpx.Response(200, json={'id': 'fictional', 'object': 'chat.completion', 'created': 0,
                    'model': label, 'choices': [{'index': 0, 'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': '{"requirements":[]}'}}]})
            with self.subTest(label=label), patch('career_agent.ai._transport', side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handler))):
                response = ai.complete(route, 'fictional prompt')
                self.assertEqual(response, '{"requirements":[]}')
                self.assertEqual(response.reported_model, expected)
    def test_ollama_receives_constrained_output_schema(self):
        import httpx
        original = httpx.Client
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={'id': 'fictional', 'object': 'chat.completion', 'created': 0,
                'model': 'fictional', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': '{"suggestions":[]}'}}]})
        schema = {'type': 'object', 'properties': {'suggestions': {'type': 'array'}}}
        route = {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}
        with patch('career_agent.ai._transport', side_effect=lambda: original(transport=httpx.MockTransport(handler))):
            ai.complete(route, 'fictional prompt', json_schema=schema)
        format = json.loads(requests[0].content)['response_format']
        self.assertEqual(format['type'], 'json_schema')
        self.assertEqual(format['json_schema']['schema'], schema)

    def test_incomplete_and_tool_responses_are_rejected(self):
        import httpx
        route = {'provider': 'lmstudio', 'model': 'fictional', 'base_url': 'http://127.0.0.1:1234/v1'}
        for finish in ('length', 'tool_calls', 'content_filter'):
            def handler(request):
                return httpx.Response(200, json={'id': 'fictional', 'object': 'chat.completion', 'created': 0,
                    'model': 'fictional', 'choices': [{'index': 0, 'finish_reason': finish,
                    'message': {'role': 'assistant', 'content': '{"requirements":[]}'}}]})
            with self.subTest(finish=finish), patch('career_agent.ai._transport', side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handler))), self.assertRaises(ValueError):
                ai.complete(route, 'fictional prompt')

    def test_openai_rejects_refusals_legacy_functions_and_nonassistant_messages(self):
        import httpx
        route = {'provider': 'lmstudio', 'model': 'fictional', 'base_url': 'http://127.0.0.1:1234/v1'}
        messages = [
            {'role': 'assistant', 'content': 'fictional text', 'refusal': 'private refusal'},
            {'role': 'assistant', 'content': 'fictional text', 'function_call': {'name': 'fictional', 'arguments': '{}'}},
            {'role': 'user', 'content': 'fictional text'},
            {'role': 'assistant', 'content': ' '},
            {'role': 'assistant', 'content': 'x' * 32001},
        ]
        for message in messages:
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(200, json={'id': 'fictional', 'object': 'chat.completion', 'created': 0,
                    'model': 'fictional', 'choices': [{'index': 0, 'finish_reason': 'stop', 'message': message}]})
            with self.subTest(message=message.get('role')), patch('career_agent.ai._transport', side_effect=lambda: httpx.Client(transport=ai._bounded_transport(httpx.MockTransport(handler)), trust_env=False)):
                with self.assertRaises(ValueError) as error: ai.complete(route, 'private fictional prompt')
                self.assertIn('no result was accepted', str(error.exception))
                self.assertNotIn('private', str(error.exception))
                self.assertEqual(len(seen), 1)

    def test_anthropic_rejects_mixed_text_and_tool_blocks_even_on_end_turn(self):
        import httpx
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={'id': 'msg_fictional', 'type': 'message', 'role': 'assistant',
                'model': 'fictional', 'content': [{'type': 'text', 'text': 'partial answer'},
                    {'type': 'tool_use', 'id': 'fictional', 'name': 'fictional', 'input': {'private': 'secret'}}],
                'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fictional-key'}), \
                patch('career_agent.ai._transport', side_effect=lambda: httpx.Client(transport=ai._bounded_transport(httpx.MockTransport(handler)), trust_env=False)):
            with self.assertRaises(ValueError) as error: ai.complete({'provider': 'anthropic', 'model': 'fictional'}, 'private prompt')
        self.assertIn('no result was accepted', str(error.exception))
        self.assertNotIn('private', str(error.exception))
        self.assertNotIn('partial answer', str(error.exception))
        self.assertEqual(len(seen), 1)

    def test_bedrock_rejects_nontext_or_empty_blocks_without_leaking_them(self):
        from unittest.mock import MagicMock
        route = {'provider': 'bedrock', 'model': 'fictional', 'region': 'us-east-1'}
        invalid = [
            {'role': 'assistant', 'content': [{'text': 'partial answer'}, {'toolUse': {'input': {'private': 'secret'}}}]},
            {'role': 'assistant', 'content': [{'text': 'partial answer', 'toolUse': {'input': {}}}]},
            {'role': 'user', 'content': [{'text': 'partial answer'}]},
            {'role': 'assistant', 'content': []},
            {'role': 'assistant', 'content': [{'text': None}]},
            {'role': 'assistant', 'content': [{'text': 'x' * 32001}]},
        ]
        for message in invalid:
            client = MagicMock()
            client.converse.return_value = {'output': {'message': message}, 'stopReason': 'end_turn'}
            with self.subTest(role=message['role']), patch('boto3.Session') as session:
                session.return_value.client.return_value = client
                with self.assertRaises(ValueError) as error: ai.complete(route, 'private fictional prompt')
                self.assertIn('no result was accepted', str(error.exception))
                self.assertNotIn('private', str(error.exception))
                self.assertNotIn('partial answer', str(error.exception))
                client.converse.assert_called_once()
                client.close.assert_called_once()
        client = MagicMock()
        client.converse.return_value = {'output': {'message': {'role': 'assistant', 'content': [{'text': 'O'}, {'text': 'K'}]}}, 'stopReason': 'end_turn'}
        with patch('boto3.Session') as session:
            session.return_value.client.return_value = client
            self.assertEqual(ai.complete(route, 'fictional prompt'), 'OK')

    def test_provider_error_categories_are_useful_and_never_echo_private_details(self):
        import httpx
        route = {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}
        for code, phrase in [(401, 'authentication or access'), (403, 'authentication or access'),
                             (404, 'model or endpoint'), (429, 'rate or quota'), (503, 'server error')]:
            def handler(request):
                return httpx.Response(code, json={'error': {'message': 'PRIVATE PROMPT SECRET'}})
            with self.subTest(code=code), patch('career_agent.ai._transport', side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handler))):
                with self.assertRaises(ValueError) as caught:
                    ai.complete(route, 'fictional private prompt')
                self.assertIn(phrase, str(caught.exception))
                self.assertIn('No fallback', str(caught.exception))
                self.assertNotIn('PRIVATE', str(caught.exception))
                self.assertNotIn('fictional private', str(caught.exception))
        def timeout(request):
            raise httpx.ReadTimeout('PRIVATE PROMPT SECRET', request=request)
        with patch('career_agent.ai._transport', side_effect=lambda: httpx.Client(transport=httpx.MockTransport(timeout))):
            with self.assertRaisesRegex(ValueError, 'timed out') as caught:
                ai.complete(route, 'fictional private prompt')
            self.assertNotIn('PRIVATE', str(caught.exception))
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}):
            with self.assertRaisesRegex(ValueError, 'credential is not configured'):
                ai.complete({'provider': 'anthropic', 'model': 'fictional'}, 'fictional')
        from botocore.exceptions import ClientError, NoCredentialsError
        error = ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': 'PRIVATE SECRET'},
                             'ResponseMetadata': {'HTTPStatusCode': 403}}, 'Converse')
        self.assertIn('authentication or access', ai._provider_error_message(error))
        self.assertNotIn('PRIVATE', ai._provider_error_message(error))
        self.assertIn('AWS credentials', ai._provider_error_message(NoCredentialsError()))

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

    def test_bedrock_sdk_response_with_tool_block_is_rejected(self):
        import boto3
        from botocore.stub import Stubber
        client = boto3.client('bedrock-runtime', region_name='us-east-1', aws_access_key_id='fictional', aws_secret_access_key='fictional')
        self.addCleanup(client.close)
        output = {'output': {'message': {'role': 'assistant', 'content': [
            {'text': 'partial fictional text'},
            {'toolUse': {'toolUseId': 'fictional-id', 'name': 'fictional-tool', 'input': {}}}]}},
            'stopReason': 'end_turn', 'usage': {'inputTokens': 1, 'outputTokens': 1, 'totalTokens': 2}, 'metrics': {'latencyMs': 1}}
        expected = {'modelId': 'fictional', 'system': [{'text': ai.SYSTEM}],
            'messages': [{'role': 'user', 'content': [{'text': 'fictional prompt'}]}], 'inferenceConfig': {'maxTokens': 1500}}
        with Stubber(client) as stub, patch('boto3.Session') as session:
            session.return_value.client.return_value = client
            stub.add_response('converse', output, expected)
            with self.assertRaises(ValueError) as error:
                ai.complete({'provider': 'bedrock', 'model': 'fictional', 'region': 'us-east-1'}, 'fictional prompt')
            self.assertIn('no result was accepted', str(error.exception))
            self.assertNotIn('partial fictional text', str(error.exception))
            stub.assert_no_pending_responses()

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
