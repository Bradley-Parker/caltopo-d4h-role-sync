"""
Probe CalTopo API connectivity and v0 group members endpoint.

Uses service-account signed requests (CALTOPO_CREDENTIAL_ID / CALTOPO_CREDENTIAL_SECRET).
"""

from __future__ import annotations

import argparse
import os
import sys

from caltopo_client import CalTopoAPIError, CalTopoClient
from env_loader import load_env


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Probe CalTopo API auth and endpoints")
    parser.add_argument(
        "--team-id",
        default=os.environ.get("CALTOPO_TEAM_ID", ""),
        help="CalTopo team/group id (default: CALTOPO_TEAM_ID env)",
    )
    parser.add_argument(
        "--skip-v1",
        action="store_true",
        help="Skip official v1 account data probe",
    )
    parser.add_argument(
        "--skip-v0",
        action="store_true",
        help="Skip v0 group members probe",
    )
    args = parser.parse_args()

    if not args.team_id:
        print("Error: pass --team-id or set CALTOPO_TEAM_ID", file=sys.stderr)
        return 2

    try:
        client = CalTopoClient()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    exit_code = 0

    if not args.skip_v1:
        print(f"=== v1 account data: /api/v1/acct/{args.team_id}/since/0 ===")
        try:
            data = client.get_account_data(args.team_id)
            features = data.get("features") or []
            accounts = data.get("accounts") or []
            print(f"OK — {len(features)} features, {len(accounts)} accounts")
            for acct in accounts[:5]:
                props = acct.get("properties") or {}
                title = props.get("title") or props.get("alias") or acct.get("id")
                print(f"  account {acct.get('id')}: {title}")
            if len(accounts) > 5:
                print(f"  ... and {len(accounts) - 5} more")
        except CalTopoAPIError as exc:
            print(f"FAIL — {exc}", file=sys.stderr)
            exit_code = 1

    if not args.skip_v0:
        print(f"\n=== v0 group members: /api/v0/group/{args.team_id}/members ===")
        try:
            members = client.get_group_members(args.team_id)
            print(f"OK — {len(members)} members")
            for member in members[:5]:
                print(
                    f"  {member.get('id')}: {member.get('fullName')} "
                    f"<{member.get('email')}> permission={member.get('permission')}"
                )
            if len(members) > 5:
                print(f"  ... and {len(members) - 5} more")
        except CalTopoAPIError as exc:
            print(f"FAIL — {exc}", file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
