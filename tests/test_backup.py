"""Offline archive tests use fictional records and temporary workspaces only."""
import json
import hashlib
import importlib.util
import os
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from career_agent.backup import create_backup, restore_backup, verify_backup
from career_agent.application import packet_fingerprint
from career_agent.cli import main


def database(path, value):
    connection = sqlite3.connect(path)
    try:
        connection.execute('CREATE TABLE test (value TEXT)')
        connection.execute('INSERT INTO test VALUES (?)', (value,))
        connection.commit()
    finally:
        connection.close()


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'jobs.sqlite3'
        database(self.db, 'fictional candidate')
        database(self.db.with_suffix('.workflows.sqlite3'), 'fictional workflow')
        database(self.db.with_suffix('.discovery.sqlite3'), 'fictional discovery')
        self.packets = self.root / 'packets'; self.packets.mkdir()
        (self.packets / 'abc.json').write_text('{"fictional":true}')
        self.archive = self.root / 'backup.zip'

    def test_roundtrip_preserves_databases_and_packet_without_overwriting(self):
        create_backup(self.db, self.packets, self.archive, offline=True)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            create_backup(self.db, self.packets, self.archive, offline=True)
        destination = self.root / 'restored'
        result = restore_backup(self.archive, destination, offline=True)
        self.assertEqual(result['files'], 4)
        for filename, expected in [('workspace.sqlite3', 'fictional candidate'),
                ('workspace.workflows.sqlite3', 'fictional workflow'), ('workspace.discovery.sqlite3', 'fictional discovery')]:
            connection = sqlite3.connect(destination / filename)
            try:
                self.assertEqual(connection.execute('SELECT value FROM test').fetchone()[0], expected)
            finally:
                connection.close()
        self.assertEqual((destination / 'packets' / 'abc.json').read_text(), '{"fictional":true}')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            restore_backup(self.archive, destination, offline=True)

    def test_source_description_candidate_survives_backup_and_restore(self):
        from dataclasses import replace
        from career_agent.models import Job
        from career_agent.storage import Store
        job = Job.from_mapping({'company': 'Fictional Co', 'title': 'Engineer',
                                'url': 'https://example.invalid/candidate', 'source': 'linkedin', 'description': ''})
        with Store(self.db) as store:
            store.add_jobs([job])
            store.add_discovered_jobs([replace(job, description='Fictional source text.')], stage_descriptions=True)
        create_backup(self.db, self.packets, self.archive, offline=True)
        destination = self.root / 'restored'
        restore_backup(self.archive, destination, offline=True)
        with Store(destination / 'workspace.sqlite3') as store:
            self.assertEqual(store.get_job(job.id).description, '')
            self.assertEqual(store.description_candidates(job.id)[0]['description'], 'Fictional source text.')

    @unittest.skipUnless(importlib.util.find_spec('cryptography'), 'Install backup extra')
    def test_opt_in_ai_configuration_encrypted_roundtrip(self):
        config = self.root / 'ai.json'
        original = json.dumps({'cloud': {'provider': 'anthropic', 'model': 'fictional-model', 'key_env': 'FICTIONAL_API_KEY'}})
        config.write_text(original, encoding='utf-8')
        with patch.dict(os.environ, {'FICTIONAL_API_KEY': 'fictional-secret-value'}):
            result = create_backup(self.db, self.packets, self.archive, offline=True, password='passphrase', ai_config=config)
            self.assertTrue(result['ai_config_included'])
            self.assertNotIn(b'fictional-model', self.archive.read_bytes())
            self.assertTrue(verify_backup(self.archive, password='passphrase')['ai_config_included'])
            restored = restore_backup(self.archive, self.root / 'restored', offline=True, password='passphrase')
        self.assertEqual(json.loads(Path(restored['ai_config']).read_text()), json.loads(original))
        self.assertNotIn('fictional-secret-value', Path(restored['ai_config']).read_text())
        self.assertEqual(config.read_text(), original)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            restore_backup(self.archive, self.root / 'restored', offline=True, password='passphrase')

    def test_ai_config_requires_encryption_and_rejects_inline_secrets(self):
        config = self.root / 'ai.json'
        config.write_text(json.dumps({'cloud': {'provider': 'anthropic', 'model': 'fictional', 'api_key': 'secret'}}))
        with self.assertRaisesRegex(ValueError, 'encrypted'):
            create_backup(self.db, self.packets, self.archive, offline=True, ai_config=config)
        with self.assertRaisesRegex(ValueError, 'Unsupported AI setting'):
            create_backup(self.db, self.packets, self.archive, offline=True, password='passphrase', ai_config=config)
        self.assertFalse(self.archive.exists())
        config.write_text(json.dumps({'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://user:pass@127.0.0.1:11434/v1'}}))
        with self.assertRaisesRegex(ValueError, 'credentials'):
            create_backup(self.db, self.packets, self.archive, offline=True, password='passphrase', ai_config=config)
        with self.assertRaises(ValueError):
            create_backup(self.db, self.packets, self.archive, offline=True, password='passphrase', ai_config=self.root / 'missing')

    def test_ai_config_rejects_symlinks(self):
        config = self.root / 'ai.json'
        config.write_text('{}')
        link = self.root / 'linked.json'
        try:
            link.symlink_to(config)
        except OSError:
            self.skipTest('Creating symlinks requires additional privileges')
        with self.assertRaises(ValueError):
            create_backup(self.db, self.packets, self.archive, offline=True, password='passphrase', ai_config=link)

    def test_oversized_ai_config_rejected_before_parser_reads_it(self):
        config = self.root / 'oversized-ai.json'
        config.write_bytes(b' ' * 32769)
        with patch('career_agent.ai.routes') as routes:
            with self.assertRaisesRegex(ValueError, 'too large'):
                create_backup(self.db, self.packets, self.archive, offline=True, password='passphrase', ai_config=config)
            routes.assert_not_called()
        self.assertFalse(self.archive.exists())

    def test_restore_rejects_injected_ai_configuration(self):
        create_backup(self.db, self.packets, self.archive, offline=True)
        injected = b'{"cloud":{"provider":"anthropic","model":"fictional","api_key":"secret"}}'
        forged = self.root / 'forged.zip'
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(forged, 'w') as target:
            manifest = json.loads(source.read('manifest.json'))
            manifest['files']['ai-routes.json'] = {'size': len(injected), 'sha256': hashlib.sha256(injected).hexdigest()}
            target.writestr('manifest.json', json.dumps(manifest))
            for name in source.namelist():
                if name != 'manifest.json':
                    target.writestr(name, source.read(name))
            target.writestr('ai-routes.json', injected)
        with self.assertRaisesRegex(ValueError, 'Restore failed'):
            restore_backup(forged, self.root / 'forged-restore', offline=True)
        with self.assertRaisesRegex(ValueError, 'verification failed'):
            verify_backup(forged)

    @unittest.skipUnless(importlib.util.find_spec('cryptography'), 'Install backup extra')
    def test_encrypted_backup_roundtrip_and_authentication(self):
        create_backup(self.db, self.packets, self.archive, offline=True, password='fictional passphrase')
        self.assertNotIn(b'fictional candidate', self.archive.read_bytes())
        with self.assertRaisesRegex(ValueError, 'passphrase or corrupted'):
            restore_backup(self.archive, self.root / 'wrong', offline=True, password='wrong')
        self.assertFalse((self.root / 'wrong').exists())
        damaged = self.root / 'damaged'
        data = bytearray(self.archive.read_bytes())
        data[-17] ^= 1
        damaged.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'passphrase or corrupted'):
            restore_backup(damaged, self.root / 'damaged-restore', offline=True, password='fictional passphrase')
        self.assertFalse((self.root / 'damaged-restore').exists())
        destination = self.root / 'encrypted-restored'
        restore_backup(self.archive, destination, offline=True, password='fictional passphrase')
        self.assertEqual((destination / 'packets' / 'abc.json').read_text(), '{"fictional":true}')

    @unittest.skipUnless(importlib.util.find_spec('cryptography'), 'Install backup extra')
    def test_encrypted_cli_prompts_and_restores(self):
        config = self.root / 'ai.json'
        config.write_text('{"local":{"provider":"ollama","model":"fictional","base_url":"http://127.0.0.1:11434/v1"}}')
        with patch('career_agent.cli.getpass.getpass', side_effect=['test password', 'test password']):
            self.assertEqual(main(['--db', str(self.db), 'backup', str(self.archive), '--packet-dir', str(self.packets), '--offline', '--encrypted', '--ai-config', str(config)]), 0)
        with patch('career_agent.cli.getpass.getpass', return_value='test password'):
            self.assertEqual(main(['restore', str(self.archive), str(self.root / 'cli-restored'), '--offline', '--encrypted']), 0)
        self.assertTrue((self.root / 'cli-restored' / 'workspace.sqlite3').is_file())
        self.assertEqual(json.loads((self.root / 'cli-restored' / 'ai-routes.json').read_text()), json.loads(config.read_text()))

    @unittest.skipUnless(importlib.util.find_spec('cryptography'), 'Install backup extra')
    def test_verify_encrypted_backup_and_remove_temporary_files(self):
        create_backup(self.db, self.packets, self.archive, offline=True, password='test password')
        staging = self.root / 'staging'
        staging.mkdir()
        self.assertEqual(verify_backup(self.archive, password='test password', temp_dir=staging)['files'], 4)
        self.assertEqual(list(staging.iterdir()), [])
        with self.assertRaisesRegex(ValueError, 'passphrase or corrupted'):
            verify_backup(self.archive, password='wrong', temp_dir=staging)
        self.assertEqual(list(staging.iterdir()), [])
        with patch('career_agent.cli.getpass.getpass', return_value='test password'):
            self.assertEqual(main(['verify-backup', str(self.archive), '--encrypted', '--temp-dir', str(staging)]), 0)
        self.assertEqual(list(staging.iterdir()), [])

    def test_verify_plain_backup_detects_corruption(self):
        create_backup(self.db, self.packets, self.archive, offline=True)
        self.assertTrue(verify_backup(self.archive, temp_dir=self.root)['verified'])
        self.archive.write_bytes(b'not a backup')
        with self.assertRaisesRegex(ValueError, 'Invalid or corrupt'):
            verify_backup(self.archive, temp_dir=self.root)
        self.assertEqual(list(self.root.glob('.career-verify-*')), [])

    def test_offline_acknowledgement_and_missing_source(self):
        with self.assertRaisesRegex(ValueError, 'Stop the app'):
            create_backup(self.db, self.packets, self.archive)
        self.assertEqual(main(['--db', str(self.db), 'backup', str(self.archive), '--packet-dir', str(self.packets)]), 1)
        self.assertFalse(self.archive.exists())
        with self.assertRaises(ValueError):
            create_backup(self.root / 'missing.sqlite3', self.packets, self.archive, offline=True)

    def test_missing_or_wrong_packet_directory_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'Packet directory does not exist'):
            create_backup(self.db, self.root / 'typo', self.archive, offline=True)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute('CREATE TABLE workflow_runs (job_id TEXT, packet_sha256 TEXT)')
            connection.execute('INSERT INTO workflow_runs VALUES (?, ?)', ('missing-job', 'published'))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, 'Published workflow packets are missing'):
            create_backup(self.db, self.packets, self.archive, offline=True)
        self.assertFalse(self.archive.exists())

    def test_workflow_packets_must_match_latest_published_version(self):
        def packet(text):
            data = {'version': 2, 'job_id': 'job-1', 'resume_markdown': text}
            data['packet_sha256'] = packet_fingerprint(data)
            return data
        older, current = packet('Older fictional draft'), packet('Current fictional draft')
        path = self.packets / 'job-1.json'
        path.write_text(json.dumps(current), encoding='utf-8')
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE workflow_runs (job_id TEXT, packet_sha256 TEXT)')
            db.executemany('INSERT INTO workflow_runs VALUES (?, ?)', [
                ('job-1', older['packet_sha256']), ('job-1', current['packet_sha256']),
                ('job-1', None)])  # A newer queued run has not published a packet.
        create_backup(self.db, self.packets, self.archive, offline=True)
        self.assertTrue(verify_backup(self.archive, temp_dir=self.root)['verified'])
        # Unrelated legacy/custom files remain opaque, even if they are not packets.
        self.assertEqual((self.packets / 'abc.json').read_text(), '{"fictional":true}')
        path.write_text(json.dumps(older), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'latest published workflow'):
            create_backup(self.db, self.packets, self.root / 'stale.zip', offline=True)
        self.assertFalse((self.root / 'stale.zip').exists())
        path.write_text('{invalid', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'published workflow packet is invalid'):
            create_backup(self.db, self.packets, self.root / 'invalid.zip', offline=True)
        path.write_text(json.dumps(current), encoding='utf-8')
        with patch('career_agent.backup.MAX_WORKFLOW_PACKET', 1):
            with self.assertRaisesRegex(ValueError, 'published workflow packet is invalid'):
                create_backup(self.db, self.packets, self.root / 'oversized.zip', offline=True)
        self.db.with_suffix('.workflows.sqlite3').unlink()
        with self.assertRaisesRegex(ValueError, 'require the sibling workflow checkpoint database'):
            create_backup(self.db, self.packets, self.root / 'no-checkpoint.zip', offline=True)
        self.assertFalse((self.root / 'no-checkpoint.zip').exists())

    def test_restore_rejects_old_archive_with_inconsistent_workflow_packet(self):
        packet = {'version': 2, 'job_id': 'job-1', 'resume_markdown': 'Current fictional draft'}
        packet['packet_sha256'] = packet_fingerprint(packet)
        (self.packets / 'job-1.json').write_text(json.dumps(packet), encoding='utf-8')
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE workflow_runs (job_id TEXT, packet_sha256 TEXT)')
            db.execute('INSERT INTO workflow_runs VALUES (?, ?)', ('job-1', packet['packet_sha256']))
        create_backup(self.db, self.packets, self.archive, offline=True)
        with zipfile.ZipFile(self.archive) as source:
            contents = {name: source.read(name) for name in source.namelist()}
        packet['resume_markdown'] = 'Older fictional draft'
        packet['packet_sha256'] = packet_fingerprint(packet)
        contents['packets/job-1.json'] = json.dumps(packet).encode('utf-8')
        manifest = json.loads(contents['manifest.json'])
        manifest['files']['packets/job-1.json'] = {
            'size': len(contents['packets/job-1.json']),
            'sha256': hashlib.sha256(contents['packets/job-1.json']).hexdigest()}
        contents['manifest.json'] = json.dumps(manifest).encode('utf-8')
        old_archive = self.root / 'old-inconsistent.zip'
        with zipfile.ZipFile(old_archive, 'w') as output:
            for name, value in contents.items():
                output.writestr(name, value)
        with self.assertRaisesRegex(ValueError, 'Backup verification failed'):
            verify_backup(old_archive, temp_dir=self.root)
        with self.assertRaisesRegex(ValueError, 'Restore failed'):
            restore_backup(old_archive, self.root / 'inconsistent-restore', offline=True)

    @unittest.skipUnless(importlib.util.find_spec('langgraph'), 'Install durable graph support')
    def test_restored_workflow_can_resume_review_from_real_checkpoint(self):
        from career_agent.candidate import FactBank
        from career_agent.models import CandidateProfile
        from career_agent.storage import Store
        from career_agent.workflows import Workflows, inputs, signature
        from test_dashboard import fake_fact_bank, fake_job

        job = fake_job()
        with Store(self.db) as store:
            store.add_jobs([job])
        state = inputs(job, CandidateProfile(('AI Lead',), ('Python',), (), True),
                       FactBank.parse(fake_fact_bank()), ())
        run = Workflows(self.db, self.packets).start(state)
        create_backup(self.db, self.packets, self.archive, offline=True)
        destination = self.root / 'real-workflow-restored'
        result = restore_backup(self.archive, destination, offline=True)
        restored = Workflows(result['database'], result['packet_dir'])
        decision = {'approved': True, 'reviewed': True, 'packet_sha256': run['packet']['packet_sha256']}
        reviewed = restored.advance(run['run_id'], signature(state), decision)
        self.assertEqual(reviewed['status'], 'approved_for_manual_submission')
        with Store(result['database']) as store:
            self.assertEqual(store.stage_of(job.id), 'discovered')
            self.assertEqual(store.latest_review(job.id)['packet_sha256'], run['packet']['packet_sha256'])

    @unittest.skipUnless(importlib.util.find_spec('langgraph'), 'Install durable graph support')
    def test_missing_root_checkpoints_warn_on_backup_verify_and_restore(self):
        from career_agent.candidate import FactBank
        from career_agent.models import CandidateProfile
        from career_agent.storage import Store
        from career_agent.workflows import Workflows, inputs
        from test_dashboard import fake_fact_bank, fake_job
        job = fake_job()
        with Store(self.db) as store: store.add_jobs([job])
        state = inputs(job, CandidateProfile(('Engineer',), (), ()), FactBank.parse(fake_fact_bank()), ())
        workflows = Workflows(self.db, self.packets)
        first = workflows.start(state); second = workflows.start(state)
        healthy = create_backup(self.db, self.packets, self.root / 'healthy.zip', offline=True)
        self.assertFalse(any('checkpoint' in warning for warning in healthy['warnings']))
        checkpoint = self.db.with_suffix('.workflows.sqlite3')
        with closing(sqlite3.connect(checkpoint)) as connection, connection:
            connection.execute("INSERT INTO checkpoints SELECT thread_id,'child',checkpoint_id,parent_checkpoint_id,type,checkpoint,metadata FROM checkpoints WHERE thread_id=? AND checkpoint_ns=''", (first['run_id'],))
            connection.execute("DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_ns=''", (first['run_id'],))
        older = create_backup(self.db, self.packets, self.root / 'missing-history.zip', offline=True)
        self.assertTrue(any('1 older published workflow' in warning for warning in older['warnings']))
        self.assertFalse(any('latest published workflow' in warning for warning in older['warnings']))
        with closing(sqlite3.connect(checkpoint)) as connection, connection:
            connection.execute('DELETE FROM checkpoints WHERE thread_id=?', (second['run_id'],))
        result = create_backup(self.db, self.packets, self.archive, offline=True)
        self.assertTrue(any('1 latest published workflow' in warning for warning in result['warnings']))
        self.assertTrue(any('1 older published workflow' in warning for warning in result['warnings']))
        if importlib.util.find_spec('cryptography'):
            encrypted_path = self.root / 'missing-checkpoint.enc'
            encrypted = create_backup(self.db, self.packets, encrypted_path, offline=True, password='fictional-passphrase')
            self.assertEqual(encrypted['warnings'], result['warnings'])
            self.assertEqual(verify_backup(encrypted_path, password='fictional-passphrase', temp_dir=self.root)['warnings'], result['warnings'])
        verified = verify_backup(self.archive, temp_dir=self.root)
        restored = restore_backup(self.archive, self.root / 'missing-checkpoint-restored', offline=True)
        self.assertEqual(verified['warnings'], result['warnings'])
        self.assertEqual(restored['warnings'], result['warnings'])
        packet = json.loads((Path(restored['packet_dir']) / (job.id + '.json')).read_text(encoding='utf-8'))
        self.assertEqual(packet['packet_sha256'], second['packet']['packet_sha256'])

    def test_unrecognized_checkpoint_schema_warns_without_preventing_salvage(self):
        packet = {'version': 2, 'job_id': 'job-1', 'resume_markdown': 'Fictional draft'}
        packet['packet_sha256'] = packet_fingerprint(packet)
        (self.packets / 'job-1.json').write_text(json.dumps(packet), encoding='utf-8')
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE workflow_runs (run_id TEXT, job_id TEXT, packet_sha256 TEXT)')
            db.execute('INSERT INTO workflow_runs VALUES (?,?,?)', ('fictional-run', 'job-1', packet['packet_sha256']))
        result = create_backup(self.db, self.packets, self.archive, offline=True)
        self.assertTrue(any('unsupported schema' in warning for warning in result['warnings']))
        self.assertEqual(verify_backup(self.archive, temp_dir=self.root)['warnings'], result['warnings'])

    def test_empty_packet_directory_restores_as_directory(self):
        (self.packets / 'abc.json').unlink()
        create_backup(self.db, self.packets, self.archive, offline=True)
        destination = self.root / 'restored'
        restore_backup(self.archive, destination, offline=True)
        self.assertTrue((destination / 'packets').is_dir())

    def test_missing_discovery_checkpoints_warns_but_preserves_jobs_and_schedule(self):
        from career_agent.automation import Automation
        from career_agent.storage import Store
        from test_dashboard import fake_job
        auto = Automation(self.db)
        auto.save({'query': 'Fictional role', 'location': 'United States', 'sites': ['indeed'],
                   'interval_minutes': 60, 'results': 5, 'acknowledged': True})
        with patch('career_agent.automation.fetch', return_value=[asdict(fake_job())]):
            self.assertTrue(auto.tick())
        # Simulate an already-missing history file, not a backup operation deleting it.
        self.db.with_suffix('.discovery.sqlite3').unlink()
        with patch('career_agent.automation.fetch') as fetch:
            result = create_backup(self.db, self.packets, self.archive, offline=True)
            self.assertTrue(any('checkpoint database is absent' in w for w in result['warnings']))
            self.assertTrue(any('remain enabled' in w for w in result['warnings']))
            verified = verify_backup(self.archive, temp_dir=self.root)
            self.assertEqual(verified['warnings'], result['warnings'])
            restored = restore_backup(self.archive, self.root / 'discovery-restore', offline=True)
            self.assertEqual(restored['warnings'], result['warnings'])
            fetch.assert_not_called()  # Backup, verification, and restore are offline.
        with Store(restored['database']) as store:
            self.assertEqual(store.get_job(fake_job().id).title, fake_job().title)
        self.assertEqual(Automation(restored['database']).list()['schedules'][0]['enabled'], 1)
        self.assertFalse((self.root / 'discovery-restore' / 'workspace.discovery.sqlite3').exists())

    def test_unpublished_run_without_checkpoint_warns_without_blocking_salvage(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE workflow_runs (job_id TEXT, packet_sha256 TEXT, status TEXT)')
            db.execute("INSERT INTO workflow_runs VALUES ('job-1', NULL, 'failed')")
        self.db.with_suffix('.workflows.sqlite3').unlink()
        result = create_backup(self.db, self.packets, self.archive, offline=True)
        self.assertTrue(any('queued or failed runs' in w for w in result['warnings']))
        self.assertEqual(verify_backup(self.archive, temp_dir=self.root)['warnings'], result['warnings'])

    def test_empty_never_run_and_legacy_workspaces_do_not_warn_of_missing_checkpoints(self):
        from career_agent.automation import Automation
        self.db.with_suffix('.discovery.sqlite3').unlink()
        self.db.with_suffix('.workflows.sqlite3').unlink()
        auto = Automation(self.db)
        schedule = auto.save({'query': 'Fictional role', 'location': 'United States', 'sites': ['indeed'],
                              'interval_minutes': 60, 'results': 5, 'acknowledged': True})
        auto.enable(schedule['id'], False)
        result = create_backup(self.db, self.packets, self.archive, offline=True)
        self.assertEqual(result['warnings'], [])
        self.assertEqual(verify_backup(self.archive, temp_dir=self.root)['warnings'], [])

    def test_committed_wal_data_is_included_in_snapshot(self):
        connection = sqlite3.connect(self.db)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA wal_autocheckpoint=0')
            connection.execute('INSERT INTO test VALUES (?)', ('uncheckpointed fictional row',))
            connection.commit()
            self.assertTrue(Path(str(self.db) + '-wal').exists())
            create_backup(self.db, self.packets, self.archive, offline=True)
        finally:
            connection.close()
        destination = self.root / 'wal-restored'
        restore_backup(self.archive, destination, offline=True)
        restored = sqlite3.connect(destination / 'workspace.sqlite3')
        try:
            self.assertIn(('uncheckpointed fictional row',), restored.execute('SELECT value FROM test').fetchall())
        finally:
            restored.close()

    def test_archive_tamper_and_traversal_are_rejected(self):
        create_backup(self.db, self.packets, self.archive, offline=True)
        with zipfile.ZipFile(self.archive) as source:
            contents = {name: source.read(name) for name in source.namelist()}
        contents['packets/abc.json'] = b'{"fictional":false}'
        tampered = self.root / 'tampered.zip'
        with zipfile.ZipFile(tampered, 'w') as target:
            for name, value in contents.items(): target.writestr(name, value)
        with self.assertRaises(ValueError):
            restore_backup(tampered, self.root / 'bad', offline=True)
        malicious = self.root / 'malicious.zip'
        manifest = json.loads(contents['manifest.json'])
        manifest['files']['../outside.json'] = manifest['files'].pop('packets/abc.json')
        with zipfile.ZipFile(malicious, 'w') as target:
            target.writestr('manifest.json', json.dumps(manifest))
            target.writestr('../outside.json', contents['packets/abc.json'])
            for name in ('workspace.sqlite3', 'workspace.workflows.sqlite3', 'workspace.discovery.sqlite3'):
                target.writestr(name, contents[name])
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            restore_backup(malicious, self.root / 'evil', offline=True)
        self.assertFalse((self.root / 'outside.json').exists())

    def test_entry_limit_accounts_for_manifest(self):
        with patch('career_agent.backup.MAX_ENTRIES', 4), self.assertRaisesRegex(ValueError, 'Too many'):
            create_backup(self.db, self.packets, self.archive, offline=True)
        self.assertFalse(self.archive.exists())
        with patch('career_agent.backup.MAX_ENTRIES', 5):
            create_backup(self.db, self.packets, self.archive, offline=True)
            restore_backup(self.archive, self.root / 'limit-restore', offline=True)
