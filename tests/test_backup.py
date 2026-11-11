"""Offline archive tests use fictional records and temporary workspaces only."""
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from career_agent.backup import create_backup, restore_backup
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

    def test_empty_packet_directory_restores_as_directory(self):
        (self.packets / 'abc.json').unlink()
        create_backup(self.db, self.packets, self.archive, offline=True)
        destination = self.root / 'restored'
        restore_backup(self.archive, destination, offline=True)
        self.assertTrue((destination / 'packets').is_dir())

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
