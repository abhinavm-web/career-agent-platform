"""Isolated JobSpy process: approved search settings in, public jobs out."""
import contextlib
import json
import sys
import os
from threading import Timer
from dataclasses import asdict
from .discovery import discover_jobspy


def main():
    watchdog = Timer(125, os._exit, args=(124,))
    watchdog.daemon = True
    watchdog.start()
    try:
        data = json.load(sys.stdin)
        with contextlib.redirect_stdout(sys.stderr):
            options = {'fetch_details': True} if data.get('fetch_details') is True else {}
            jobs = discover_jobspy(data['query'], data['location'], data['results'], sites=data['sites'], **options)
        print(json.dumps({'rows': [asdict(j) for j in jobs], 'source_status': jobs.source_status}))
    finally:
        watchdog.cancel()


if __name__ == '__main__':
    main()
