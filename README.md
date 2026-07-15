# caltopo-d4h-role-sync

Audit **CalTopo team members** against a **D4H** roster for one Ground SAR team.

D4H is the membership source of truth. This project flags CalTopo accounts that cannot be matched to an OPERATIONAL or NON_OPERATIONAL D4H member. It does **not** report D4H people missing from CalTopo.

## Shared D4H client

The D4H API lives in the sibling package [`d4h_client`](../d4h_client/). `requirements.txt` installs it editable:

```
-e ../d4h_client
```

Local `match_utils.py` re-exports name helpers from `name_utils` in the shared package.

## Prerequisites

- Python 3.9+
- Sibling checkout of `../d4h_client`
- D4H PAT + CalTopo service-account credentials

## Setup

```powershell
pip install -r requirements.txt
copy .env.example .env
copy audit_exceptions.example.json audit_exceptions.json   # optional local-only
```

Edit `.env` next to this repo’s scripts (loaded via `env_loader.py` from the
package directory, not your shell cwd — so it works standalone and when
invoked from `sar_service_coordinator`).

**Org links/ignores** are operational config. Resolution order for the JSON path:

1. `--exceptions PATH`
2. `CALTOPO_EXCEPTIONS_PATH`
3. `./audit_exceptions.json` (cwd only — not the package directory)

The tool never creates that file. If it is missing, report mode warns and
continues with empty links/ignores (more unmatched). `--guided` refuses to run
until an existing file is provided (create it yourself or use the coordinator
copy under `sar_service_coordinator/config/caltopo/`).

```powershell
# preferred: same map the coordinator uses
python audit_members.py --exceptions ..\sar_service_coordinator\config\caltopo\audit_exceptions.json

# or put audit_exceptions.json in the cwd (gitignored local copy)
copy audit_exceptions.example.json audit_exceptions.json
python audit_members.py
```

| Variable | Purpose |
|----------|---------|
| `D4H_PAT` | D4H API token |
| `D4H_CONTEXT` / `D4H_CONTEXT_ID` | Optional fixed API context |
| `CALTOPO_CREDENTIAL_ID` / `CALTOPO_CREDENTIAL_SECRET` | Team service account |
| `CALTOPO_TEAM_ID` | CalTopo team id (e.g. `AZ001`) |
| `D4H_TEAM` | D4H team title substring filter |

## Run

```powershell
python audit_members.py --exceptions ..\sar_service_coordinator\config\caltopo\audit_exceptions.json
python audit_members.py --guided --exceptions ..\sar_service_coordinator\config\caltopo\audit_exceptions.json
python caltopo_probe.py
```

| Flag | Purpose |
|------|---------|
| `--team-id` | CalTopo team (default: `CALTOPO_TEAM_ID`) |
| `--d4h-team` | D4H team title filter (default: `D4H_TEAM`) |
| `--exceptions` | Existing links + ignore JSON (`CALTOPO_EXCEPTIONS_PATH` or `./audit_exceptions.json`) |
| `--guided` | Interactive resolve into that file (must already exist; never auto-created) |

Exit codes: `0` clean, `1` rows need review, `2` config/API failure.

## Matching order (CalTopo → D4H)

1. Stored link (`audit_exceptions.json` / legacy `member_links.json`)
2. Email
3. Loose name keys
4. Else `extra_in_caltopo` (operator action)

## File index

| File | Role |
|------|------|
| `audit_members.py` | Main audit CLI |
| `caltopo_client.py` | CalTopo signed API client (local; not yet shared) |
| `caltopo_probe.py` | Auth / shape probe |
| `audit_store.py` / `audit_exceptions.py` | Exceptions + links persistence |
| `match_utils.py` | Re-export of shared `name_utils` |
| `env_loader.py` | `.env` loader |
| `../sar_d4h_client/` | Shared D4H client + probe |
