"""Offline tests for the beginner path: parsing, honest documents, endpoints and downloads."""
from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from career_agent import ai
from career_agent.models import Job
from career_agent.simple_apply import check_ai_letter, guess_job, parse_resume
from career_agent.storage import Store

HAS_WEB = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
RESUME = """Alex Fictional
alex@example.invalid | 555-123-4567
EXPERIENCE
• Led a fictional budget process for a team of four.
• Built a fictional AI tool in Python for the front desk.
Education
B.S. Fictional Studies, Example University
Skills:
Python, customer service, AWS
"""


class ParseTests(unittest.TestCase):
    def test_headed_resume_gives_name_contact_and_sectioned_lines(self):
        result = parse_resume(RESUME)
        self.assertEqual(result['name'], 'Alex Fictional')
        self.assertIn('alex@example.invalid', result['contact'])
        self.assertEqual([(l['section'], l['text']) for l in result['lines']], [
            ('experience', 'Led a fictional budget process for a team of four.'),
            ('experience', 'Built a fictional AI tool in Python for the front desk.'),
            ('education', 'B.S. Fictional Studies, Example University'),
            ('skills', 'Python, customer service, AWS')])

    def test_plain_resume_without_headings_still_yields_lines(self):
        result = parse_resume('Sam Plain\nI cleaned fictional offices for two years.\nI know customer service well.')
        self.assertEqual(result['name'], 'Sam Plain')
        self.assertEqual(len(result['lines']), 2)

    def test_contact_lines_and_short_fragments_are_never_offered_as_facts(self):
        lines = parse_resume('Sam\nsam@example.invalid\nExperience\nok\nhttps://example.invalid/sam\n')['lines']
        self.assertEqual(lines, [])

    def test_guess_job_prefers_what_the_user_typed(self):
        text = '# Senior Baker at Sunrise Bakery\nMake bread.'
        self.assertEqual(guess_job(text)[:2], ('Senior Baker at Sunrise Bakery', 'Sunrise Bakery'))
        self.assertEqual(guess_job(text, title='Baker', company='My Shop')[:2], ('Baker', 'My Shop'))
        self.assertEqual(guess_job('Cook\nCook food.')[1], 'Company not listed')

    def test_ai_letter_cannot_add_numbers_or_placeholders(self):
        job = Job('Example Clinic', 'Front Desk', 'Town', 'https://example.invalid/j', 'Needs 2 years of customer service.')
        claims = {'a': 'Led a team of four for 3 years.'}
        self.assertEqual(len(check_ai_letter('Dear Hiring Team,\n\nI led a team of four for 3 years.\n\nSincerely,\nAlex', claims, job)), 3)
        with self.assertRaisesRegex(ValueError, 'number'):
            check_ai_letter('I increased sales by 40%.', claims, job)
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            check_ai_letter('Dear [Hiring Manager],', claims, job)
        with self.assertRaises(ValueError):
            check_ai_letter('   ', claims, job)


