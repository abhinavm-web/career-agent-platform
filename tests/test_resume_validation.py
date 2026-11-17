"""Fictional full-posting checks, missing evidence and safe persisted repair."""
import json
import unittest
from datetime import date
from unittest.mock import patch

import test_dashboard
from test_document_revisions import OUTPUT, ROUTE
from career_agent import ai, resume_validation as validation
from career_agent.storage import Store


def snapshot(description='Required qualifications:\n- Python\n- 15+ years of experience'):
    return {'description': description, 'claims': {'fact-1': 'Used Python for a fictional data service.',
            'role-1': 'Example Labs, Engineer, January 2015 to January 2025.'},
            'claim_sections': {'fact-1': 'experience', 'role-1': 'experience'}}


def document():
    return {'document': {'resume': [{'text': 'Used Python for a fictional data service.', 'fact_ids': ['fact-1'], 'section': 'projects'},
                                    {'text': 'Example Labs, Engineer, January 2015 to January 2025.', 'fact_ids': ['role-1'], 'section': 'experience'}],
                         'cover_letter': [{'text': 'Used Python for a fictional data service.', 'fact_ids': ['fact-1']}]}}


def completion(route, prompt, **kwargs):
    payload = json.loads(prompt)
    return json.dumps({'requirements': [{'id': r['id'], 'status': 'supported',
        'resume_indices': [0, 1], 'profile_fact_ids': [], 'reason': 'Fictional model evidence judgment.'}
        for r in payload['requirements']]})


