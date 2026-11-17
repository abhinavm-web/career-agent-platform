import copy
import json
import unittest
from unittest.mock import patch

from career_agent.claim_fidelity import audit_document
from career_agent.tailoring_checks import wording_review_warnings


FACTS = {
    'tool': 'Skills: FictionalGraph and FictionalSearch.',
    'project': 'Built a production knowledge assistant for Fictional Industries.',
    'team': 'Managed six direct reports in the delivery organization.',
    'dates': 'Delivery Lead, Fictional Industries, January 2023 to February 2025.',
}
DOCUMENT = {
    'resume': [
        {'text': 'Delivered a production knowledge assistant for Fictional Industries.', 'fact_ids': ['project'], 'section': 'experience'},
        {'text': 'Built the knowledge assistant using FictionalSearch.', 'fact_ids': ['project', 'tool'], 'section': 'experience'},
        {'text': 'Led a six-person product-management team.', 'fact_ids': ['team'], 'section': 'experience'},
    ],
    'cover_letter': [{'text': 'I bring hands-on production expertise with FictionalGraph.', 'fact_ids': ['tool']}],
}
ROUTE = {'provider': 'anthropic', 'model': 'fictional-model', 'max_tokens': 8192}


def supported_response(route, prompt, **kwargs):
    return json.dumps({'passages': [{'passage_id': row['passage_id'], 'status': 'supported',
                                    'reason': 'The cited fictional statement supports this passage.'}
                                   for row in json.loads(prompt)['passages']]})


