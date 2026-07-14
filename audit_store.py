"""
Load and save audit persistence files (JSON + static Python exceptions).

Guided mode writes confirmed D4H id -> CalTopo id links to audit_exceptions.json.
Legacy member_links.json is still read if present. Static shared-account rules live in
audit_exceptions.py and are merged with JSON "ignore" entries at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from audit_exceptions import (
    EXCEPTION_CALTOPO_IDS as STATIC_CALTOPO_IDS,
    EXCEPTION_EMAILS as STATIC_EMAILS,
    EXCEPTION_NAMES as STATIC_NAMES,
)
from caltopo_client import CalTopoClient
from match_utils import normalize_name_loose

DEFAULT_EXCEPTIONS_PATH = Path("audit_exceptions.json")


def default_exceptions_document() -> dict[str, Any]:
    return {
        "links": {},
        "ignore": {
            "caltopo_ids": [],
            "emails": [],
            "names": [],
        },
    }


def load_exceptions_document(path: Path = DEFAULT_EXCEPTIONS_PATH) -> dict[str, Any]:
    if not path.is_file():
        return default_exceptions_document()
    raw = json.loads(path.read_text(encoding="utf-8"))
    doc = default_exceptions_document()
    if isinstance(raw.get("links"), dict):
        doc["links"] = raw["links"]
    ignore = raw.get("ignore")
    if isinstance(ignore, dict):
        for key in ("caltopo_ids", "emails", "names"):
            values = ignore.get(key)
            if isinstance(values, list):
                doc["ignore"][key] = values
    return doc


def save_exceptions_document(doc: dict[str, Any], path: Path = DEFAULT_EXCEPTIONS_PATH) -> None:
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_member_links(
    *,
    links_path: Path | None = None,
    exceptions_path: Path = DEFAULT_EXCEPTIONS_PATH,
) -> dict[str, str]:
    """
    Merge d4h_id -> caltopo_id mappings from audit_exceptions.json and optional
    legacy member_links.json.
    """
    links: dict[str, str] = {}

    if links_path and links_path.is_file():
        raw = json.loads(links_path.read_text(encoding="utf-8"))
        links.update(_parse_links_mapping(raw))

    doc = load_exceptions_document(exceptions_path)
    links.update(_parse_links_mapping(doc.get("links", {})))
    return links


def _parse_links_mapping(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    links: dict[str, str] = {}
    for d4h_id, value in raw.items():
        if isinstance(value, str):
            links[str(d4h_id)] = value
        elif isinstance(value, dict) and value.get("caltopo_id"):
            links[str(d4h_id)] = str(value["caltopo_id"])
    return links


def load_ignore_rules(
    exceptions_path: Path = DEFAULT_EXCEPTIONS_PATH,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Merge static audit_exceptions.py rules with JSON ignore entries."""
    doc = load_exceptions_document(exceptions_path)
    ignore = doc.get("ignore") or {}

    emails = set(STATIC_EMAILS)
    for value in ignore.get("emails") or []:
        emails.add(str(value).strip().lower())

    names = set(STATIC_NAMES)
    for value in ignore.get("names") or []:
        names.add(normalize_name_loose(str(value)))

    caltopo_ids = {value.upper() for value in STATIC_CALTOPO_IDS}
    for value in ignore.get("caltopo_ids") or []:
        caltopo_ids.add(str(value).strip().upper())

    return frozenset(emails), frozenset(names), frozenset(caltopo_ids)


def is_audit_exception(
    member: dict[str, Any],
    *,
    exceptions_path: Path = DEFAULT_EXCEPTIONS_PATH,
) -> bool:
    emails, names, caltopo_ids = load_ignore_rules(exceptions_path)

    email = (member.get("email") or "").strip().lower()
    if email in emails:
        return True

    name = normalize_name_loose(member.get("fullName") or member.get("name") or "")
    if name in names:
        return True

    member_id = (member.get("id") or "").strip().upper()
    if member_id in caltopo_ids:
        return True

    return False


def append_tracked_link(
    d4h_id: str,
    caltopo_id: str,
    *,
    note: str = "",
    exceptions_path: Path = DEFAULT_EXCEPTIONS_PATH,
) -> None:
    """Persist a confirmed D4H member id -> CalTopo member id mapping."""
    doc = load_exceptions_document(exceptions_path)
    links = doc.setdefault("links", {})
    entry: dict[str, str] = {"caltopo_id": caltopo_id}
    if note:
        entry["note"] = note
    links[str(d4h_id)] = entry
    save_exceptions_document(doc, exceptions_path)


def append_ignore_entry(
    caltopo_member: dict[str, Any],
    *,
    note: str = "",
    exceptions_path: Path = DEFAULT_EXCEPTIONS_PATH,
) -> None:
    """Persist a CalTopo account that should never be flagged as extra."""
    doc = load_exceptions_document(exceptions_path)
    ignore = doc.setdefault("ignore", default_exceptions_document()["ignore"])

    caltopo_id = (caltopo_member.get("id") or "").strip().upper()
    if caltopo_id and caltopo_id not in {v.upper() for v in ignore.get("caltopo_ids", [])}:
        ignore.setdefault("caltopo_ids", []).append(caltopo_id)

    email = CalTopoClient.member_email(caltopo_member)
    if email and email not in {str(v).lower() for v in ignore.get("emails", [])}:
        ignore.setdefault("emails", []).append(email)

    if note:
        ignore.setdefault("notes", []).append(
            {
                "caltopo_id": caltopo_id,
                "email": email,
                "note": note,
            }
        )

    save_exceptions_document(doc, exceptions_path)
