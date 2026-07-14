# caltopo-d4h-role-sync

Audit **CalTopo team members** against a **D4H** roster for one Ground SAR team.

D4H is the membership source of truth. This project flags CalTopo accounts that cannot be matched to an OPERATIONAL or NON_OPERATIONAL D4H member. It does **not** report D4H people missing from CalTopo.

## Shared D4H client

The D4H API lives in the sibling package [`sar_d4h_client`](../sar_d4h_client/). `requirements.txt` installs it editable:

```
-e ../sar_d4h_client
```

Open `sar_d4h_client` in the same Cursor workspace when changing D4H client behavior. See that repo’s `README.md` and `AGENTS.md`.

Local `match_utils.py` re-exports name helpers from `name_utils` in the shared package.

## Prerequisites

- Python 3.9+
- Sibling checkout of `../sar_d4h_client`
- D4H PAT + CalTopo service-account credentials

## Setup

```powershell
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` (see table below).

| Variable | Purpose |
|----------|---------|
| `D4H_PAT` | D4H API token |
| `D4H_CONTEXT` / `D4H_CONTEXT_ID` | Optional fixed API context |
| `CALTOPO_CREDENTIAL_ID` / `CALTOPO_CREDENTIAL_SECRET` | Team service account |
| `CALTOPO_TEAM_ID` | CalTopo team id (e.g. `PJ04RF`) |
| `D4H_TEAM` | D4H team title substring filter |

## Run

```powershell
python audit_members.py
python audit_members.py --guided
python caltopo_probe.py
```

| Flag | Purpose |
|------|---------|
| `--team-id` | CalTopo team (default: `CALTOPO_TEAM_ID`) |
| `--d4h-team` | D4H team title filter (default: `D4H_TEAM`) |
| `--exceptions` | Links + ignore file (`audit_exceptions.json`) |
| `--guided` | Interactively resolve review rows into exceptions |

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