class ResumeValidationTests(unittest.TestCase):
    def test_dated_tenure_unions_overlaps_and_ignores_future_undated_work(self):
        result = validation.dated_tenure(['January 2015 to January 2020', 'Jan 2018 - Jan 2025',
            'January 2024 to present', 'January 2030 to January 2040', 'Undated earlier work'], date(2026, 1, 1))
        self.assertEqual(result['dated_months'], 132)
        self.assertEqual(result['approximate_years'], 11)

    def test_all_requirements_batched_exactly_once_and_compound_tenure_not_hidden(self):
        desc = 'Requirements:\n' + '\n'.join(f'- Skill {n}' for n in range(23)) + '\n- 15 years experience including 4 years AI and 3 years leading teams'
        seen = []
        def complete(*args, **kwargs):
            seen.extend(r['id'] for r in json.loads(args[1])['requirements'])
            return completion(*args, **kwargs)
        with patch('career_agent.ai.routes', return_value={'test': ROUTE}):
            result = validation.validate(snapshot(desc), document(), 'test', complete=complete)
        self.assertEqual(len(result['requirements']), 24)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(result['requirements'][-1]['status'], 'partial')
        self.assertIn('10.0 years', result['requirements'][-1]['reason'])
        self.assertTrue(result['export_allowed'])  # A truthful stretch application may be reviewed.

    def test_omitted_duplicate_or_unknown_ids_fail_the_whole_validation(self):
        for mode in ['missing', 'duplicate', 'unknown', 'false_index']:
            def bad(route, prompt, **kwargs):
                rows = json.loads(completion(route, prompt))['requirements']
                if mode == 'missing': rows.pop()
                if mode == 'duplicate': rows[1] = rows[0]
                if mode == 'unknown': rows[0]['id'] = 'invented'
                if mode == 'false_index': rows[0]['resume_indices'] = [True]
                return json.dumps({'requirements': rows})
            with self.subTest(mode=mode), patch('career_agent.ai.routes', return_value={'test': ROUTE}):
                with self.assertRaisesRegex(ValueError, 'incomplete or invalid'):
                    validation.validate(snapshot(), document(), 'test', complete=bad)

    def test_requirement_failure_diagnostics_are_precise_and_private(self):
        cases = [
            ('id', 'PRIVATE_PROVIDER_TEXT', 'unknown_requirement_id'),
            ('status', 'PRIVATE_PROVIDER_TEXT', 'invalid_status'),
            ('resume_indices', [True], 'invalid_resume_index'),
            ('resume_indices', [0, 0], 'duplicate_resume_indices'),
            ('resume_indices', [], 'supported_without_resume_evidence'),
            ('profile_fact_ids', ['PRIVATE_PROVIDER_TEXT'], 'unknown_fact_id'),
            ('profile_fact_ids', ['fact-1', 'fact-1'], 'duplicate_fact_ids'),
            ('reason', 'PRIVATE_PROVIDER_TEXT' * 61, 'reason_too_long'),
        ]
        for field, value, code in cases:
            def bad(route, prompt, **kwargs):
                rows = json.loads(completion(route, prompt))['requirements']
                rows[0][field] = value
                return json.dumps({'requirements': rows})
            with self.subTest(field=field, code=code), patch('career_agent.ai.routes', return_value={'test': ROUTE}):
                with self.assertRaises(ValueError) as caught:
                    validation.validate(snapshot(), document(), 'test', complete=bad)
            message = str(caught.exception)
            self.assertIn('batch 1/1; ' + code, message)
            self.assertNotIn('PRIVATE_PROVIDER_TEXT', message)

    def test_later_requirement_batch_count_failure_reports_only_counts(self):
        desc = 'Requirements:\n' + '\n'.join(f'- Fictional skill {n}' for n in range(21))
        calls = 0
        def bad(route, prompt, **kwargs):
            nonlocal calls
            calls += 1
            response = json.loads(completion(route, prompt))
            if calls == 3: response['requirements'].clear()
            return json.dumps(response)
        with patch('career_agent.ai.routes', return_value={'test': ROUTE}):
            with self.assertRaisesRegex(ValueError, 'batch 3/3; requirement_count expected=1 actual=0'):
                validation.validate(snapshot(desc), document(), 'test', complete=bad)

    def test_explicit_qualification_cannot_be_reclassified_as_context(self):
        def context(route, prompt, **kwargs):
            rows = json.loads(completion(route, prompt))['requirements']
            for row in rows: row['status'] = 'context'
            return json.dumps({'requirements': rows})
        with patch('career_agent.ai.routes', return_value={'test': ROUTE}):
            result = validation.validate(snapshot('Basic Qualifications:\n- 15 years experience'), document(), 'test', complete=context)
        self.assertEqual(result['requirements'][0]['status'], 'needs_confirmation')

    def test_education_intervals_do_not_inflate_employment_tenure(self):
        s=snapshot('Required qualifications:\n- 12 years of experience')
        s['claims']['degree']='Example University, September 2008 to May 2012.'
        d=document();d['document']['resume'].append({'text':s['claims']['degree'],'fact_ids':['degree'],'section':'education'})
        with patch('career_agent.ai.routes',return_value={'test':ROUTE}):
            result=validation.validate(s,d,'test',complete=completion)
        self.assertEqual(result['tenure']['approximate_years'],10)
        self.assertEqual(result['requirements'][0]['status'],'partial')

    def test_claim_failures_block_review_and_linked_requirement_support(self):
        claims = {'complete': True, 'passages': [{'kind':'resume','index':0,'status':'unsupported','reason':'Invented project association.'},
                    {'kind':'resume','index':1,'status':'supported','reason':'Exact dates.'},
                    {'kind':'cover_letter','index':0,'status':'supported','reason':'Exact source.'}]}
        with patch('career_agent.ai.routes', return_value={'test': ROUTE}), patch('career_agent.resume_validation.audit_document', return_value=claims):
            result = validation.validate(snapshot(), document(), 'test', complete=completion)
        self.assertFalse(result['export_allowed'])
        self.assertEqual(result['requirements'][0]['status'], 'partial')

    def test_repair_standalone_sources_omitted_evidence_and_idempotent_bounds(self):
        s = snapshot(); s['claims']['extra'] = 'Led testing for a fictional regulated lender.'
        d = document(); d['id'] = 4
        d['document']['resume'][0]['text'] = 'Invented hands-on skill and project link.'
        d['validation'] = {'status':'completed','complete':True,'document_sha256':validation.document_digest(d['document']),
            'claims':{'passages':[{'kind':'resume','index':0,'status':'unsupported'}]},
            'requirements':[{'status':'partial','profile_fact_ids':['extra']}]}
        repaired = validation.repair(s, d)
        self.assertEqual(repaired['document']['resume'][0]['text'], s['claims']['fact-1'])
        self.assertEqual(repaired['document']['resume'][0]['section'], 'projects')
        self.assertEqual(repaired['document']['resume'][-1]['text'], s['claims']['extra'])
        self.assertNotIn('validation', repaired)
        self.assertNotIn('id', repaired)
        self.assertEqual(d['document']['resume'][0]['text'], 'Invented hands-on skill and project link.')


