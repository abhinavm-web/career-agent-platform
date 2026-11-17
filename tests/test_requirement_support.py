"""Fictional cases for independent positive-requirement review, no provider calls."""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from career_agent import requirement_support as audit, resume_validation as validation
from career_agent.document_revisions import require_validation
from test_resume_validation import completion, document, snapshot
from test_document_revisions import ROUTE


def requirement(number, status='supported'):
    return {'id': f'r{number}', 'quote': 'Governance including regulatory compliance and model risk management.',
            'status': status, 'reason': 'Governance is shown; regulatory compliance and formal model risk are absent.',
            'resume_indices': [0], 'profile_fact_ids': ['profile-only'], 'category': 'required'}


def answer(route, prompt, **kwargs):
    payload = json.loads(prompt)
    return json.dumps({'requirements': [{'id': r['id'], 'status': 'partial', 'resume_indices': [0],
        'reason': 'General governance does not establish the regulatory compliance or model risk clauses.'}
        for r in payload['requirements']]})


class RequirementSupportTests(unittest.TestCase):
    def test_independent_batches_preserve_order_and_do_not_send_absent_profile_facts(self):
        rows = [requirement(i) for i in range(18)]
        rows[4]['status'] = 'partial'
        original = deepcopy(rows)
        calls = []
        def complete(route, prompt, **kwargs):
            payload = json.loads(prompt)
            calls.append(payload)
            self.assertEqual(payload['purpose'], 'requirement_support_audit')
            self.assertEqual(payload['allowed_resume_indices'], [0, 1])
            self.assertEqual([r['resume_index'] for r in payload['resume']], [0, 1])
            self.assertNotIn('verified_facts', payload)
            self.assertNotIn('profile-only', prompt)
            self.assertIn('Independently audit', kwargs['system'])
            return answer(route, prompt, **kwargs)
        result = audit.audit_support(rows, document()['document'], ROUTE, complete)
        self.assertEqual(len(calls), 3)
        self.assertEqual([r['id'] for r in result], [r['id'] for r in rows])
        self.assertEqual(result[4], original[4])
        self.assertTrue(all(r['status'] == 'partial' for r in result))
        self.assertEqual(rows, original)
        seen = [r['id'] for call in calls for r in call['requirements']]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertNotIn('r4', seen)

    def test_no_positive_rows_make_no_calls(self):
        def forbidden(*args, **kwargs): raise AssertionError('Unexpected model call')
        rows = [requirement(1, 'not_demonstrated')]
        self.assertEqual(audit.audit_support(rows, document()['document'], ROUTE, forbidden), rows)

    def test_at_least_one_example_list_gets_independent_review(self):
        row = requirement(1, 'partial')
        row['quote'] = 'Proficiency with at least one framework such as FictionalGraph, FictionalAgents, FictionalCrew.'
        with patch('career_agent.requirement_support.ai.complete', side_effect=answer) as model:
            audit.audit_support([row], document()['document'], ROUTE)
        model.assert_called_once()
        self.assertEqual(json.loads(model.call_args.args[1])['requirements'][0]['id'], 'r1')

    def test_skill_alternatives_do_not_disable_mandatory_duration_bounds(self):
        for quote in [
            '15 years of experience, including 4 years using Java or Python.',
            '15 years of experience AND 4 years of leadership, with Python OR Java.',
            '15 years experience with Java OR Python including 4 years of leadership.',
            '15 years of experience AND 4 years using Java OR 3 years using Python.',
        ]:
            with self.subTest(quote=quote):
                self.assertTrue(validation._duration_exceeds_evidence(quote, 120))
        for quote in [
            '12 years experience with a bachelors OR 8 years with a masters.',
            '12 years experience OR a masters degree with 8 years experience.',
        ]:
            with self.subTest(quote=quote):
                self.assertFalse(validation._duration_exceeds_evidence(quote, 120))
                self.assertTrue(validation._duration_exceeds_evidence(quote, 84))

    def test_three_compound_or_profile_only_false_positives_are_corrected(self):
        rows = [requirement(1), requirement(2), requirement(3)]
        rows[1].update(quote='Use coding assistants for code generation, debugging and refactoring.',
                       reason='Tool adoption is shown; debugging and refactoring are not detailed.')
        rows[2].update(quote='Familiarity with Agile delivery.',
                       reason='Agile is only in the profile and absent from the actual resume.')
        def challenge(route, prompt, **kwargs):
            payload = json.loads(prompt)
            return json.dumps({'requirements': [{'id': r['id'],
                'status': 'not_demonstrated' if r['id'] == 'r3' else 'partial',
                'resume_indices': [] if r['id'] == 'r3' else [0],
                'reason': 'The actual resume omits material clauses; profile-only facts do not qualify.'}
                for r in payload['requirements']]})
        result = audit.audit_support(rows, document()['document'], ROUTE, challenge)
        self.assertEqual([r['status'] for r in result], ['partial', 'partial', 'not_demonstrated'])

    def test_malformed_results_fail_closed_and_do_not_leak_provider_text(self):
        cases = [('id', 'PRIVATE_RESPONSE', 'unknown_requirement_id'),
                 ('status', 'PRIVATE_RESPONSE', 'invalid_status'),
                 ('resume_indices', [True], 'invalid_resume_indices'),
                 ('resume_indices', [999], 'resume_index_out_of_range'),
                 ('resume_indices', [0, 0], 'duplicate_resume_indices'),
                 ('reason', 'PRIVATE_RESPONSE' * 100, 'reason_too_long'),
                 ('reason', '', 'invalid_reason')]
        for field, value, code in cases:
            def bad(route, prompt, **kwargs):
                result = json.loads(answer(route, prompt))
                result['requirements'][0][field] = value
                return json.dumps(result)
            with self.subTest(field=field, code=code), self.assertRaises(ValueError) as caught:
                audit.audit_support([requirement(1)], document()['document'], ROUTE, bad)
            self.assertIn(code, str(caught.exception))
            self.assertNotIn('PRIVATE_RESPONSE', str(caught.exception))

    def test_missing_duplicate_extra_fields_and_supported_without_indices_fail(self):
        for mode in ['missing', 'duplicate', 'extra', 'empty_support']:
            def bad(route, prompt, **kwargs):
                result = json.loads(answer(route, prompt))
                if mode == 'missing': result['requirements'].pop()
                if mode == 'duplicate': result['requirements'][1] = result['requirements'][0]
                if mode == 'extra': result['requirements'][0]['profile_fact_ids'] = []
                if mode == 'empty_support': result['requirements'][0].update(status='supported', resume_indices=[])
                return json.dumps(result)
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'no validation accepted'):
                audit.audit_support([requirement(1), requirement(2)], document()['document'], ROUTE, bad)

    def test_all_prompt_limits_are_checked_before_any_call(self):
        rows = [requirement(i) for i in range(9)]
        rows[-1]['quote'] = 'x' * 100000
        with patch('career_agent.requirement_support.ai.complete') as model:
            with self.assertRaisesRegex(ValueError, 'no partial audit accepted'):
                audit.audit_support(rows, document()['document'], ROUTE)
            model.assert_not_called()

    def test_validation_integrates_audit(self):
        def complete(route, prompt, **kwargs):
            payload = json.loads(prompt)
            if payload.get('purpose') == 'requirement_support_audit':
                return answer(route, prompt, **kwargs)
            return completion(route, prompt, **kwargs)
        with patch('career_agent.ai.routes', return_value={'test': ROUTE}):
            result = validation.validate(snapshot('Requirements:\n- Governance and compliance'), document(), 'test', complete=complete)
        self.assertEqual(result['version'], 2)
        self.assertEqual(result['support_audit'], {'complete': True, 'checked': 1})
        self.assertEqual(result['requirements'][0]['status'], 'partial')

    def test_alternatives_get_independent_review_and_can_correct_false_negatives(self):
        rows = [requirement(1, 'partial'), requirement(2, 'not_demonstrated'), requirement(3, 'partial')]
        for row, quote in zip(rows, ['Java or Python.', 'RAG or agent workflows.',
                                    'Evaluation or fine-tuning or in-context learning.']):
            row['quote'] = quote
        rows.append(requirement(4, 'context'))
        rows[-1]['quote'] = 'Our office or remote organization.'
        def complete(route, prompt, **kwargs):
            payload = json.loads(prompt)
            self.assertEqual([r['id'] for r in payload['requirements']], ['r1', 'r2', 'r3'])
            self.assertIn('Preserve AND versus OR', kwargs['system'])
            return json.dumps({'requirements': [{'id': r['id'], 'status': 'supported',
                'resume_indices': [0], 'reason': 'One genuine alternative is explicitly established.'}
                for r in payload['requirements']]})
        result = audit.audit_support(rows, document()['document'], ROUTE, complete)
        self.assertEqual([r['status'] for r in result], ['supported', 'supported', 'supported', 'context'])

    def test_auditor_cannot_restore_support_using_unsafe_claims_or_impossible_tenure(self):
        s = snapshot('Requirements:\n- Python or Java\n- 15 years experience with Python or Java')
        claims = {'complete': True, 'passages': [{'kind': 'resume', 'index': 0,
            'status': 'unsupported', 'reason': 'Fictional unsafe claim.'},
            {'kind': 'resume', 'index': 1, 'status': 'supported', 'reason': 'Fictional dates.'},
            {'kind': 'cover_letter', 'index': 0, 'status': 'supported', 'reason': 'Fictional letter.'}]}
        with patch('career_agent.ai.routes', return_value={'test': ROUTE}), patch(
                'career_agent.resume_validation.audit_document', return_value=claims):
            result = validation.validate(s, document(), 'test', complete=completion)
        self.assertEqual(result['support_audit']['checked'], 2)
        self.assertTrue(all(r['status'] == 'partial' for r in result['requirements']))
        # A separate safe-link response must still not restore impossible duration.
        def safe_link(route, prompt, **kwargs):
            result = json.loads(completion(route, prompt, **kwargs))
            if json.loads(prompt).get('purpose') == 'requirement_support_audit':
                for row in result['requirements']: row['resume_indices'] = [1]
            return json.dumps(result)
        with patch('career_agent.ai.routes', return_value={'test': ROUTE}):
            result = validation.validate(s, document(), 'test', complete=safe_link)
        self.assertEqual(result['requirements'][1]['status'], 'partial')

    def test_current_version_still_requires_completed_support_audit_for_export(self):
        revision = document()
        revision['validation'] = {'version': validation.VERSION, 'status': 'completed', 'complete': True,
            'export_allowed': True, 'document_sha256': validation.document_digest(revision['document'])}
        with self.assertRaisesRegex(ValueError, 'Validate the current resume'):
            require_validation(revision)
        revision['validation']['support_audit'] = {'complete': True, 'checked': 0}
        require_validation(revision)
        revision['validation']['version'] = 1
        with self.assertRaises(ValueError): require_validation(revision)

    def test_degree_duration_alternatives_are_not_replaced_by_largest_number(self):
        s = snapshot('Requirements:\n- 12 years of experience with a bachelors OR 8 years of experience with a masters.')
        s['claims']['degree'] = 'Masters in Computer Science, Example University, 2014.'
        d = document()
        d['document']['resume'].append({'text': s['claims']['degree'], 'fact_ids': ['degree'], 'section': 'education'})
        def complete(route, prompt, **kwargs):
            response = json.loads(completion(route, prompt, **kwargs))
            if json.loads(prompt).get('purpose') == 'requirement_support_audit':
                for row in response['requirements']:
                    row.update(status='supported', resume_indices=[1, 2],
                               reason='Ten dated years and the explicit masters degree satisfy the eight-year alternative.')
            return json.dumps(response)
        with patch('career_agent.ai.routes', return_value={'test': ROUTE}):
            result = validation.validate(s, d, 'test', complete=complete)
        self.assertEqual(result['requirements'][0]['status'], 'supported')
        self.assertEqual(result['support_audit']['checked'], 1)
        self.assertTrue(validation._duration_exceeds_evidence('15 years experience with Java OR Python', 120))
        self.assertTrue(validation._duration_exceeds_evidence('15 years experience including 4 years AI and 3 years leadership', 120))
