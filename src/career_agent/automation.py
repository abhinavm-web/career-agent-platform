"""Persisted, opt-in discovery schedules with bounded workers and graph checkpoints."""
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from threading import Event, Thread
from typing import TypedDict

from .models import Job
from .storage import Store


class DiscoveryState(TypedDict, total=False):
    settings: dict
    jobs: list[dict]
    counts: dict


class DiscoveryTimedOut(ValueError):
    """The isolated discovery process exceeded its deadline."""


class DiscoveryStopped(ValueError):
    """The server stopped while discovery was in progress."""


def fetch(settings, stop):
    """Kill the child on shutdown or after two minutes, including slow reads."""
    with subprocess.Popen([sys.executable, '-m', 'career_agent.discovery_worker'], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as child:
        started, payload = time.monotonic(), json.dumps(settings)
        while True:
            if stop.is_set():
                child.kill(); child.communicate()
                raise DiscoveryStopped('Discovery stopped; no results imported')
            if time.monotonic() - started > 120:
                child.kill(); child.communicate()
                raise DiscoveryTimedOut('Discovery timed out; no results imported')
            try:
                output, _ = child.communicate(payload, timeout=1)
                break
            except subprocess.TimeoutExpired:
                payload = None
        if child.returncode or len(output) > 10 * 1024 * 1024:
            raise ValueError('JobSpy failed; check installation or source availability')
        rows = json.loads(output)
        if not isinstance(rows, list) or len(rows) > 300:
            raise ValueError('Invalid discovery response')
        return rows


class Automation:
    def __init__(self, db):
        self.db = Path(db)
        self.stop = Event()
        self.thread = None
        with Store(db) as store:
            store.connection.executescript('''
                CREATE TABLE IF NOT EXISTS discovery_schedules(
                    id TEXT PRIMARY KEY, settings TEXT NOT NULL, enabled INTEGER NOT NULL,
                    interval_minutes INTEGER NOT NULL, next_run REAL NOT NULL,
                    failures INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS discovery_runs(
                    id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, status TEXT NOT NULL,
                    started REAL NOT NULL, finished REAL, found INTEGER, added INTEGER, message TEXT);
                CREATE TABLE IF NOT EXISTS discovery_lease(id INTEGER PRIMARY KEY CHECK(id=1), token TEXT, expires REAL NOT NULL);
                INSERT OR IGNORE INTO discovery_lease(id,expires) VALUES(1,0);
                CREATE TABLE IF NOT EXISTS discovery_imports(run_id TEXT PRIMARY KEY, counts TEXT NOT NULL);
            ''')

    def list(self):
        with Store(self.db) as store:
            schedules = [dict(r) for r in store.connection.execute('SELECT * FROM discovery_schedules ORDER BY rowid')]
            for row in schedules:
                row['settings'] = json.loads(row['settings'])
            runs = [dict(r) for r in store.connection.execute('SELECT * FROM discovery_runs ORDER BY started DESC LIMIT 50')]
        return {'schedules': schedules, 'runs': runs, 'worker': 'runs while this local server is running'}

    def save(self, data):
        if not isinstance(data, dict) or data.get('acknowledged') is not True:
            raise ValueError('Confirm recurring searches to the selected job boards')
        for key in ('query', 'location'):
            if not isinstance(data.get(key), str) or not 1 <= len(data[key].strip()) <= 160:
                raise ValueError('Enter a search query and location (1–160 characters)')
        sites = data.get('sites')
        if not isinstance(sites, list) or not sites or not all(isinstance(s, str) and s in {'indeed', 'linkedin', 'zip_recruiter'} for s in sites) or len(set(sites)) != len(sites):
            raise ValueError('Choose supported, distinct job boards')
        interval, results = data.get('interval_minutes', 360), data.get('results', 20)
        if type(interval) is not int or not 60 <= interval <= 10080 or type(results) is not int or not 1 <= results <= 50:
            raise ValueError('Use 60–10080 minutes and 1–50 results per source')
        settings = json.dumps({k: data[k] for k in ('query', 'location', 'sites')} | {'results': results}, sort_keys=True)
        with Store(self.db) as store, store.connection:
            store.connection.execute('BEGIN IMMEDIATE')
            if store.connection.execute('SELECT count(*) FROM discovery_schedules').fetchone()[0] >= 5:
                raise ValueError('At most five saved searches are supported')
            if store.connection.execute('SELECT 1 FROM discovery_schedules WHERE settings=?', (settings,)).fetchone():
                raise ValueError('This search is already saved')
            sid = uuid.uuid4().hex
            store.connection.execute('INSERT INTO discovery_schedules(id,settings,enabled,interval_minutes,next_run) VALUES(?,?,1,?,?)',
                                     (sid, settings, interval, time.time()))
        return {'id': sid}

    def enable(self, sid, enabled):
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        with Store(self.db) as store, store.connection:
            changed = store.connection.execute('UPDATE discovery_schedules SET enabled=? WHERE id=?', (enabled, sid))
            if changed.rowcount != 1:
                raise ValueError('Unknown search')

    def remove(self, sid):
        with Store(self.db) as store, store.connection:
            if store.connection.execute('DELETE FROM discovery_schedules WHERE id=? AND enabled=0', (sid,)).rowcount != 1:
                raise ValueError('Pause this search before removing it')

    def tick(self, now=None):
        now = time.time() if now is None else now
        token = uuid.uuid4().hex
        with Store(self.db) as store, store.connection:
            store.connection.execute('BEGIN IMMEDIATE')
            lease = store.connection.execute('SELECT expires FROM discovery_lease WHERE id=1').fetchone()
            if lease['expires'] > now:
                return False
            for pending in store.connection.execute("SELECT r.id,i.counts FROM discovery_runs r JOIN discovery_imports i ON i.run_id=r.id WHERE r.status='running'").fetchall():
                counts = json.loads(pending['counts'])
                store.connection.execute("UPDATE discovery_runs SET status='completed',finished=?,found=?,added=?,message='Committed import recovered after worker interruption' WHERE id=?",
                                         (now, counts['found'], counts['added'], pending['id']))
            row = store.connection.execute("SELECT * FROM discovery_schedules WHERE enabled=1 AND (next_run<=? OR id IN (SELECT schedule_id FROM discovery_runs WHERE status='running')) ORDER BY next_run LIMIT 1", (now,)).fetchone()
            if not row:
                return False
            # A dead process loses its lease. Mark old work accurately, then start a new run.
            store.connection.execute("UPDATE discovery_runs SET status='interrupted',finished=?,message='Worker stopped before completion; next scheduled run retries discovery' WHERE status='running'", (now,))
            store.connection.execute('UPDATE discovery_lease SET token=?,expires=? WHERE id=1', (token, now + 300))
            store.connection.execute('INSERT INTO discovery_runs(id,schedule_id,status,started) VALUES(?,?,?,?)', (token, row['id'], 'running', now))
            store.connection.execute('UPDATE discovery_schedules SET next_run=? WHERE id=?', (now + row['interval_minutes'] * 60, row['id']))
        try:
            counts = self._graph(token, row)
            status = 'completed' if counts['found'] else 'inconclusive'
            message = 'Imported returned jobs; source coverage may be partial' if counts['found'] else 'No usable rows returned; this may be an upstream failure, not zero vacancies'
        except Exception:
            counts, status, message = {'found': 0, 'added': 0}, 'failed', 'Discovery failed, paused or timed out; existing jobs retained'
            with Store(self.db) as store:
                receipt = store.connection.execute('SELECT counts FROM discovery_imports WHERE run_id=?', (token,)).fetchone()
            if receipt:
                counts, status, message = json.loads(receipt['counts']), 'completed', 'Jobs imported; graph finalization failed after the committed import'
        with Store(self.db) as store, store.connection:
            store.connection.execute('BEGIN IMMEDIATE')
            if store.connection.execute('SELECT token FROM discovery_lease WHERE id=1').fetchone()['token'] != token:
                return False
            store.connection.execute('UPDATE discovery_runs SET status=?,finished=?,found=?,added=?,message=? WHERE id=?',
                                     (status, time.time(), counts['found'], counts['added'], message, token))
            failures = row['failures'] + 1 if status != 'completed' else 0
            delay = max(row['interval_minutes'] * 60, min(86400, 3600 * 2 ** min(failures, 5))) if failures else row['interval_minutes'] * 60
            store.connection.execute('UPDATE discovery_schedules SET failures=?,next_run=? WHERE id=?', (failures, time.time() + delay, row['id']))
            # Shared cooldown also spaces different searches using the same providers.
            store.connection.execute('UPDATE discovery_lease SET expires=? WHERE id=1', (time.time() + 60,))
        return True

    def _graph(self, run_id, schedule):
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.graph import START, END, StateGraph
        from langsmith import tracing_context
        from .application import _reject_symlink_components
        checkpoint_path = self.db.with_suffix('.discovery.sqlite3')
        _reject_symlink_components(checkpoint_path)
        def discover(state):
            return {'jobs': fetch(state['settings'], self.stop)}
        def normalize(state):
            normalized = {j.id: j for j in (Job.from_mapping(r) for r in state['jobs'])}
            from dataclasses import asdict
            return {'jobs': [asdict(j) for j in normalized.values()]}
        def ingest(state):
            with Store(self.db) as store, store.connection:
                store.connection.execute('BEGIN IMMEDIATE')
                existing = store.connection.execute('SELECT counts FROM discovery_imports WHERE run_id=?', (run_id,)).fetchone()
                if existing:
                    return {'counts': json.loads(existing['counts'])}
                enabled = store.connection.execute('SELECT enabled FROM discovery_schedules WHERE id=?', (schedule['id'],)).fetchone()
                lease = store.connection.execute('SELECT token,expires FROM discovery_lease WHERE id=1').fetchone()
                if not enabled or not enabled['enabled'] or lease['token'] != run_id or lease['expires'] <= time.time() or self.stop.is_set():
                    raise ValueError('Discovery was paused or lease expired')
                added = 0
                for raw in state['jobs']:
                    j = Job.from_mapping(raw)
                    added += store.connection.execute('INSERT OR IGNORE INTO jobs(job_id,company,title,location,url,description,source,remote) VALUES(?,?,?,?,?,?,?,?)',
                        (j.id,j.company,j.title,j.location,j.url,j.description,j.source,int(j.remote))).rowcount
                counts = {'found': len(state['jobs']), 'added': added}
                store.connection.execute('INSERT INTO discovery_imports VALUES(?,?)', (run_id,json.dumps(counts)))
                return {'counts': counts}
        builder = StateGraph(DiscoveryState)
        for name, fn in [('discover', discover), ('normalize', normalize), ('ingest', ingest)]:
            builder.add_node(name, fn)
        builder.add_edge(START, 'discover'); builder.add_edge('discover', 'normalize')
        builder.add_edge('normalize', 'ingest'); builder.add_edge('ingest', END)
        with tracing_context(enabled=False), SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            graph = builder.compile(checkpointer=saver)
            return graph.invoke({'settings': json.loads(schedule['settings'])}, {'configurable': {'thread_id': run_id}}, durability='sync')['counts']

    def start(self):
        def loop():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception:
                    pass  # Next tick retries; provider failures have persisted run records.
                self.stop.wait(15)
        self.thread = Thread(target=loop, daemon=True, name='career-discovery')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
