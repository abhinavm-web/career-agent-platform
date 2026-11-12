"""Review live résumé suggestions against fixed fictional evidence, never a truth score."""
import argparse
import json
from pathlib import Path

from career_agent.live_tailoring_evaluation import evaluate


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
