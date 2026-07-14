"""
CalTopo API client.

Official v1 endpoints use HMAC-signed requests (service account credential id +
secret). v0 endpoints are undocumented but use the same signing scheme in practice.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import time
from typing import Any
from urllib.parse import urlencode

import requests

DEFAULT_TIMEOUT_MS = 2 * 60 * 1000
BASE_URL = "https://caltopo.com"


class CalTopoClient:
    def __init__(
        self,
        credential_id: str | None = None,
        credential_secret: str | None = None,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ):
        self.credential_id = credential_id or os.environ.get("CALTOPO_CREDENTIAL_ID", "")
        self.credential_secret = (
            credential_secret or os.environ.get("CALTOPO_CREDENTIAL_SECRET", "")
        )
        if not self.credential_id or not self.credential_secret:
            raise ValueError(
                "CalTopo credentials required: pass credential_id/credential_secret "
                "or set CALTOPO_CREDENTIAL_ID and CALTOPO_CREDENTIAL_SECRET"
            )
        self.timeout_ms = timeout_ms

    @staticmethod
    def sign(
        method: str,
        endpoint: str,
        expires: int,
        payload_string: str,
        credential_secret: str,
    ) -> str:
        """HMAC-SHA256 signature per CalTopo team API docs."""
        message = f"{method.upper()} {endpoint}\n{expires}\n{payload_string}"
        secret = base64.b64decode(credential_secret)
        digest = hmac.new(secret, message.encode(), "sha256").digest()
        return base64.b64encode(digest).decode()

    def request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        return_full_response: bool = False,
    ) -> Any:
        """
        Send a signed CalTopo API request.

        endpoint is the path only, e.g. /api/v1/acct/PJ04RF/since/0
        """
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint

        method = method.upper()
        payload_string = json.dumps(payload) if payload is not None else ""
        expires = int(time.time() * 1000) + self.timeout_ms
        signature = self.sign(
            method,
            endpoint,
            expires,
            payload_string,
            self.credential_secret,
        )

        params = {
            "id": self.credential_id,
            "expires": expires,
            "signature": signature,
        }

        url = BASE_URL + endpoint
        if method == "POST" and payload is not None:
            params["json"] = payload_string
            resp = requests.post(
                url,
                data=urlencode(params),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=60,
            )
        else:
            resp = requests.request(
                method,
                url,
                params=params,
                timeout=60,
            )

        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError:
            raise CalTopoAPIError(resp.status_code, resp.text) from None

        if not resp.text:
            return None

        body = resp.json()
        if return_full_response:
            return body
        return body.get("result")

    # --- Official v1 API ---

    def get_account_data(
        self,
        team_id: str,
        since_ms: int = 0,
    ) -> dict[str, Any]:
        """GET /api/v1/acct/{team_id}/since/{timestamp}"""
        result = self.request("GET", f"/api/v1/acct/{team_id}/since/{since_ms}")
        if not isinstance(result, dict):
            raise CalTopoAPIError(0, f"Expected account dict, got {type(result)}")
        return result

    def get_map_data(self, map_id: str, since_ms: int = 0) -> dict[str, Any]:
        """GET /api/v1/map/{map_id}/since/{timestamp}"""
        result = self.request("GET", f"/api/v1/map/{map_id}/since/{since_ms}")
        if not isinstance(result, dict):
            raise CalTopoAPIError(0, f"Expected map dict, got {type(result)}")
        return result

    # --- Undocumented v0 API ---

    def get_group_members(self, team_id: str) -> list[dict[str, Any]]:
        """
        GET /api/v0/group/{team_id}/members

        Undocumented; returns team member list with id, email, fullName, permission.
        """
        result = self.request(
            "GET",
            f"/api/v0/group/{team_id}/members",
            return_full_response=True,
        )
        if not isinstance(result, dict):
            raise CalTopoAPIError(0, f"Expected response dict, got {type(result)}")

        inner = result.get("result")
        if isinstance(inner, dict) and "list" in inner:
            members = inner["list"]
        elif isinstance(inner, list):
            members = inner
        else:
            raise CalTopoAPIError(
                0,
                f"Unexpected members payload: {json.dumps(result)[:500]}",
            )

        if not isinstance(members, list):
            raise CalTopoAPIError(0, f"Expected member list, got {type(members)}")
        return members

    @staticmethod
    def member_email(member: dict[str, Any]) -> str:
        return (member.get("email") or "").strip().lower()

    @staticmethod
    def member_name(member: dict[str, Any]) -> str:
        return (member.get("fullName") or member.get("name") or "").strip()


class CalTopoAPIError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"CalTopo API {status_code}: {body[:500]}")
