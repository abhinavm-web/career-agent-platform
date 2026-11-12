"""Private, metadata-only AI request journal and conservative local count limits."""
from __future__ import annotations

import hmac

from . import ai
from .storage import Store

PURPOSES = {'route_test', 'career_chat', 'requirements', 'resume_suggestions', 'resume_fact_suggestions'}


class DailyRequestLimit(ValueError):
    """No request was made because this route exhausted its local UTC-day limit."""


class AIHistory:
    def __init__(self, store: Store):
        self.conn = store.connection
        self.conn.execute('''CREATE TABLE IF NOT EXISTS ai_request_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL, provider TEXT NOT NULL,
            model TEXT NOT NULL, purpose TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT, status TEXT NOT NULL CHECK(status IN ('running','completed','failed'))
        )''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_ai_history_route_date ON ai_request_history(route,started_at)')
        self.conn.commit()

    def reserve(self, name, route, purpose):
        if purpose not in PURPOSES:
            raise ValueError('Invalid AI request purpose')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            count = self.conn.execute("SELECT COUNT(*) FROM ai_request_history WHERE route=? AND started_at >= date('now') AND started_at < date('now','+1 day')", (name,)).fetchone()[0]
            limit = route.get('daily_request_limit')
            if limit is not None and count >= limit:
                raise DailyRequestLimit('Local daily request limit reached for this route; no provider request was made. Limit resets at 00:00 UTC.')
            row = self.conn.execute('INSERT INTO ai_request_history(route,provider,model,purpose,status) VALUES(?,?,?,?,?)',
                                    (name, route['provider'], route['model'], purpose, 'running'))
            self.conn.commit()
            return row.lastrowid
        except Exception:
            self.conn.rollback()
            raise

    def finish(self, request_id, status):
        if status not in {'completed', 'failed'}:
            raise ValueError('Invalid request status')
        with self.conn:
            self.conn.execute('UPDATE ai_request_history SET status=?,finished_at=CURRENT_TIMESTAMP WHERE id=? AND status=\'running\'', (status, request_id))

    def recent(self):
        return [dict(row) for row in self.conn.execute('SELECT id,route,provider,model,purpose,started_at,finished_at,status FROM ai_request_history ORDER BY id DESC LIMIT 100')]

    def today(self):
        return [dict(row) for row in self.conn.execute("SELECT route,COUNT(*) AS attempts FROM ai_request_history WHERE started_at >= date('now') AND started_at < date('now','+1 day') GROUP BY route ORDER BY route")]


def run_recorded(db_path, route_name, purpose, function, *args, expected_route_fingerprint=None):
    """Reserve before invocation so concurrent processes cannot exceed the local limit.

    A failure or crash consumes the reservation. No prompt, response, credential, job ID,
    endpoint or error text is written; token usage and provider charges are not estimated.
    """
    route = ai.routes().get(route_name) if isinstance(route_name, str) else None
    if route is None:
        raise ValueError('Choose a configured AI route')
    if expected_route_fingerprint is not None and (
            not isinstance(expected_route_fingerprint, str) or
            not hmac.compare_digest(expected_route_fingerprint, ai.route_fingerprint(route_name, route))):
        raise ValueError('Model destination or configuration changed; reload routes and consent again')
    if not route.get('record_history', False) and 'daily_request_limit' not in route:
        token = ai._ROUTE_SNAPSHOT.set((route_name, route))
        try:
            return function(*args)
        finally:
            ai._ROUTE_SNAPSHOT.reset(token)
    with Store(db_path) as store:
        history = AIHistory(store)
        request_id = history.reserve(route_name, route, purpose)
        token = ai._ROUTE_SNAPSHOT.set((route_name, route))
        try:
            result = function(*args)
        except BaseException:
            history.finish(request_id, 'failed')
            raise
        else:
            history.finish(request_id, 'completed')
            return result
        finally:
            ai._ROUTE_SNAPSHOT.reset(token)
