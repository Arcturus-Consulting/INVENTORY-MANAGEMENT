"""
ORION Validator - one-off diagnostic (v2 - queries by CostOrganizationCode
directly, since we already confirmed that field name from an earlier itemCosts
sample row, and the costOrganizations resource's Id field name turned out to
be something other than what we guessed).

Run from the backend/ folder:
    python diagnose_e360ch_cost.py
"""

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
E360CH_COST_ORG_CODE = "E360CH-001"  # confirmed real code from the costOrganizations listing


def get(url, **kwargs):
    return requests.get(url, headers={"Accept": "application/json"}, auth=AUTH, timeout=30, **kwargs)


print("=" * 70)
print(f"[1] itemCosts filtered to CostOrganizationCode='{E360CH_COST_ORG_CODE}' (no item filter)")
print("=" * 70)
cost_url = f"{BASE_URL}/fscmRestApi/resources/{REST_VERSION}/{COST_RESOURCE}"
resp = get(cost_url, params={"q": f"CostOrganizationCode='{E360CH_COST_ORG_CODE}'", "limit": 10})
print("HTTP", resp.status_code)
if resp.status_code == 200:
    rows = resp.json().get("items", [])
    print(f"Rows found: {len(rows)}\n")
    for r in rows[:10]:
        print(f"  - ItemNumber={r.get('ItemNumber')!r}  TotalUnitCost={r.get('TotalUnitCost')!r}  CostAsOfDate={r.get('CostAsOfDate')!r}")
else:
    print("Response body (first 500 chars):")
    print(resp.text[:500])

print()
print("=" * 70)
print("[2] itemCosts by description text match ('Wrist'), no filters at all")
print("=" * 70)
resp2 = get(cost_url, params={"q": "ItemDescription LIKE '%Wrist%'", "limit": 10})
print("HTTP", resp2.status_code)
if resp2.status_code == 200:
    rows2 = resp2.json().get("items", [])
    print(f"Rows found: {len(rows2)}\n")
    for r in rows2[:10]:
        print(f"  - ItemNumber={r.get('ItemNumber')!r}  CostOrganizationCode={r.get('CostOrganizationCode')!r}  TotalUnitCost={r.get('TotalUnitCost')!r}")
else:
    print("Response body (first 500 chars):")
    print(resp2.text[:500])

print()
print("=" * 70)
print("[3] itemCosts totally unfiltered - just the first 5 rows that exist at all")
print("=" * 70)
resp3 = get(cost_url, params={"limit": 5})
print("HTTP", resp3.status_code)
if resp3.status_code == 200:
    rows3 = resp3.json().get("items", [])
    print(f"Rows found: {len(rows3)}\n")
    for r in rows3[:5]:
        print(f"  - ItemNumber={r.get('ItemNumber')!r}  CostOrganizationCode={r.get('CostOrganizationCode')!r}  TotalUnitCost={r.get('TotalUnitCost')!r}")
    print("\nDistinct CostOrganizationCode values seen in this unfiltered sample:")
    print(sorted(set(r.get("CostOrganizationCode") for r in rows3)))
else:
    print(resp3.text[:500])

print()
print("=" * 70)
print("CONCLUSION")
print("=" * 70)
print(
    "If [1] shows 0 rows but [3] shows real rows from OTHER cost orgs (e.g. FR-001),\n"
    "that confirms E360CH-001 has no cost snapshots in itemCosts on this instance -\n"
    "Check 4 correctly stays SKIPPED for this report, that's accurate, not a bug.\n"
    "If [1] DOES show rows, paste this back and we'll wire that filter into\n"
    "oracle_check_item_cost properly."
)