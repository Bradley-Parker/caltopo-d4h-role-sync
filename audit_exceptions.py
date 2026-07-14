"""
Static CalTopo exception rules (shared/service accounts).

Runtime also merges ignore entries from audit_exceptions.json; see audit_store.py.
Guided mode can append to that JSON file without editing this module.
"""

from __future__ import annotations

from match_utils import normalize_name_loose

EXCEPTION_EMAILS = frozenset(
    {
        "president@sarnbres.ca",
        "saric@yssr.ca",
        "ops-pc@yssr.ca",
    }
)

EXCEPTION_NAMES = frozenset(
    {
        normalize_name_loose("President NBGSARA"),
        normalize_name_loose("YSSR SM LAPTOP"),
        normalize_name_loose("YSSR OPS PC"),
    }
)

EXCEPTION_CALTOPO_IDS = frozenset(
    {
        "N4DJL8", #NBGSARA PRes
        "DQ4KB0", # YSSR SM Laptop
        "SUF80K", # YSSR OPS PC
    }
)