class ClaimFidelityTests(unittest.TestCase):
    def test_abridged_book_citation_does_not_warn_about_omitted_synopsis_negation(self):
        source = 'Author, Fictional Systems: A Guide (2024): Explores why capability alone does not create adoption.'
        wording = 'Author, Fictional Systems: A Guide (2024).'
        self.assertFalse(any('Negation' in x for x in wording_review_warnings(source, wording)))
        career = source + '\nDid not manage the implementation team.'
        changed = wording + ' Managed the implementation team.'
        self.assertTrue(any('Negation' in x for x in wording_review_warnings(career, changed)))
        synopsis_change = 'Author, Fictional Systems: A Guide (2024): Explores why capability alone creates adoption.'
        self.assertTrue(any('Negation' in x for x in wording_review_warnings(source, synopsis_change)))
        implicit_change = 'Author, Fictional Systems: A Guide (2024): capability alone creates adoption.'
        self.assertTrue(any('Negation' in x for x in wording_review_warnings(source, implicit_change)))
        separate_change = 'Author, Fictional Systems: A Guide (2024); capability alone creates adoption.'
        self.assertTrue(any('Negation' in x for x in wording_review_warnings(source, separate_change)))

    def test_fidelity_outcomes_cover_both_documents_without_mutation(self):
        before = copy.deepcopy(DOCUMENT)
        rows = [
            {'passage_id': 'resume:0', 'status': 'supported', 'reason': 'Exact supported accomplishment.'},
            {'passage_id': 'resume:1', 'status': 'unsupported', 'reason': 'The tool is not linked to this project.'},
            {'passage_id': 'resume:2', 'status': 'unsupported', 'reason': 'Direct reports do not establish total team size or product-management scope.'},
            {'passage_id': 'cover_letter:0', 'status': 'uncertain', 'reason': 'A listed skill does not establish hands-on production depth.'},
        ]
        with patch('career_agent.ai.complete', return_value=json.dumps({'passages': list(reversed(rows))})) as complete:
            result = audit_document(DOCUMENT, FACTS, ROUTE)
        self.assertTrue(result['complete'])
        self.assertEqual([r['status'] for r in result['passages']], ['supported', 'unsupported', 'unsupported', 'uncertain'])
        self.assertEqual(result['passages'][-1]['kind'], 'cover_letter')
        self.assertEqual(result['passages'][-1]['index'], 0)
        self.assertEqual(DOCUMENT, before)
        payload = json.loads(complete.call_args.args[1])
        self.assertEqual(payload['verified_facts'], FACTS)
        self.assertEqual(complete.call_args.args[0], ROUTE)

    def test_all_passages_are_batched_and_not_silently_sampled(self):
        document = {'resume': [copy.deepcopy(DOCUMENT['resume'][0]) for _ in range(20)],
                    'cover_letter': [copy.deepcopy(DOCUMENT['cover_letter'][0]) for _ in range(6)]}
        with patch('career_agent.ai.complete', side_effect=supported_response) as complete:
            result = audit_document(document, FACTS, ROUTE)
        self.assertEqual(len(result['passages']), 26)
        ids = [(r['kind'], r['index']) for r in result['passages']]
        self.assertEqual(len(set(ids)), 26)
        self.assertEqual(complete.call_count, 4)
        for call in complete.call_args_list:
            self.assertLessEqual(len(json.loads(call.args[1])['passages']), 8)

    def test_exact_single_fact_repairs_need_no_model_request(self):
        document = {'resume': [{'text': FACTS['project'], 'fact_ids': ['project'], 'section': 'projects'} for _ in range(60)],
                    'cover_letter': [{'text': FACTS['team'], 'fact_ids': ['team']} for _ in range(12)]}
        with patch('career_agent.ai.complete') as complete:
            result = audit_document(document, FACTS, ROUTE)
        complete.assert_not_called()
        self.assertEqual(len(result['passages']), 72)
        self.assertTrue(all(r['status'] == 'supported' for r in result['passages']))

    def test_unscoped_experience_is_audited_in_complete_document_context(self):
        document = {'resume': [
            {'text': FACTS['dates'], 'fact_ids': ['dates'], 'section': 'experience'},
            {'text': FACTS['project'], 'fact_ids': ['project'], 'section': 'experience'},
            {'text': FACTS['team'], 'fact_ids': ['team'], 'section': 'projects'},
        ], 'cover_letter': [{'text': FACTS['team'], 'fact_ids': ['team']}]}
        with patch('career_agent.ai.complete', side_effect=supported_response) as complete:
            result = audit_document(document, FACTS, ROUTE)
        self.assertEqual(complete.call_count, 1)
        prompt = json.loads(complete.call_args.args[1])
        self.assertEqual([r['passage_id'] for r in prompt['passages']], ['resume:1'])
        self.assertEqual(prompt['context_document']['resume'][0]['text'], FACTS['dates'])
        self.assertEqual(len(prompt['context_document']['resume']), 3)
        self.assertEqual(len(result['passages']), 4)

    def test_safe_diagnostics_identify_batch_without_echoing_model_text(self):
        document = {'resume': [copy.deepcopy(DOCUMENT['resume'][0]) for _ in range(9)],
                    'cover_letter': DOCUMENT['cover_letter']}
        calls = 0
        def response(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1: return supported_response(*args, **kwargs)
            rows = json.loads(supported_response(*args, **kwargs))
            rows['passages'][0]['passage_id'] = 'PRIVATE_MODEL_TEXT'
            return json.dumps(rows)
        with patch('career_agent.ai.complete', side_effect=response):
            with self.assertRaises(ValueError) as caught:
                audit_document(document, FACTS, ROUTE)
        self.assertIn('batch 2/2; unknown_passage_id', str(caught.exception))
        self.assertNotIn('PRIVATE_MODEL_TEXT', str(caught.exception))

    def test_callback_records_each_actual_model_batch(self):
        document = {'resume': [copy.deepcopy(DOCUMENT['resume'][0]) for _ in range(9)],
                    'cover_letter': [{'text': FACTS['team'], 'fact_ids': ['team']}]}
        recorded = []
        def complete(*args, **kwargs):
            recorded.append(json.loads(args[1])['passages'])
            return supported_response(*args, **kwargs)
        with patch('career_agent.ai.complete') as default:
            result = audit_document(document, FACTS, ROUTE, complete=complete)
        default.assert_not_called()
        self.assertEqual([len(batch) for batch in recorded], [8, 1])
        self.assertEqual(len(result['passages']), 10)

    def test_missing_duplicate_foreign_or_malformed_results_fail_closed(self):
        valid = json.loads(supported_response(ROUTE, json.dumps({'passages': [
            {'passage_id': f'resume:{i}'} for i in range(3)] + [{'passage_id': 'cover_letter:0'}]})))
        cases = []
        missing = copy.deepcopy(valid); missing['passages'].pop(); cases.append(missing)
        duplicate = copy.deepcopy(valid); duplicate['passages'][-1] = duplicate['passages'][0]; cases.append(duplicate)
        foreign = copy.deepcopy(valid); foreign['passages'][0]['passage_id'] = 'resume:99'; cases.append(foreign)
        status = copy.deepcopy(valid); status['passages'][0]['status'] = 'verified'; cases.append(status)
        reason = copy.deepcopy(valid); reason['passages'][0]['reason'] = ' '; cases.append(reason)
        oversized = copy.deepcopy(valid); oversized['passages'][0]['reason'] = 'x' * 801; cases.append(oversized)
        extra = copy.deepcopy(valid); extra['passages'][0]['new_claim'] = 'Invented'; cases.append(extra)
        malformed = copy.deepcopy(valid); malformed['passages'][0]['status'] = []; cases.append(malformed)
        cases += [{'passages': []}, {'complete': True}, []]
        for case in cases:
            with self.subTest(case=case), patch('career_agent.ai.complete', return_value=json.dumps(case)):
                with self.assertRaisesRegex(ValueError, 'no validation accepted'):
                    audit_document(DOCUMENT, FACTS, ROUTE)

    def test_later_batch_failure_cannot_return_partial_complete_audit(self):
        document = {'resume': [copy.deepcopy(DOCUMENT['resume'][0]) for _ in range(9)],
                    'cover_letter': DOCUMENT['cover_letter']}
        calls = 0
        def response(*args, **kwargs):
            nonlocal calls
            calls += 1
            return supported_response(*args, **kwargs) if calls == 1 else '{"passages": []}'
        with patch('career_agent.ai.complete', side_effect=response):
            with self.assertRaisesRegex(ValueError, 'no validation accepted'):
                audit_document(document, FACTS, ROUTE)
        self.assertEqual(calls, 2)

    def test_invalid_inputs_do_not_transmit(self):
        missing = copy.deepcopy(DOCUMENT); missing['resume'][0]['fact_ids'] = ['not-verified']
        duplicate = copy.deepcopy(DOCUMENT); duplicate['resume'][0]['fact_ids'] *= 2
        text = copy.deepcopy(DOCUMENT); text['resume'][0]['text'] = 'x' * 2001
        empty = {'resume': [], 'cover_letter': DOCUMENT['cover_letter']}
        for document in [missing, duplicate, text, empty, {}, None]:
            with self.subTest(document=document), patch('career_agent.ai.complete') as complete:
                with self.assertRaises(ValueError): audit_document(document, FACTS, ROUTE)
                complete.assert_not_called()
        with patch('career_agent.ai.complete') as complete:
            with self.assertRaisesRegex(ValueError, '48,000'):
                audit_document(DOCUMENT, {**FACTS, 'large': 'x' * 48000}, ROUTE)
            complete.assert_not_called()

    def test_fenced_json_is_allowed_but_surrounding_prose_is_not(self):
        raw = supported_response(ROUTE, json.dumps({'passages': [
            {'passage_id': f'resume:{i}'} for i in range(3)] + [{'passage_id': 'cover_letter:0'}]}))
        with patch('career_agent.ai.complete', return_value='```json\n' + raw + '\n```'):
            self.assertTrue(audit_document(DOCUMENT, FACTS, ROUTE)['complete'])
        with patch('career_agent.ai.complete', return_value='Here is the answer: ' + raw):
            with self.assertRaises(ValueError): audit_document(DOCUMENT, FACTS, ROUTE)


if __name__ == '__main__':
    unittest.main()
