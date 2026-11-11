"""Offline local workspace backup and non-overwriting restore."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MAX_FILE = 2 * 1024 * 1024 * 1024
MAX_TOTAL = 4 * 1024 * 1024 * 1024
MAX_ENTRIES = 10002
DATABASES = ('workspace.sqlite3', 'workspace.workflows.sqlite3', 'workspace.discovery.sqlite3')


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Expected a regular file: {path.name}')


def create_backup(db, packets, destination, *, offline=False):
    """Write an archive only after an explicit stopped-server acknowledgement.

    The caller must stop all writers: SQLite snapshots alone cannot synchronize
    three independent databases and packet files to one logical instant.
    """
    if offline is not True:
        raise ValueError('Stop the app and all workspace writers, then pass --offline')
    db, packets, destination = Path(db), Path(packets), Path(destination)
    _regular(db)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Backup destination already exists')
    if not destination.parent.is_dir():
        raise ValueError('Backup destination directory does not exist')
    if packets.is_symlink() or not packets.is_dir():
        raise ValueError('Packet directory does not exist or is not a regular directory; choose the correct --packet-dir')
    packet_files = sorted(packets.glob('*.json'))
    for path in packet_files:
        _regular(path)
    if len(packet_files) + len(DATABASES) + 1 > MAX_ENTRIES:
        raise ValueError('Too many backup files')
    if any(path.stat().st_size > MAX_FILE for path in packet_files):
        raise ValueError('A packet exceeds the backup file size limit')
    with tempfile.TemporaryDirectory(prefix='.career-backup-', dir=destination.parent) as temp:
        temp_path = Path(temp)
        sources = [(db, DATABASES[0]), (db.with_suffix('.workflows.sqlite3'), DATABASES[1]),
                   (db.with_suffix('.discovery.sqlite3'), DATABASES[2])]
        files = []
        for source, name in sources:
            if not source.exists() and source != db:
                continue
            _regular(source)
            target = temp_path / name
            with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original:
                with closing(sqlite3.connect(target)) as snapshot:
                    original.backup(snapshot)
                    if snapshot.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError('A SQLite database failed its integrity check')
                    if name == DATABASES[0] and snapshot.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
                    ).fetchone():
                        published_ids = {row[0] for row in snapshot.execute(
                            'SELECT DISTINCT job_id FROM workflow_runs WHERE packet_sha256 IS NOT NULL'
                        )}
                        packet_ids = {path.name[:-5] for path in packet_files}
                        if not published_ids.issubset(packet_ids):
                            raise ValueError('Published workflow packets are missing from --packet-dir; choose the correct directory')
            files.append((target, name))
        files.extend((path, 'packets/' + path.name) for path in packet_files)
        if any(path.stat().st_size > MAX_FILE for path, _ in files):
            raise ValueError('A workspace file exceeds the backup file size limit')
        if sum(path.stat().st_size for path, _ in files) > MAX_TOTAL:
            raise ValueError('Workspace exceeds the backup size limit')
        manifest = {'format': 'career-agent-backup', 'version': 1,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'files': {name: {'sha256': _digest(path), 'size': path.stat().st_size}
                              for path, name in files}}
        manifest_data = json.dumps(manifest, sort_keys=True).encode('utf-8')
        if len(manifest_data) > 2 * 1024 * 1024:
            raise ValueError('Backup manifest exceeds the size limit')
        archive_path = temp_path / 'archive.zip'
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr('manifest.json', manifest_data)
            for path, name in files:
                archive.write(path, name)
        with zipfile.ZipFile(archive_path) as archive:
            for name, expected in manifest['files'].items():
                digest = hashlib.sha256()
                with archive.open(name) as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != expected['sha256']:
                    raise ValueError('A workspace file changed during backup; stop all writers and retry')
        archive_path.chmod(0o600)
        # Atomic create-if-absent; hard link prevents replacing a concurrent file.
        os.link(archive_path, destination)
    return {'archive': str(destination), 'files': len(files)}


def restore_backup(archive_path, destination, *, offline=False):
    """Validate an archive and restore into a newly created directory only."""
    if offline is not True:
        raise ValueError('Stop the app and all workspace writers, then pass --offline')
    archive_path, destination = Path(archive_path), Path(destination)
    _regular(archive_path)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Restore destination already exists; choose a new directory')
    if not destination.parent.is_dir():
        raise ValueError('Restore destination parent does not exist')
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > MAX_ENTRIES or len(names) != len(set(names)) or 'manifest.json' not in names:
                raise ValueError('Invalid backup archive entries')
            if archive.getinfo('manifest.json').file_size > 2 * 1024 * 1024:
                raise ValueError('Backup manifest is too large')
            manifest = json.loads(archive.read('manifest.json'))
            if not isinstance(manifest, dict) or manifest.get('format') != 'career-agent-backup' or manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict):
                raise ValueError('Unsupported backup manifest')
            files = manifest['files']
            if set(names) != set(files) | {'manifest.json'} or DATABASES[0] not in files:
                raise ValueError('Backup files do not match the manifest')
            total = 0
            for name, metadata in files.items():
                parts = PurePosixPath(name).parts
                if not (name in DATABASES or (len(parts) == 2 and parts[0] == 'packets' and parts[1] not in ('.', '..') and parts[1].endswith('.json') and '\\' not in parts[1] and ':' not in parts[1])):
                    raise ValueError('Unsafe backup file name')
                entry = archive.getinfo(name)
                if not isinstance(metadata, dict) or set(metadata) != {'sha256', 'size'} or type(metadata['size']) is not int or metadata['size'] != entry.file_size or entry.file_size > MAX_FILE:
                    raise ValueError('Invalid backup file metadata')
                total += entry.file_size
            if total > MAX_TOTAL:
                raise ValueError('Backup exceeds the restore size limit')
            # Create the destination exclusively. Never replace an existing workspace.
            destination.mkdir(mode=0o700)
            try:
                (destination / 'packets').mkdir(mode=0o700)
                for name, metadata in files.items():
                    target = destination.joinpath(*PurePosixPath(name).parts)
                    target.parent.mkdir(mode=0o700, exist_ok=True)
                    digest = hashlib.sha256()
                    with archive.open(name) as source, target.open('xb') as output:
                        os.chmod(target, 0o600)
                        while chunk := source.read(1024 * 1024):
                            digest.update(chunk)
                            output.write(chunk)
                    if digest.hexdigest() != metadata['sha256']:
                        raise ValueError(f'Backup checksum failed: {name}')
                for name in DATABASES:
                    target = destination / name
                    if target.exists():
                        with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                                raise ValueError('Restored database failed its integrity check')
            except Exception:
                # Leave partial files in the new directory for inspection; never delete user data.
                raise ValueError(f'Restore failed; incomplete files are in the new directory {destination}') from None
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, KeyError, sqlite3.Error):
        raise ValueError('Invalid or corrupt backup archive') from None
    return {'directory': str(destination), 'database': str(destination / DATABASES[0]),
            'packet_dir': str(destination / 'packets'), 'files': len(files)}
