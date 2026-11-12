"""Metadata-only AI request history, count limits and concurrent reservations."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from career_agent import ai
from career_agent.ai_history import AIHistory, DailyRequestLimit, run_recorded
from career_agent.storage import Store


class AIHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'fictional.sqlite3'
        self.route = {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1', 'daily_request_limit': 2}

    def test_records_outcome_without_private_content_and_caps_failures(self):
        routes = {'local': self.route}
        with patch('career_agent.ai.routes', return_value=routes):
            self.assertEqual(run_recorded(self.db, 'local', 'career_chat', lambda: 'private answer'), 'private answer')
            def fail(): raise ValueError('private prompt, private error and fictional secret')
            with self.assertRaises(ValueError): run_recorded(self.db, 'local', 'requirements', fail)
            with self.assertRaises(DailyRequestLimit): run_recorded(self.db, 'local', 'requirements', lambda: self.fail('No call should run'))
        with Store(self.db) as store:
            history = AIHistory(store)
            rows = history.recent()
            self.assertEqual([row['status'] for row in rows], ['failed', 'completed'])
            self.assertEqual(history.today(), [{'route': 'local', 'attempts': 2}])
            self.assertNotIn('private', json.dumps(rows))
            store.connection.execute("UPDATE ai_request_history SET started_at='2000-01-01 00:00:00'")
            store.connection.commit()
        with patch('career_agent.ai.routes', return_value=routes):
            self.assertEqual(run_recorded(self.db, 'local', 'route_test', lambda: 'ok'), 'ok')

    def test_atomic_reservation_across_connections(self):
        routes = {'local': self.route | {'daily_request_limit': 1}}
        with Store(self.db) as store: AIHistory(store)
        gate = threading.Barrier(4)
        def attempt(_):
            gate.wait()
            try:
                return run_recorded(self.db, 'local', 'route_test', lambda: 'allowed')
            except DailyRequestLimit:
                return 'blocked'
        with patch('career_agent.ai.routes', return_value=routes), ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(attempt, range(4)))
        self.assertEqual(outcomes.count('allowed'), 1)
        self.assertEqual(outcomes.count('blocked'), 3)

    def test_snapshot_used_for_model_even_if_config_changes(self):
        routes = {'local': self.route}
        with patch('career_agent.ai.routes', side_effect=[routes, {}]):
            value = run_recorded(self.db, 'local', 'route_test', lambda: ai._route('local'))
        self.assertEqual(value, self.route)

    def test_unknown_route_invalid_purpose_and_zero_limit_do_not_invoke(self):
        with patch('career_agent.ai.routes', return_value={'local': self.route | {'daily_request_limit': 0}}):
            for name, purpose in [('missing', 'route_test'), ('local', 'unknown'), ('local', 'route_test')]:
                with self.assertRaises(ValueError): run_recorded(self.db, name, purpose, lambda: self.fail('No call'))
        with Store(self.db) as store:
            self.assertEqual(AIHistory(store).recent(), [])

    def test_cli_shares_cap_with_workspace_and_requires_consent(self):
        from career_agent.cli import main
        from test_dashboard import fake_job, fake_fact_bank
        import contextlib
        import io
        job = fake_job()
        with Store(self.db) as store: store.add_jobs([job])
        facts = Path(self.temp.name) / 'fictional.json'
        facts.write_text(json.dumps(fake_fact_bank()), encoding='utf-8')
        args = ['--db', str(self.db), 'ai-analyze', job.id, '--facts', str(facts), '--route', 'local']
        route = {'local': self.route | {'daily_request_limit': 1}}
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value='{"requirements":[]}') as complete, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 1)
            complete.assert_not_called()
            self.assertEqual(main(args + ['--acknowledge-data-sharing']), 0)
            self.assertEqual(main(args + ['--acknowledge-data-sharing']), 1)
            self.assertEqual(complete.call_count, 1)
        with Store(self.db) as store:
            self.assertEqual(AIHistory(store).today(), [{'route': 'local', 'attempts': 1}])

    def test_logging_is_opt_in_or_enabled_by_limit(self):
        uncapped = {k: v for k, v in self.route.items() if k != 'daily_request_limit'}
        with patch('career_agent.ai.routes', return_value={'local': uncapped}):
            self.assertEqual(run_recorded(self.db, 'local', 'route_test', lambda: 'ok'), 'ok')
        self.assertFalse(self.db.exists())
        with patch('career_agent.ai.routes', return_value={'local': uncapped | {'record_history': True}}):
            self.assertEqual(run_recorded(self.db, 'local', 'route_test', lambda: 'ok'), 'ok')
        with Store(self.db) as store:
            self.assertEqual(AIHistory(store).today(), [{'route': 'local', 'attempts': 1}])