@unittest.skipUnless(HAS_WEB, 'Install test dependencies with pip install -e ".[web,dev]"')
class SimpleApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from career_agent.dashboard import DashboardConfig, create_app
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = root / 'career.sqlite3'
        self.job = Job.from_mapping({'company': 'Example Clinic', 'title': 'Front Desk AI Assistant', 'location': 'Knoxville, TN',
            'job_url': 'https://example.invalid/jobs/front-desk',
            'description': 'Requirements\n- Experience with Python\n- Strong customer service skills\n- Budget awareness\n'})
        with Store(self.db) as store:
            store.add_jobs([self.job])
        self.client = TestClient(create_app(DashboardConfig(db_path=self.db, packet_dir=root / 'packets')), base_url='http://localhost')
        landing = self.client.get('/')
        self.assertEqual(landing.status_code, 200)
        self.session = re.search(r'data-session="([^"]+)"', landing.text).group(1)
        self.landing = landing

    def call(self, method, path, payload=None):
        kwargs = {'json': payload} if payload is not None else {}
        return self.client.request(method, path, headers={'X-Session-Token': self.session}, **kwargs)

    def save_resume(self, confirmed=None, **extra):
        state = self.call('GET', '/api/simple/state').json()
        parsed = parse_resume(RESUME)
        body = {'name': 'Alex Fictional', 'contact': parsed['contact'], 'filename': 'resume.txt', 'text': RESUME,
                'confirmed': confirmed if confirmed is not None else parsed['lines'], 'acknowledged': True,
                'workspace_revision': state['workspace_revision'], 'resume_revision': state['resume_revision'], **extra}
        return self.call('POST', '/api/simple/resume', body)

    def test_root_is_the_simple_app_and_classic_is_still_available(self):
        self.assertIn('/assets/simple.js', self.landing.text)
        self.assertNotIn('Opportunity board', self.landing.text)
        classic = self.client.get('/classic')
        self.assertEqual(classic.status_code, 200)
        self.assertIn('Opportunity board', classic.text)
        self.assertIn('href="/"', classic.text)  # way back to the simple view
        self.assertEqual(self.call('GET', '/assets/simple.js' if False else '/api/simple/state').status_code, 200)

    def test_new_user_state_then_saving_confirmed_lines_only(self):
        self.assertFalse(self.call('GET', '/api/simple/state').json()['resume_saved'])
        parsed = parse_resume(RESUME)
        skipped = parsed['lines'][2]  # the candidate answers "No" to the education line
        saved = self.save_resume([l for l in parsed['lines'] if l is not skipped])
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['fact_count'], 3)
        state = self.call('GET', '/api/simple/state').json()
        self.assertTrue(state['resume_saved'])
        self.assertEqual(state['name'], 'Alex Fictional')
        statements = [f['statement'] for f in self.call('GET', '/api/workspace').json()['workspace']['facts']]
        self.assertNotIn(skipped['text'], statements)       # a skipped line never becomes a fact
        self.assertEqual(len(statements), 3)

    def test_save_requires_acknowledgement_name_and_a_confirmed_line(self):
        self.assertEqual(self.save_resume(acknowledged=False).status_code, 422)
        self.assertEqual(self.save_resume(name='  ').status_code, 422)
        self.assertEqual(self.save_resume(confirmed=[]).status_code, 422)
        self.assertEqual(self.save_resume(confirmed=[{'text': 'Invented fictional award winner.', 'section': 'made-up'}]).status_code, 422)

    def test_resaving_the_same_resume_does_not_duplicate_facts(self):
        self.assertEqual(self.save_resume().status_code, 200)
        again = self.save_resume()
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['fact_count'], 4)

    def test_stale_revisions_are_rejected_not_overwritten(self):
        self.assertEqual(self.save_resume().status_code, 200)
        self.assertEqual(self.save_resume(workspace_revision=0, resume_revision=0).status_code, 409)

    def test_job_page_needs_a_resume_first(self):
        self.assertEqual(self.call('GET', f'/api/jobs/{self.job.id}/simple').status_code, 412)

    def test_job_page_shows_original_recommended_and_cover_letter_from_true_lines(self):
        self.save_resume()
        view = self.call('GET', f'/api/jobs/{self.job.id}/simple').json()
        self.assertEqual(view['job']['title'], 'Front Desk AI Assistant')
        self.assertTrue(any(row['kind'] == 'heading' and row['text'] == 'EXPERIENCE' for row in view['original']))
        bullets = [b['text'] for s in view['resume']['sections'] for b in s['bullets']]
        self.assertIn('Built a fictional AI tool in Python for the front desk.', bullets)
        self.assertFalse(any(b['new'] for s in view['resume']['sections'] for b in s['bullets']))  # nothing added yet
        letter = [p['text'] for p in view['cover_letter']['paragraphs']]
        self.assertIn('Dear Hiring Team,', letter)
        self.assertTrue(any('Front Desk AI Assistant' in t and 'Example Clinic' in t for t in letter))
        self.assertEqual(view['cover_letter']['written_by'], 'template')
        for text in (p['text'] for p in view['cover_letter']['paragraphs'] if p['type'] == 'bullet'):
            self.assertIn(text, bullets)  # the letter only quotes confirmed lines
        self.assertTrue(any('Budget' in ask for ask in view['job_asks']))

    def test_adding_something_true_marks_it_new_and_needs_confirmation(self):
        self.save_resume()
        state = self.call('GET', '/api/simple/state').json()
        body = {'text': 'Managed a fictional school office budget for two years.', 'job_id': self.job.id, 'workspace_revision': state['workspace_revision'], 'acknowledged': False}
        self.assertEqual(self.call('POST', '/api/simple/add-line', body).status_code, 422)
        self.assertEqual(self.call('POST', '/api/simple/add-line', {**body, 'acknowledged': True, 'text': 'short'}).status_code, 422)
        self.assertEqual(self.call('POST', '/api/simple/add-line', {**body, 'acknowledged': True}).status_code, 200)
        view = self.call('GET', f'/api/jobs/{self.job.id}/simple').json()
        added = [b for s in view['resume']['sections'] for b in s['bullets'] if b['new']]
        self.assertEqual([b['text'] for b in added], ['Managed a fictional school office budget for two years.'])
        self.assertEqual(view['added_count'], 1)

    def test_plain_english_lines_match_the_job_and_unrelated_ones_stay_out(self):
        self.save_resume()
        state = self.call('GET', '/api/simple/state').json()
        for text, job_id in (('I cooked fictional lasagna for my family every Sunday.', self.job.id),):
            self.call('POST', '/api/simple/add-line', {'text': text, 'job_id': job_id, 'workspace_revision': state['workspace_revision'], 'acknowledged': True})
        other = Job.from_mapping({'company': 'Other Co', 'title': 'Baker', 'job_url': 'https://example.invalid/baker',
                                  'description': 'Requirements\n- Baking and kitchen safety\n'})
        with Store(self.db) as store:
            store.add_jobs([other])
        texts = [b['text'] for s in self.call('GET', f'/api/jobs/{other.id}/simple').json()['resume']['sections'] for b in s['bullets']]
        self.assertNotIn('I cooked fictional lasagna for my family every Sunday.', texts)  # added on another job, no shared words
        original = [b['text'] for s in self.call('GET', f'/api/jobs/{self.job.id}/simple').json()['resume']['sections'] for b in s['bullets']]
        self.assertIn('Python, customer service, AWS', original)

    def test_jobs_list_and_search(self):
        jobs = self.call('GET', '/api/simple/jobs').json()['jobs']
        self.assertEqual([j['title'] for j in jobs], ['Front Desk AI Assistant'])
        self.assertEqual(self.call('GET', '/api/simple/jobs?q=zzz').json()['jobs'], [])
        self.assertEqual(len(self.call('GET', '/api/simple/jobs?q=clinic').json()['jobs']), 1)

    def test_search_words_are_remembered_and_validated(self):
        self.assertEqual(self.call('GET', '/api/simple/state').json()['search'], {'what': '', 'where': ''})
        self.assertEqual(self.call('PUT', '/api/simple/search', {'what': '', 'where': 'Town'}).status_code, 422)
        self.assertEqual(self.call('PUT', '/api/simple/search', {'what': 'baker', 'where': 'x' * 300}).status_code, 422)
        saved = self.call('PUT', '/api/simple/search', {'what': '  customer service ', 'where': 'Knoxville, TN'})
        self.assertEqual(saved.json()['search'], {'what': 'customer service', 'where': 'Knoxville, TN'})
        self.assertEqual(self.call('GET', '/api/simple/state').json()['search']['where'], 'Knoxville, TN')

    def test_pasted_job_guesses_title_and_company_and_hides_the_placeholder_link(self):
        self.save_resume()
        posting = 'Library Helper\nCompany: Example Library\nRequirements\n- Helping people find books\n- Organizing shelves'
        bad = self.call('POST', '/api/simple/paste', {'text': 'too short', 'title': '', 'company': '', 'url': ''})
        self.assertEqual(bad.status_code, 422)
        bad_link = self.call('POST', '/api/simple/paste', {'text': posting, 'title': '', 'company': '', 'url': 'not a link'})
        self.assertEqual(bad_link.status_code, 422)
        made = self.call('POST', '/api/simple/paste', {'text': posting, 'title': '', 'company': '', 'url': ''})
        self.assertEqual(made.status_code, 200)
        view = self.call('GET', f'/api/jobs/{made.json()["job_id"]}/simple').json()
        self.assertEqual((view['job']['title'], view['job']['company']), ('Library Helper', 'Example Library'))
        self.assertEqual(view['job']['url'], '')
        typed = self.call('POST', '/api/simple/paste', {'text': posting + ' more', 'title': 'Shelver', 'company': 'City Library', 'url': 'https://example.invalid/shelver'})
        view = self.call('GET', f'/api/jobs/{typed.json()["job_id"]}/simple').json()
        self.assertEqual((view['job']['title'], view['job']['company'], view['job']['url']), ('Shelver', 'City Library', 'https://example.invalid/shelver'))

    def test_word_downloads_are_real_documents(self):
        self.save_resume()
        resume = self.call('GET', f'/api/jobs/{self.job.id}/simple/resume.docx')
        self.assertEqual(resume.status_code, 200)
        self.assertTrue(resume.content.startswith(b'PK'))
        rows = [{'type': 'text', 'text': 'Dear Hiring Team,'}, {'type': 'bullet', 'text': 'A true line.'}]
        letter = self.call('POST', f'/api/jobs/{self.job.id}/simple/cover-letter.docx', {'paragraphs': rows})
        self.assertEqual(letter.status_code, 200)
        self.assertTrue(letter.content.startswith(b'PK'))
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/simple/cover-letter.docx', {'paragraphs': []}).status_code, 422)
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/simple/cover-letter.docx',
                                   {'paragraphs': [{'type': 'html', 'text': 'x'}]}).status_code, 422)

    def test_ai_letter_is_consented_checked_and_never_changes_the_resume(self):
        self.save_resume()
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        url = f'/api/jobs/{self.job.id}/simple/ai-cover-letter'
        fingerprint = ai.route_fingerprint('local', route['local'])
        request = {'route': 'local', 'route_fingerprint': fingerprint, 'acknowledged': True}
        before = self.call('GET', '/api/workspace').json()
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete') as complete:
            self.assertTrue(self.call('GET', '/api/simple/state').json()['ai_available'])
            self.assertEqual(self.call('POST', url, {**request, 'acknowledged': False}).status_code, 422)
            complete.assert_not_called()
            complete.return_value = 'Dear Hiring Team,\n\nI built an AI tool in Python for the front desk.\n\nSincerely,\nAlex Fictional'
            good = self.call('POST', url, request)
            self.assertEqual(good.status_code, 200)
            self.assertEqual(good.json()['cover_letter']['written_by'], 'ai')
            prompt = json.loads(complete.call_args.args[1])
            self.assertEqual(len(prompt['candidate_facts']), 4)
            complete.return_value = 'Dear Hiring Team,\n\nI raised sales by 250%.\n\nSincerely,\nAlex'
            bad = self.call('POST', url, request)
            self.assertEqual(bad.status_code, 422)
            self.assertIn('number', bad.json()['detail'])
        self.assertEqual(self.call('GET', '/api/workspace').json(), before)


if __name__ == '__main__':
    unittest.main()