class ValidationAPITests(unittest.TestCase):
    setUp = test_dashboard.DashboardTests.setUp
    call = test_dashboard.DashboardTests.call

    def create(self):
        path = '/api/jobs/' + self.job.id + '/document-revisions'
        body = {'route':'test','route_fingerprint':ai.route_fingerprint('test',ROUTE),'acknowledged':True}
        with patch('career_agent.ai.routes', return_value={'test':ROUTE}), patch('career_agent.ai.complete', return_value=json.dumps(OUTPUT)):
            result = self.call('POST', path, {**body,'parent_id':0,'instructions':'','fresh':True})
        self.assertEqual(result.status_code,200,result.text)
        return path+'/'+str(result.json()['revisions'][0]['id']), body

    def test_validation_required_failure_persisted_and_stale_response_rejected(self):
        path, body = self.create()
        self.assertEqual(self.call('POST',path+'/review',{'acknowledged':True}).status_code,422)
        with patch('career_agent.ai.routes', return_value={'test':ROUTE}), patch('career_agent.resume_validation.validate',side_effect=ValueError('Incomplete model result')):
            response = self.call('POST',path+'/validate',body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['revisions'][0]['validation']['status'],'failed')
        self.assertEqual(self.call('POST',path+'/review',{'acknowledged':True}).status_code,422)
        def changed(*args, **kwargs):
            with Store(self.db) as store:store.correct_description(self.job.id,'Changed requirements.',0,'Fictional source')
            return {'status':'completed','complete':True,'export_allowed':True}
        with patch('career_agent.ai.routes', return_value={'test':ROUTE}), patch('career_agent.resume_validation.validate',side_effect=changed):
            response = self.call('POST',path+'/validate',body)
        self.assertEqual(response.status_code,422,response.text)

    def test_consent_route_changes_and_per_call_daily_limits(self):
        path, body = self.create()
        route = {**ROUTE,'record_history':True,'daily_request_limit':1}
        with patch('career_agent.ai.routes',return_value={'test':route}), patch('career_agent.ai.complete') as model:
            self.assertEqual(self.call('POST',path+'/validate',{**body,'acknowledged':False}).status_code,422)
            response=self.call('POST',path+'/validate',{**body,'route_fingerprint':'0'*64})
            self.assertEqual(response.json()['revisions'][0]['validation']['status'],'failed')
            model.assert_not_called()
        def batches(snap, rev, name, *, complete):
            complete(route,'fictional first request',system='fictional')
            complete(route,'fictional second request',system='fictional')
        body['route_fingerprint']=ai.route_fingerprint('test',route)
        with patch('career_agent.ai.routes',return_value={'test':route}), patch('career_agent.resume_validation.validate',side_effect=batches), patch('career_agent.ai.complete',return_value='{}') as model:
            response=self.call('POST',path+'/validate',body)
            self.assertEqual(response.json()['revisions'][0]['validation']['status'],'failed')
            self.assertEqual(model.call_count,1)
        with Store(self.db) as store:
            self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM ai_request_history').fetchone()[0],1)
