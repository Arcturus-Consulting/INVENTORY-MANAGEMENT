"""Defensive Oracle Fusion REST client used by the validator.

All public lookups fail soft with ``available=False``. Report-only validation
therefore remains usable during Oracle outages, while the result records that
the live evidence is incomplete.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
logger = logging.getLogger("orion_validator.oracle")

USE_LIVE_ORACLE = os.getenv("USE_LIVE_ORACLE", "false").lower() == "true"
ORACLE_BASE_URL = os.getenv("ORACLE_BASE_URL", "").rstrip("/")
ORACLE_TOKEN_URL = os.getenv("ORACLE_TOKEN_URL", "")
ORACLE_AUTH_MODE = os.getenv("ORACLE_AUTH_MODE", "basic").lower()
ORACLE_USERNAME = os.getenv("ORACLE_USERNAME", "")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD", "")
ORACLE_ORG_CODE = os.getenv("ORACLE_DEFAULT_ORG_CODE", "001")
ORACLE_COST_RESOURCE = os.getenv("ORACLE_COST_RESOURCE", "itemCosts")
REST_VERSION = os.getenv("ORACLE_REST_VERSION", "11.13.18.05")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("ORACLE_REQUEST_TIMEOUT_SECONDS", "15"))
PAGE_SIZE = min(max(int(os.getenv("ORACLE_PAGE_SIZE", "250")), 1), 500)
MAX_PAGES = min(max(int(os.getenv("ORACLE_MAX_PAGES", "20")), 1), 100)

_token_cache = {"token": None, "expires_at": 0.0}
_token_lock = threading.Lock()


def get_oracle_connection_status() -> dict:
    """Return configuration readiness; it deliberately does not make a network call."""
    missing = []
    if not USE_LIVE_ORACLE:
        missing.append("USE_LIVE_ORACLE=true")
    if not ORACLE_BASE_URL or "your-instance" in ORACLE_BASE_URL:
        missing.append("ORACLE_BASE_URL")
    if ORACLE_AUTH_MODE == "basic":
        if not ORACLE_USERNAME:
            missing.append("ORACLE_USERNAME")
        if not ORACLE_PASSWORD:
            missing.append("ORACLE_PASSWORD")
    elif ORACLE_AUTH_MODE == "oauth":
        if not os.getenv("ORACLE_CLIENT_ID"):
            missing.append("ORACLE_CLIENT_ID")
        if not os.getenv("ORACLE_CLIENT_SECRET"):
            missing.append("ORACLE_CLIENT_SECRET")
    else:
        missing.append("ORACLE_AUTH_MODE must be basic or oauth")

    if missing:
        return {
            "configured": False,
            "connected": False,
            "message": "Oracle live checks disabled or incomplete: " + ", ".join(missing) + ".",
        }
    return {
        "configured": True,
        # Kept for older clients. This means ready to attempt, not that a probe ran.
        "connected": True,
        "message": "Oracle is configured; each live check records actual request success.",
    }


def _get_oracle_token() -> str:
    with _token_lock:
        now = time.time()
        if _token_cache["token"] and now < _token_cache["expires_at"] - 30:
            return str(_token_cache["token"])

        import requests

        client_id = os.getenv("ORACLE_CLIENT_ID", "")
        client_secret = os.getenv("ORACLE_CLIENT_SECRET", "")
        token_url = ORACLE_TOKEN_URL or f"{ORACLE_BASE_URL}/oauth/token"
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        response = requests.post(
            token_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": "urn:opc:resource:consumer::all"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = now + float(data.get("expires_in", 3600))
        return str(_token_cache["token"])


def _request_kwargs() -> dict:
    if ORACLE_AUTH_MODE == "basic":
        from requests.auth import HTTPBasicAuth

        return {
            "auth": HTTPBasicAuth(ORACLE_USERNAME, ORACLE_PASSWORD),
            "headers": {"Accept": "application/json"},
        }
    return {
        "headers": {
            "Authorization": f"Bearer {_get_oracle_token()}",
            "Accept": "application/json",
        }
    }


def _query_literal(value: str) -> str:
    return str(value).replace("'", "''")


def _request_failure(response, privilege_hint: str) -> dict | None:
    if response.status_code == 401:
        return {"available": False, "reason": "Oracle returned 401 Unauthorized; verify the integration credentials."}
    if response.status_code == 403:
        return {"available": False, "reason": f"Oracle returned 403 Forbidden; {privilege_hint}."}
    if response.status_code == 404:
        return {"available": False, "reason": "The configured Oracle REST resource was not found."}
    return None


def _fetch_all(resource: str, query: str, privilege_hint: str) -> tuple[list[dict] | None, dict | None]:
    import requests

    url = f"{ORACLE_BASE_URL}/fscmRestApi/resources/{REST_VERSION}/{resource}"
    rows: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        response = requests.get(
            url,
            params={"q": query, "limit": PAGE_SIZE, "offset": offset, "onlyData": "true"},
            timeout=REQUEST_TIMEOUT_SECONDS,
            **_request_kwargs(),
        )
        failure = _request_failure(response, privilege_hint)
        if failure:
            return None, failure
        response.raise_for_status()
        payload = response.json()
        page_rows = payload.get("items", [])
        rows.extend(page_rows)
        if not payload.get("hasMore") or not page_rows:
            return rows, None
        offset += len(page_rows)
    return None, {
        "available": False,
        "reason": f"Oracle pagination exceeded the safety limit of {MAX_PAGES} pages.",
    }


def _safe_request_reason(label: str, exc: Exception) -> str:
    # Do not echo request URLs, credentials, or response bodies into the UI.
    return f"{label} failed ({type(exc).__name__}). Check the backend log for details."


def oracle_check_onhand_qty(item_number: str, org_code: str | None = None) -> dict:
    status = get_oracle_connection_status()
    if not status["configured"]:
        return {"available": False, "reason": status["message"]}

    effective_org = org_code or ORACLE_ORG_CODE
    try:
        query = f"ItemNumber='{_query_literal(item_number)}'"
        rows, failure = _fetch_all(
            "inventoryOnhandBalances",
            query,
            "the user needs permission to view on-hand quantities",
        )
        if failure:
            return failure
        matching = [row for row in rows or [] if str(row.get("OrganizationCode", "")) == effective_org]
        if not matching:
            return {
                "available": False,
                "reason": f"Item '{item_number}' was not returned for inventory organization {effective_org}.",
            }
        total = sum(float(row.get("PrimaryQuantity", 0) or 0) for row in matching)
        return {
            "available": True,
            "onHandQty": total,
            "rowCount": len(matching),
            "orgCode": effective_org,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Oracle on-hand lookup failed for item %s: %s", item_number, exc)
        return {"available": False, "reason": _safe_request_reason("Oracle on-hand lookup", exc)}


def oracle_check_item_cost(
    item_number: str,
    *,
    cost_org_code: str | None = None,
    cost_org_name: str | None = None,
    cost_book: str | None = None,
) -> dict:
    status = get_oracle_connection_status()
    if not status["configured"]:
        return {"available": False, "reason": status["message"]}

    try:
        query = f"ItemNumber='{_query_literal(item_number)}'"
        rows, failure = _fetch_all(
            ORACLE_COST_RESOURCE,
            query,
            "the user needs permission to view item costs",
        )
        if failure:
            if "not found" in failure.get("reason", "").lower():
                failure["reason"] = (
                    f"Cost REST resource '{ORACLE_COST_RESOURCE}' was not found; "
                    "set ORACLE_COST_RESOURCE to the resource exposed by this instance."
                )
            return failure

        candidates = list(rows or [])
        if cost_org_code:
            candidates = [row for row in candidates if row.get("CostOrganizationCode") == cost_org_code]
        elif cost_org_name:
            named = [row for row in candidates if row.get("CostOrganizationName") == cost_org_name]
            if named:
                candidates = named
        if cost_book:
            booked = [row for row in candidates if row.get("CostBook") == cost_book]
            if booked:
                candidates = booked
        if not candidates:
            scope = cost_org_code or cost_org_name or "the report's cost context"
            return {
                "available": False,
                "reason": f"No costed record was returned for item '{item_number}' in {scope}.",
            }

        best_row = max(candidates, key=lambda row: row.get("CostAsOfDate") or "")
        cost = best_row.get("TotalUnitCost")
        if cost is None:
            return {"available": False, "reason": "TotalUnitCost was absent from the Oracle response."}
        return {
            "available": True,
            "unitCost": float(cost),
            "costOrganization": best_row.get("CostOrganizationName"),
            "costOrganizationCode": best_row.get("CostOrganizationCode"),
            "costBook": best_row.get("CostBook"),
            "costAsOfDate": best_row.get("CostAsOfDate"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Oracle cost lookup failed for item %s: %s", item_number, exc)
        return {"available": False, "reason": _safe_request_reason("Oracle cost lookup", exc)}
