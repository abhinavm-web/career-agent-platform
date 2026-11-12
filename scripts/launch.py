"""Create a local environment and launch the single-user dashboard."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = 'web,jobspy,resume,lca,backup,ai'


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('Use a port from 1 to 65535') from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('Use a port from 1 to 65535')
    return port


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Set up and start the local Career Agent dashboard.')
    parser.add_argument('--port', type=port_number, default=8765)
    parser.add_argument('--db', help='Workspace database path, relative to the repository; provide with --packet-dir')
    parser.add_argument('--packet-dir', help='Packet directory, relative to the repository; provide with --db')
    parser.add_argument('--skip-install', action='store_true', help='Use an existing environment without installing packages')
    parser.add_argument('--with-pdf', action='store_true', help='Install PDF export and its Chromium browser (additional download)')
    args = parser.parse_args(argv)
    if args.skip_install and args.with_pdf:
        parser.error('--with-pdf cannot be combined with --skip-install')
    if (args.db is None) != (args.packet_dir is None):
        parser.error('Supply --db and --packet-dir together for an existing workspace')
    if args.db is not None and (not args.db.strip() or not args.packet_dir.strip()):
        parser.error('--db and --packet-dir must be nonempty')
    db = args.db if args.db is not None else '.local/career_agent.sqlite3'
    packets = args.packet_dir if args.packet_dir is not None else '.local/packets'
    if sys.version_info < (3, 11):
        print('Python 3.11 or newer is required.', file=sys.stderr)
        return 1
    environment = ROOT / '.venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if (environment.exists() or environment.is_symlink()) and not python.is_file():
        print('The existing .venv has no Python executable. Repair it or choose a separate checkout; no files were removed.', file=sys.stderr)
        return 1
    if args.skip_install and not python.is_file():
        print('No local environment exists. Run without --skip-install first.', file=sys.stderr)
        return 1
    try:
        if not environment.exists():
            print('Creating .venv in this checkout.', flush=True)
            subprocess.run([sys.executable, '-m', 'venv', str(environment)], cwd=ROOT, check=True)
        if not args.skip_install:
            extras = EXTRAS + (',pdf' if args.with_pdf else '')
            print('Installing local dependencies; pip may download packages.', flush=True)
            subprocess.run([str(python), '-m', 'pip', 'install', '-e', f'{ROOT}[{extras}]'], cwd=ROOT, check=True)
            if args.with_pdf:
                print('Installing the Chromium PDF renderer; this downloads a browser.', flush=True)
                subprocess.run([str(python), '-m', 'playwright', 'install', 'chromium'], cwd=ROOT, check=True)
        print(f'Starting local dashboard at http://127.0.0.1:{args.port}/. Press Ctrl+C to stop.', flush=True)
        print('Enabled saved searches in the selected workspace may contact job boards.', flush=True)
        result = subprocess.run([str(python), '-m', 'career_agent.cli', '--db', db, 'serve',
                                 '--packet-dir', packets, '--port', str(args.port)], cwd=ROOT)
        return result.returncode
    except (OSError, subprocess.CalledProcessError):
        print('Setup failed. Check the output above; existing workspace files were not removed.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
