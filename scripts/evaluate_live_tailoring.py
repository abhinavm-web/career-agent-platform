"""Review live résumé suggestions against fixed fictional evidence, never a truth score."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from career_agent import ai
from career_agent.ai_history import DailyRequestLimit, run_recorded
from career_agent.candidate import FactBank
from career_agent.models import Job


CASES = (
    ('team-contribution', 'Contributed Python scripts to a data pipeline as a team member.',
     'Required qualifications:\nExperience building Python data pipelines.\nExperience leading a team.'),
    ('nonproduction', 'Built a prototype dashboard; it was not deployed to production.',
     'Required qualifications:\nBuild and operate production dashboards.'),
    ('measured-result', 'Reduced report preparation time by 15%.',
     'Required qualifications:\nImprove reporting efficiency by 50%.'),
)


def evaluate(route_name: str, db_path: Path, acknowledged: bool = False) -> dict:
    if acknowledged is not True:
        raise ValueError('Confirm sending three fictional cases to the selected provider')
    route = ai.routes().get(route_name)
    if route is None:
        raise ValueError('Choose a configured AI route')
    fingerprint = ai.route_fingerprint(route_name, route)
    cases = []
    for case_id, source, description in CASES:
        job = Job.from_mapping({'company': 'Fictional employer', 'title': 'Fictional analyst',
                                'url': 'https://example.invalid/' + case_id, 'description': description})
        bank = FactBank.parse({'name': 'Fictional candidate', 'facts': [{
            'id': 'fact-01', 'section': 'experience', 'statement': source, 'keywords': [],
            'source_note': 'Fictional only', 'verified_by_candidate': True}]})
        row = {'case': case_id, 'source_fact': source, 'job_description': description}
        try:
            result = run_recorded(db_path, route_name, 'resume_suggestions', ai.tailoring_suggestions,
                                  job, bank, route_name, True, expected_route_fingerprint=fingerprint)
        except DailyRequestLimit as error:
            cases.append({**row, 'status': 'not_sent_limit', 'error': str(error)})
            break
        except ValueError as error:
            cases.append({**row, 'status': 'failed', 'error': str(error)})
            # A changed route invalidates the user's consent for later cases too.
            if 'configuration changed' in str(error):
                break
        else:
            cases.append({**row, 'status': 'validated_response', 'configured_model': result['model'],
                          'reported_model': result.get('reported_model'), 'suggestions': result['suggestions']})
    return {'route': route_name, 'provider': route['provider'], 'cases': cases,
            'notice': ('Fictional cases only. Valid citations and missing warnings do not establish factual fidelity. '
                       'A person must review each suggestion against its source. This small sample is not a benchmark.')}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--route', required=True, help='Configured route; cloud requests may incur charges')
    parser.add_argument('--db', type=Path, default=Path('.local/career_agent.sqlite3'), help='Workspace used for configured request limits and optional history')
    parser.add_argument('--acknowledge-provider-requests', action='store_true',
                        help='Send up to three fixed fictional cases to the selected provider')
    args = parser.parse_args()
    try:
        report = evaluate(args.route, args.db, args.acknowledge_provider_requests)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if all(row['status'] == 'validated_response' for row in report['cases']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
