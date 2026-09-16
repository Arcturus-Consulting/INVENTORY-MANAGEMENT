"""
ORION Validator - one-off diagnostic.

Our code filters inventoryOnhandBalances rows to OrganizationCode == "001"
(inherited from the original reference project's dataset). This report is
from a different Oracle demo dataset (E360CH OPERATIONS), which likely uses
a different organization code entirely - so real rows may be coming back
from Oracle and getting silently thrown away by that filter.

This script queries Oracle for a few known-real item numbers from the E360CH
report WITHOUT any org filter, and prints every row exactly as Oracle returns
it - so we can see the real OrganizationCode (and confirm whether itemCosts
returns anything for these items too).

Run from the backend/ folder:
    python diagnose_e360ch.py
"""

import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

load_dotenv(Path(__file__).resolve().parent / ".env")

BASE_URL = os.getenv("ORACLE_BASE_URL", "").strip().strip('"')
USERNAME = os.getenv("ORACLE_USERNAME", "").strip().strip('"')
PASSWORD = os.getenv("ORACLE_PASSWORD", "").strip().strip('"')
REST_VERSION = "11.13.18.05"
COST_RESOURCE = os.getenv("ORACLE_COST_RESOURCE", "itemCosts").strip().strip('"')
AUTH = HTTPBasicAuth(USERNAME, PASSWORD)

TEST_ITEMS = ["ESB500000", "ECM500000", "ST1040"]


def get(url, **kwargs):
    return requests.get(url, headers={"Accept": "application/json"}, auth=AUTH, timeout=30, **kwargs)


for item in TEST_ITEMS:
    print("=" * 70)
    print(f"ITEM: {item}")
    print("=" * 70)

    # --- Onhand balances, no org filter at all ---
    onhand_url = f"{BASE_URL}/fscmRestApi/resources/{REST_VERSION}/inventoryOnhandBalances"
    resp = get(onhand_url, params={"q": f"ItemNumber='{item}'"})
    print(f"\n[inventoryOnhandBalances] HTTP {resp.status_code}")
    try:
        rows = resp.json().get("items", [])
        print(f"  Row count: {len(rows)}")
        for r in rows:
            print(
                f"   - OrganizationCode={r.get('OrganizationCode')!r}  "
                f"OrganizationName={r.get('OrganizationName')!r}  "
                f"SubinventoryCode={r.get('SubinventoryCode')!r}  "
                f"PrimaryQuantity={r.get('PrimaryQuantity')!r}"
            )
    except Exception as exc:
        print("  (could not parse response):", exc)
        print("  raw:", resp.text[:500])

    # --- Item costs, no org filter ---
    cost_url = f"{BASE_URL}/fscmRestApi/resources/{REST_VERSION}/{COST_RESOURCE}"
    resp2 = get(cost_url, params={"q": f"ItemNumber='{item}'"})
    print(f"\n[{COST_RESOURCE}] HTTP {resp2.status_code}")
    try:
        rows2 = resp2.json().get("items", [])
        print(f"  Row count: {len(rows2)}")
        for r in rows2:
            print(
                f"   - CostOrganizationCode={r.get('CostOrganizationCode')!r}  "
                f"CostBook={r.get('CostBook')!r}  "
                f"TotalUnitCost={r.get('TotalUnitCost')!r}  "
                f"CostAsOfDate={r.get('CostAsOfDate')!r}"
            )
    except Exception as exc:
        print("  (could not parse response):", exc)
        print("  raw:", resp2.text[:500])

    print()

print("=" * 70)
print(
    "Whatever OrganizationCode shows up above (if any rows came back) is what "
    "ORACLE_DEFAULT_ORG_CODE in .env should be set to. If a given item returns "
    "0 rows in BOTH sections even without any filter, that specific item simply "
    "isn't tracked in Oracle's live inventory/costing tables on this instance "
    "(common for demo/sample data) - in that case SKIPPED is the correct, "
    "honest answer for that item, not a bug."
)