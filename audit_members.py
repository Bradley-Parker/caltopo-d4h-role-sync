"""
CalTopo team member audit against a D4H roster.

Purpose
-------
Find CalTopo team members who should not be there: anyone on the CalTopo team
who cannot be matched to a D4H member for the configured team.

D4H members count as a valid match when status is OPERATIONAL or NON_OPERATIONAL.
(OBSERVER and RETIRED do not satisfy the audit.)

We intentionally do *not* report D4H members missing from CalTopo. D4H is the
membership source of truth; this script only flags unexplained CalTopo accounts.

Data flow
---------
1. Fetch D4H roster: operational + non-operational (filtered by --d4h-team).
2. Fetch CalTopo members via v0 group API.
3. Walk each CalTopo member and try to find a D4H counterpart.

Matching (CalTopo -> D4H), in order
-----------------------------------
1. Stored link   audit_exceptions.json (and legacy member_links.json) map d4h_id -> caltopo_id.
2. Email         case-insensitive exact match on roster email.
3. Name          loose match on name variants (D4H "Last, First" vs CalTopo free text).
4. No match      extra_in_caltopo — primary outcome that needs operator action.

Exceptions (audit_exceptions.py + audit_exceptions.json ignore section) skip shared
accounts that legitimately exist in CalTopo without a D4H roster row.

Guided mode (--guided)
----------------------
After the report, prompts for each row needing review. When the audit found a
probable name match, guided mode offers that D4H member as the default mapping
(press a or y, then accept). Operator can also:
  y  Enter a different D4H member id (validated via API); saved to links.
  i  Mark CalTopo account as ignore-only (service/shared account).
  n  Skip this row.
  q  Stop guided mode.

Exit codes
----------
0  no extra, ambiguous, or broken-link rows
1  one or more CalTopo members need review
2  configuration or API failure
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from audit_store import (
    DEFAULT_EXCEPTIONS_PATH,
    append_ignore_entry,
    append_tracked_link,
    is_audit_exception,
    load_member_links,
)
from caltopo_client import CalTopoAPIError, CalTopoClient
from d4h_client import D4HAPIError, D4HClient
from env_loader import load_env
from match_utils import name_match_keys

# Rows where a CalTopo member matched D4H; informational only.
MATCHED_STATUSES = frozenset({"matched_email", "matched_link", "probable_name"})

# Rows that fail the audit and yield exit code 1.
REVIEW_STATUSES = frozenset({"extra_in_caltopo", "ambiguous", "broken_link"})

# D4H statuses that count as "on roster" for this audit.
ALLOWED_D4H_STATUSES = frozenset(
    {
        D4HClient.OPERATIONAL_STATUS,
        D4HClient.NON_OPERATIONAL_STATUS,
    }
)


@dataclass
class AuditRow:
    """One CalTopo member classified against the D4H roster."""

    status: str
    caltopo: dict[str, Any]
    d4h: dict[str, Any] | None = None
    d4h_candidates: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""


def reverse_member_links(links: dict[str, str]) -> dict[str, str]:
    """Invert d4h_id -> caltopo_id for lookup while iterating CalTopo members."""
    return {caltopo_id: d4h_id for d4h_id, caltopo_id in links.items()}


def filter_d4h_team(
    members: list[dict[str, Any]],
    team_filter: str,
) -> list[dict[str, Any]]:
    """Keep members whose Team title contains team_filter (case-insensitive)."""
    needle = team_filter.strip().lower()
    if not needle:
        return members
    return [
        m
        for m in members
        if needle in (m.get("Team") or "").lower()
    ]


def build_d4h_audit_roster(
    client: D4HClient,
    team_filter: str,
) -> list[dict[str, Any]]:
    """
    Build the D4H side of the audit: operational + non-operational members.

    Uses D4HClient list/normalize helpers only (no changes to the shared client).
    Operational members still respect the same join-date filter as get_operational_members().
    """
    from datetime import date

    today = date.today()
    raw_members: dict[str, dict[str, Any]] = {}

    operational = client.list_members(status=client.OPERATIONAL_STATUS)
    operational = [
        row
        for row in operational
        if client._parse_starts_at(row.get("startsAt")) is None
        or client._parse_starts_at(row.get("startsAt")) < today
    ]
    for row in operational:
        raw_members[str(row["id"])] = row

    for row in client.list_members(status=client.NON_OPERATIONAL_STATUS):
        raw_members[str(row["id"])] = row

    team_ids = sorted(
        {
            (row.get("owner") or {}).get("id")
            for row in raw_members.values()
            if (row.get("owner") or {}).get("id") is not None
        }
    )
    team_titles = client.get_team_id_to_title(team_ids)

    roster: list[dict[str, Any]] = []
    for row in raw_members.values():
        normalized = client._normalize_member_for_sync(
            row,
            client.EXCLUDED_TEAMS,
            team_titles,
        )
        if not normalized:
            continue
        normalized["_d4h_status"] = row.get("status")
        roster.append(normalized)

    return filter_d4h_team(roster, team_filter)


def index_d4h_roster(
    members: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """
    Build lookup indexes over the normalized D4H roster.

    Returns (by_id, by_email, by_name_key). Name keys include flipped forms so
    D4H "Leslie, John" can match CalTopo "John Leslie".
    """
    by_id: dict[str, dict[str, Any]] = {}
    by_email: dict[str, list[dict[str, Any]]] = {}
    by_name_key: dict[str, list[dict[str, Any]]] = {}

    for member in members:
        d4h_id = str(member.get("_d4h_id") or member.get("id") or "")
        if d4h_id:
            by_id[d4h_id] = member

        email = (member.get("Email") or "").strip().lower()
        if email:
            by_email.setdefault(email, []).append(member)

        name = member.get("Name") or ""
        for key in name_match_keys(name):
            by_name_key.setdefault(key, []).append(member)

    return by_id, by_email, by_name_key


def dedupe_d4h_by_id(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate D4H hits that share the same member id."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for member in candidates:
        member_id = str(member.get("_d4h_id") or member.get("id") or "")
        if not member_id or member_id in seen:
            continue
        seen.add(member_id)
        unique.append(member)
    return unique


def find_d4h_name_candidates(
    caltopo_name: str,
    by_name_key: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """All D4H roster rows whose name keys overlap with this CalTopo display name."""
    candidates: list[dict[str, Any]] = []
    for key in name_match_keys(caltopo_name):
        candidates.extend(by_name_key.get(key, []))
    return dedupe_d4h_by_id(candidates)


def classify_caltopo_member(
    caltopo: dict[str, Any],
    *,
    d4h_by_id: dict[str, dict[str, Any]],
    d4h_by_email: dict[str, list[dict[str, Any]]],
    d4h_by_name_key: dict[str, list[dict[str, Any]]],
    links_by_caltopo_id: dict[str, str],
) -> AuditRow:
    """
    Decide how one CalTopo member relates to the D4H roster.

    Called once per CalTopo account (except exceptions handled by caller).
    """
    caltopo_id = (caltopo.get("id") or "").strip()
    caltopo_name = CalTopoClient.member_name(caltopo)
    caltopo_email = CalTopoClient.member_email(caltopo)

    # Tier 1: operator-confirmed mapping in audit_exceptions.json / member_links.json.
    if caltopo_id and caltopo_id in links_by_caltopo_id:
        d4h_id = links_by_caltopo_id[caltopo_id]
        d4h = d4h_by_id.get(d4h_id)
        if d4h:
            return AuditRow(
                status="matched_link",
                caltopo=caltopo,
                d4h=d4h,
                detail=f"stored link d4h_id {d4h_id}",
            )
        return AuditRow(
            status="broken_link",
            caltopo=caltopo,
            detail=(
                f"stored link maps CalTopo {caltopo_id} to D4H {d4h_id}, "
                "but that D4H member is not on the filtered roster"
            ),
        )

    # Tier 2: email is the strongest automated signal when addresses align.
    if caltopo_email:
        email_hits = dedupe_d4h_by_id(d4h_by_email.get(caltopo_email, []))
        if len(email_hits) == 1:
            return AuditRow(
                status="matched_email",
                caltopo=caltopo,
                d4h=email_hits[0],
            )
        if len(email_hits) > 1:
            return AuditRow(
                status="ambiguous",
                caltopo=caltopo,
                d4h_candidates=email_hits,
                detail="multiple D4H roster members share this email",
            )

    # Tier 3: name-only match (Apple relay, personal Gmail, nickname drift, etc.).
    name_hits = find_d4h_name_candidates(caltopo_name, d4h_by_name_key)
    if len(name_hits) == 1:
        return AuditRow(
            status="probable_name",
            caltopo=caltopo,
            d4h=name_hits[0],
            detail="name-only match - confirm with --guided or edit audit_exceptions.json",
        )
    if len(name_hits) > 1:
        return AuditRow(
            status="ambiguous",
            caltopo=caltopo,
            d4h_candidates=name_hits,
            detail="multiple D4H roster name matches",
        )

    return AuditRow(status="extra_in_caltopo", caltopo=caltopo)


def audit_caltopo_members(
    d4h_roster: list[dict[str, Any]],
    caltopo_members: list[dict[str, Any]],
    links: dict[str, str],
    *,
    exceptions_path: Path = DEFAULT_EXCEPTIONS_PATH,
) -> list[AuditRow]:
    """
    Classify every CalTopo team member against the D4H roster.

    Iteration is CalTopo-centric: we only emit rows for CalTopo accounts.
    """
    d4h_by_id, d4h_by_email, d4h_by_name_key = index_d4h_roster(d4h_roster)
    links_by_caltopo_id = reverse_member_links(links)
    rows: list[AuditRow] = []

    for caltopo in caltopo_members:
        if is_audit_exception(caltopo, exceptions_path=exceptions_path):
            rows.append(AuditRow(status="exception", caltopo=caltopo))
            continue

        rows.append(
            classify_caltopo_member(
                caltopo,
                d4h_by_id=d4h_by_id,
                d4h_by_email=d4h_by_email,
                d4h_by_name_key=d4h_by_name_key,
                links_by_caltopo_id=links_by_caltopo_id,
            )
        )

    return rows


def format_d4h_member(member: dict[str, Any]) -> str:
    status = member.get("_d4h_status")
    status_suffix = f" status={status}" if status else ""
    return (
        f"D4H {member.get('_d4h_id') or member.get('id')}: "
        f"{member.get('Name')} <{member.get('Email')}>{status_suffix}"
    )


def format_caltopo_member(member: dict[str, Any]) -> str:
    return (
        f"CalTopo {member.get('id')}: "
        f"{member.get('fullName')} <{member.get('email')}> "
        f"permission={member.get('permission')}"
    )


def format_member_line(row: AuditRow) -> str:
    parts = [format_caltopo_member(row.caltopo)]
    if row.d4h:
        parts.append(format_d4h_member(row.d4h))
    return "  " + " | ".join(parts)


def print_section(title: str, rows: list[AuditRow]) -> None:
    print(f"\n{title} ({len(rows)})")
    if not rows:
        print("  (none)")
        return
    for row in rows:
        print(format_member_line(row))
        if row.detail:
            print(f"    {row.detail}")
        for candidate in row.d4h_candidates:
            print(f"    candidate: {format_d4h_member(candidate)}")


def summarize(rows: list[AuditRow]) -> dict[str, list[AuditRow]]:
    buckets: dict[str, list[AuditRow]] = {}
    for row in rows:
        buckets.setdefault(row.status, []).append(row)
    return buckets


def print_audit_report(
    *,
    d4h_team: str,
    d4h_roster_size: int,
    caltopo_team_id: str,
    caltopo_size: int,
    links_count: int,
    buckets: dict[str, list[AuditRow]],
    exceptions_path: Path | None = None,
    exceptions_loaded: bool = True,
) -> None:
    print("=== CalTopo member audit (vs D4H operational + non-operational roster) ===")
    print(
        f"D4H team filter: {d4h_team!r} -> {d4h_roster_size} members "
        "(OPERATIONAL or NON_OPERATIONAL)"
    )
    print(f"CalTopo team: {caltopo_team_id} -> {caltopo_size} members")
    if exceptions_path is not None:
        if exceptions_loaded:
            print(f"Exceptions file: {exceptions_path} (loaded)")
        else:
            print(
                f"Exceptions file: {exceptions_path} (MISSING — running with empty "
                "links/ignores; more members may show as unmatched / extra_in_caltopo)"
            )
    print(f"Stored links: {links_count}")

    print_section("Extra in CalTopo (not on D4H roster)", buckets.get("extra_in_caltopo", []))
    print_section("Ambiguous match", buckets.get("ambiguous", []))
    print_section("Broken stored link", buckets.get("broken_link", []))
    print_section("Matched by email", buckets.get("matched_email", []))
    print_section("Matched by stored link", buckets.get("matched_link", []))
    print_section(
        "Probable name match (confirm in audit_exceptions.json)",
        buckets.get("probable_name", []),
    )
    print_section("Exceptions (ignored)", buckets.get("exception", []))

    matched = sum(len(buckets.get(status, [])) for status in MATCHED_STATUSES)
    review = sum(len(buckets.get(status, [])) for status in REVIEW_STATUSES)
    print(
        f"\nSummary: {matched} CalTopo members explained, "
        f"{review} need review, "
        f"{len(buckets.get('exception', []))} exceptions"
    )


def fetch_d4h_member_raw(client: D4HClient, d4h_id: str) -> dict[str, Any]:
    """Load one D4H member by id; raises ValueError when id is invalid."""
    d4h_id = d4h_id.strip()
    if not d4h_id.isdigit():
        raise ValueError("D4H member id must be numeric")
    try:
        return client.get_member(d4h_id)
    except D4HAPIError as exc:
        raise ValueError(f"D4H API rejected id {d4h_id}: {exc}") from exc


def validate_d4h_member_for_tracking(
    client: D4HClient,
    d4h_id: str,
    *,
    team_filter: str,
) -> tuple[dict[str, Any], str | None]:
    """
    Validate a D4H id for guided linking.

    Returns (raw_member, warning_message). Raises ValueError when unusable.
    """
    member = fetch_d4h_member_raw(client, d4h_id)
    status = member.get("status")
    if status not in ALLOWED_D4H_STATUSES:
        raise ValueError(
            f"Member {d4h_id} has status {status!r}; "
            f"expected one of {sorted(ALLOWED_D4H_STATUSES)}"
        )

    warning: str | None = None
    owner = member.get("owner") or {}
    if owner.get("resourceType") == "Team":
        team_id = str(owner.get("id", ""))
        team_titles = client.get_team_id_to_title([team_id])
        team_name = team_titles.get(team_id, team_id)
        needle = team_filter.strip().lower()
        if needle and needle not in team_name.lower():
            warning = (
                f"Member team {team_name!r} does not match filter {team_filter!r}"
            )

    return member, warning


def prompt_yes_no(message: str, *, default: bool | None = None) -> bool:
    suffix = " [y/n]: "
    if default is True:
        suffix = " [Y/n]: "
    elif default is False:
        suffix = " [y/N]: "

    while True:
        answer = input(message + suffix).strip().lower()
        if not answer and default is not None:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please enter y or n.")


def probable_d4h_match(row: AuditRow) -> dict[str, Any] | None:
    """Return the single D4H roster row from a probable_name audit match, if any."""
    if row.status == "probable_name" and row.d4h:
        return row.d4h
    return None


def confirm_and_save_link(
    row: AuditRow,
    d4h_id: str,
    *,
    d4h_client: D4HClient,
    team_filter: str,
    exceptions_path: Path,
) -> bool:
    """
    Validate a D4H id and persist d4h_id -> CalTopo id to audit_exceptions.json.

    Returns True when a link was saved.
    """
    try:
        member, warning = validate_d4h_member_for_tracking(
            d4h_client,
            d4h_id,
            team_filter=team_filter,
        )
    except ValueError as exc:
        print(f"Invalid: {exc}")
        return False

    email_obj = member.get("email") or {}
    email = email_obj.get("value") if isinstance(email_obj, dict) else email_obj
    print(
        f"Found D4H {member.get('id')}: {member.get('name')} "
        f"<{email}> status={member.get('status')}"
    )
    if warning:
        print(f"Warning: {warning}")
        if not prompt_yes_no("Use this member anyway?", default=False):
            return False

    if not prompt_yes_no("Save link to audit_exceptions.json?", default=True):
        return False

    note = input("Optional note: ").strip()
    caltopo_id = str(row.caltopo.get("id"))
    append_tracked_link(
        str(member.get("id")),
        caltopo_id,
        note=note,
        exceptions_path=exceptions_path,
    )
    print(f"Saved link D4H {member.get('id')} -> CalTopo {caltopo_id}")
    return True


def run_guided_mode(
    review_rows: list[AuditRow],
    *,
    d4h_client: D4HClient,
    team_filter: str,
    exceptions_path: Path,
) -> bool:
    """
    Walk review rows interactively. Returns True when any persistence file changed.
    """
    if not review_rows:
        print("\nGuided mode: nothing to review.")
        return False

    print("\n=== Guided mode ===")
    print(
        "For each CalTopo member: [a] accept probable name match  "
        "[y] enter D4H id  [i] ignore  [n] skip  [q] quit"
    )
    changed = False

    for row in review_rows:
        print(f"\n{format_member_line(row)}")
        if row.detail:
            print(f"  {row.detail}")
        for candidate in row.d4h_candidates:
            print(f"  candidate: {format_d4h_member(candidate)}")

        suggestion = probable_d4h_match(row)
        if suggestion:
            print(f"  suggested: {format_d4h_member(suggestion).strip()}")

        while True:
            if suggestion:
                prompt = "Action [a/y/i/n/q]: "
            else:
                prompt = "Action [y/i/n/q]: "
            choice = input(prompt).strip().lower()
            if choice in {"n", ""}:
                break
            if choice == "q":
                return changed
            if choice == "i":
                note = input("Optional note: ").strip()
                append_ignore_entry(row.caltopo, note=note, exceptions_path=exceptions_path)
                print(f"Saved ignore entry to {exceptions_path}")
                changed = True
                break
            if choice == "a":
                if not suggestion:
                    print("No probable name suggestion for this row.")
                    continue
                d4h_id = str(suggestion.get("_d4h_id") or suggestion.get("id") or "")
                if prompt_yes_no(
                    f"Accept probable name match (D4H {d4h_id})?",
                    default=True,
                ) and confirm_and_save_link(
                    row,
                    d4h_id,
                    d4h_client=d4h_client,
                    team_filter=team_filter,
                    exceptions_path=exceptions_path,
                ):
                    changed = True
                    break
                continue
            if choice == "y":
                if suggestion and prompt_yes_no(
                    f"Use probable name match (D4H "
                    f"{suggestion.get('_d4h_id') or suggestion.get('id')})?",
                    default=True,
                ):
                    d4h_id = str(suggestion.get("_d4h_id") or suggestion.get("id") or "")
                    if confirm_and_save_link(
                        row,
                        d4h_id,
                        d4h_client=d4h_client,
                        team_filter=team_filter,
                        exceptions_path=exceptions_path,
                    ):
                        changed = True
                        break
                    continue

                if not prompt_yes_no("Track this CalTopo member with a D4H id?"):
                    break
                d4h_id = input("D4H member id: ").strip()
                if confirm_and_save_link(
                    row,
                    d4h_id,
                    d4h_client=d4h_client,
                    team_filter=team_filter,
                    exceptions_path=exceptions_path,
                ):
                    changed = True
                break

            print("Please enter a valid action.")

    return changed


def collect_guided_rows(buckets: dict[str, list[AuditRow]]) -> list[AuditRow]:
    """Review queue for guided mode: failures first, then probable name matches."""
    rows: list[AuditRow] = []
    for status in ("extra_in_caltopo", "ambiguous", "broken_link", "probable_name"):
        rows.extend(buckets.get(status, []))
    return rows


def main() -> int:
    load_env()

    parser = argparse.ArgumentParser(
        description=(
            "Report CalTopo team members who are not on the D4H roster "
            "(operational or non-operational)"
        ),
    )
    parser.add_argument(
        "--team-id",
        default=os.environ.get("CALTOPO_TEAM_ID", ""),
        help="CalTopo team id (default: CALTOPO_TEAM_ID)",
    )
    parser.add_argument(
        "--d4h-team",
        default=os.environ.get(
            "D4H_TEAM",
            "York Sunbury Search and Rescue - RSC 11",
        ),
        help="D4H team title filter, case-insensitive substring",
    )
    parser.add_argument(
        "--links",
        default="member_links.json",
        help="Optional legacy d4h_id -> caltopo_id link file",
    )
    parser.add_argument(
        "--exceptions",
        default=(
            os.environ.get("CALTOPO_EXCEPTIONS_PATH")
            or str(DEFAULT_EXCEPTIONS_PATH)
        ),
        help=(
            "Existing audit_exceptions.json (links + ignore). "
            "Default: CALTOPO_EXCEPTIONS_PATH or ./audit_exceptions.json (cwd). "
            "Does not create the file."
        ),
    )
    parser.add_argument(
        "--guided",
        action="store_true",
        help=(
            "Prompt interactively for review rows and save to --exceptions "
            "(file must already exist)"
        ),
    )
    args = parser.parse_args()

    if not args.team_id:
        print("Error: pass --team-id or set CALTOPO_TEAM_ID", file=sys.stderr)
        return 2

    exceptions_path = Path(args.exceptions)
    exceptions_loaded = exceptions_path.is_file()
    links_path = Path(args.links) if args.links else None

    if args.guided and not exceptions_loaded:
        print(
            "Error: --guided requires an existing exceptions file to store links/"
            f"ignores (refusing to create one):\n  {exceptions_path.resolve()}\n"
            "Pass --exceptions PATH or create the JSON first, then re-run.",
            file=sys.stderr,
        )
        return 2

    if not exceptions_loaded:
        print(
            f"Warning: no exceptions file at {exceptions_path.resolve()}; "
            "continuing with empty links/ignores. More CalTopo members may be "
            "marked unmatched / extra_in_caltopo. Pass --exceptions PATH or "
            "place audit_exceptions.json in the cwd.",
            file=sys.stderr,
        )

    try:
        d4h = D4HClient(
            context=os.environ.get("D4H_CONTEXT") or None,
            context_id=os.environ.get("D4H_CONTEXT_ID") or None,
        )
        caltopo = CalTopoClient()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    links = load_member_links(links_path=links_path, exceptions_path=exceptions_path)

    try:
        d4h_roster = build_d4h_audit_roster(d4h, args.d4h_team)
        ct_members = caltopo.get_group_members(args.team_id)
    except (D4HAPIError, CalTopoAPIError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    rows = audit_caltopo_members(
        d4h_roster,
        ct_members,
        links,
        exceptions_path=exceptions_path,
    )
    buckets = summarize(rows)
    print_audit_report(
        d4h_team=args.d4h_team,
        d4h_roster_size=len(d4h_roster),
        caltopo_team_id=args.team_id,
        caltopo_size=len(ct_members),
        links_count=len(links),
        buckets=buckets,
        exceptions_path=exceptions_path,
        exceptions_loaded=exceptions_loaded,
    )

    if args.guided:
        guided_rows = collect_guided_rows(buckets)
        if run_guided_mode(
            guided_rows,
            d4h_client=d4h,
            team_filter=args.d4h_team,
            exceptions_path=exceptions_path,
        ):
            links = load_member_links(links_path=links_path, exceptions_path=exceptions_path)
            rows = audit_caltopo_members(
                d4h_roster,
                ct_members,
                links,
                exceptions_path=exceptions_path,
            )
            buckets = summarize(rows)
            print("\n=== After guided updates ===")
            print_audit_report(
                d4h_team=args.d4h_team,
                d4h_roster_size=len(d4h_roster),
                caltopo_team_id=args.team_id,
                caltopo_size=len(ct_members),
                links_count=len(links),
                buckets=buckets,
                exceptions_path=exceptions_path,
                exceptions_loaded=True,
            )

    review = sum(len(buckets.get(status, [])) for status in REVIEW_STATUSES)
    if review:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
