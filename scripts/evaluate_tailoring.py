"""Run a fixed offline fictional benchmark through real output validation and review checks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from career_agent import ai
from career_agent.candidate import FactBank
from career_agent.models import Job

DEFAULT_FIXTURE = Path(__file__).resolve().parents[1] / 'examples' / 'tailoring-evaluation.json'


def evaluate(document):
    cases = document.get('cases') if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError('Expected a nonempty cases array')
    job = Job.from_mapping({'title': 'Fictional role', 'company': 'Fictional employer',
        'job_url': 'https://example.invalid/jobs/fictional', 'description': 'Requires Python.'})
    route = {'local': {'provider': 'ollama', 'model': 'fixture', 'base_url': 'http://127.0.0.1:11434/v1'}}
    results = []; ids = set()
    for case in cases:
        if (not isinstance(case, dict) or set(case) != {'id', 'category', 'source', 'wording', 'label'}
                or not all(isinstance(case[k], str) and case[k].strip() for k in case)
                or case['label'] not in {'supported', 'unsupported'} or case['id'] in ids):
            raise ValueError('Invalid or duplicate fixture case')
        ids.add(case['id'])
        bank = FactBank.parse({'name': 'Fictional candidate', 'headline': '', 'facts': [
            {'id': 'fact-01', 'section': 'experience', 'statement': case['source'], 'keywords': [],
             'source_note': 'Fictional evidence', 'verified_by_candidate': True}]})
        output = json.dumps({'suggestions': [{'fact_id': 'fact-01', 'job_quote': 'Requires Python.', 'wording': case['wording']}]})
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value=output):
            suggestion = ai.tailoring_suggestions(job, bank, 'local', True)['suggestions'][0]
        results.append({'id': case['id'], 'category': case['category'], 'reference_label': case['label'],
                        'warning_emitted': bool(suggestion['review_warnings']), 'warnings': suggestion['review_warnings']})
    unsupported = [r for r in results if r['reference_label'] == 'unsupported']
    supported = [r for r in results if r['reference_label'] == 'supported']
    return {'scope': 'Hand-labeled fictional cases; warnings are review prompts, not semantic validation or approval.',
            'total': len(results), 'unsupported_cases': len(unsupported),
            'unsupported_with_warning': sum(r['warning_emitted'] for r in unsupported),
            'unsupported_without_warning': sum(not r['warning_emitted'] for r in unsupported),
            'supported_cases': len(supported), 'supported_with_warning': sum(r['warning_emitted'] for r in supported),
            'cases': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()
    print(json.dumps(evaluate(json.loads(args.fixtures.read_text(encoding='utf-8'))), indent=2))


if __name__ == '__main__':
    main()
