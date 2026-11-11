"""Persistent LangGraph application runs; only local, replay-safe graph nodes."""
import hashlib
import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path

from .application import _reject_symlink_components, save_packet
from .graph_review import build_career_graph
from .storage import Store
from .models import Job


def inputs(job, profile, bank, records, revision=None, job_description_revision=None):
    state = {'job': asdict(job), 'preferences': asdict(profile),
            'fact_bank': {'name': bank.name, 'headline': bank.headline, 'facts': [
                {'id': f.fact_id, 'section': f.section, 'statement': f.statement,
                 'keywords': list(f.keywords), 'source_note': f.source_note,
                 'verified_by_candidate': True} for f in bank.facts]},
            'filing_records': [asdict(r) for r in records], 'workspace_revision': revision}
    if job_description_revision:
        state['job_description_revision'] = job_description_revision
    return state


def signature(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


class Workflows:
    def __init__(self, db, packet_dir):
        self.db, self.packet_dir = Path(db), Path(packet_dir)
        self.checkpoints = str(self.db.with_suffix('.workflows.sqlite3'))
        _reject_symlink_components(Path(self.checkpoints))
        with Store(db) as store:
            store.connection.execute('''CREATE TABLE IF NOT EXISTS workflow_runs(
                run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, input_json TEXT NOT NULL,
                input_sha256 TEXT NOT NULL, status TEXT NOT NULL, packet_sha256 TEXT,
                error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            store.connection.commit()

    def list(self):
        with Store(self.db) as store:
            return [dict(r) for r in store.connection.execute('''SELECT run_id,job_id,status,
                packet_sha256,error,created_at FROM workflow_runs ORDER BY rowid DESC LIMIT 100''')]

    def start(self, state, *, guard_workspace=False):
        run_id = uuid.uuid4().hex
        with Store(self.db) as store, store.connection:
            store.connection.execute('INSERT INTO workflow_runs(run_id,job_id,input_json,input_sha256,status) VALUES(?,?,?,?,?)',
                (run_id, Job.from_mapping(state['job']).id,
                 json.dumps(state), signature(state), 'queued'))
        return self.advance(run_id, signature(state), expected_workspace_revision=(state.get('workspace_revision') or 0) if guard_workspace else None)

    def advance(self, run_id, current_signature, decision=None, *, expected_workspace_revision=None):
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            from langgraph.types import Command
            from langsmith import tracing_context
        except ImportError:
            raise ValueError('Install durable workflows with pip install -e ".[graph]"') from None
        with Store(self.db) as store:
            # Serialize cross-process graph advancement and packet publication.
            store.connection.execute('BEGIN IMMEDIATE')
            row = store.connection.execute('SELECT * FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown workflow run')
            latest = store.connection.execute('SELECT run_id FROM workflow_runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1', (row['job_id'],)).fetchone()
            if latest['run_id'] != run_id:
                raise ValueError('This run was superseded by a newer draft')
            if row['input_sha256'] != current_signature:
                raise ValueError('Workflow inputs changed; create a new draft and review it')
            if json.loads(row['input_json']).get('job_description_revision', 0) != store.description_revision(row['job_id']):
                raise ValueError('Job description changed; create a new draft and review it')
            if expected_workspace_revision is not None and expected_workspace_revision != store.workspace_revision():
                raise ValueError('Career profile changed; create a new draft and review it')
            if store.stage_of(row['job_id']) == 'submitted':
                raise ValueError('Submitted application is locked')
            config = {'configurable': {'thread_id': run_id}}
            try:
                with tracing_context(enabled=False), SqliteSaver.from_conn_string(self.checkpoints) as saver:
                    graph = build_career_graph(saver)
                    snapshot = graph.get_state(config)
                    if decision is not None:
                        packet = snapshot.values.get('packet')
                        if not packet or packet['packet_sha256'] != decision['packet_sha256']:
                            raise ValueError('Stale workflow packet')
                        expected = 'approved_for_manual_submission' if decision['approved'] else 'rejected_by_reviewer'
                        if snapshot.values.get('review_status') != expected:
                            # A deliberate changed decision starts another review interrupt.
                            if not snapshot.next:
                                graph.update_state(config, {'review_status': ''}, as_node='validate')
                                graph.invoke(None, config, durability='sync')
                            result = graph.invoke(Command(resume=decision), config, durability='sync')
                        else:
                            result = snapshot.values
                    elif snapshot.values and not snapshot.next:
                        result = snapshot.values
                    else:
                        result = graph.invoke(None if snapshot.values else json.loads(row['input_json']), config, durability='sync')
                packet = result['packet']
                target = self.packet_dir / (row['job_id'] + '.json')
                temporary = self.packet_dir / (run_id + '.pending.json')
                _reject_symlink_components(target)
                save_packet(packet, temporary)
                os.replace(temporary, target)
                status = result.get('review_status') or 'awaiting_human_review'
                store.connection.execute('UPDATE workflow_runs SET status=?,packet_sha256=?,error=NULL WHERE run_id=?',
                                         (status, packet['packet_sha256'], run_id))
                if status in {'approved_for_manual_submission', 'rejected_by_reviewer'}:
                    latest = store.latest_review(row['job_id'], packet['packet_sha256'])
                    if not latest or latest['decision'] != status:
                        store.connection.execute('INSERT INTO packet_reviews(job_id,packet_sha256,decision) VALUES(?,?,?)',
                                                 (row['job_id'], packet['packet_sha256'], status))
                store.connection.commit()
                return {'run_id': run_id, 'packet': packet, 'status': status, 'submission_sent': False}
            except Exception:
                store.connection.rollback()
                with store.connection:
                    store.connection.execute("UPDATE workflow_runs SET status='failed',error='Workflow failed; retry after checking inputs and dependencies' WHERE run_id=?", (run_id,))
                raise

    def review(self, job_id, fingerprint, approved, get_state):
        with Store(self.db) as store:
            row = store.connection.execute('SELECT run_id,packet_sha256 FROM workflow_runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1',
                                           (job_id,)).fetchone()
        if row is None:
            return False  # Legacy CLI packets retain their existing review path.
        if row['packet_sha256'] != fingerprint:
            raise ValueError('Workflow publication is incomplete or superseded; resume the latest run or regenerate')
        state = get_state()
        self.advance(row['run_id'], signature(state), {'packet_sha256': fingerprint, 'approved': approved, 'reviewed': True},
                     expected_workspace_revision=state.get('workspace_revision') or 0)
        return True
