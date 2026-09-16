"""
ORION Validator - one-off helper (v3).

The versioned catalog root (.../resources/11.13.18.05) only returns a small
HATEOAS envelope (self/describedby links), not the full resource list, on
this instance. This version:

  1. Prints those links plainly so we can see what's actually there.
  2. Tries the un-versioned root (.../fscmRestApi/resources), which on most
     Oracle Fusion instances DOES return the full ~500+ resource catalog.
  3. As a fallback that doesn't depend on the catalog at all, directly probes
     a shortlist of the real, documented Oracle Cost Management REST resource
     names and reports which ones respond (200/204) vs. don't exist (404) vs.
     exist but need different privileges (401/403).

Run from the backend/ folder:
    python discover_cost_resource.py
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
AUTH = HTTPBasicAuth(USERNAME, PASSWORD)

if not BASE_URL or not USERNAME or not PASSWORD:
    raise SystemExit("ORACLE_BASE_URL / ORACLE_USERNAME / ORACLE_PASSWORD missing from .env")


def get(url, **kwargs):
    return requests.get(url, headers={"Accept": "application/json"}, auth=AUTH, timeout=30, **kwargs)


# --- Step 1: show the versioned root's links plainly ---
versioned_url = f"{BASE_URL}/fscmRestApi/resources/{REST_VERSION}"
print(f"[1] Versioned root: {versioned_url}")
resp = get(versioned_url)
print("    HTTP", resp.status_code)
data = resp.json()
for link in data.get("links", []):
    print("    link:", link.get("name"), "->", link.get("href"))
print()

# --- Step 2: try the un-versioned catalog root ---
root_url = f"{BASE_URL}/fscmRestApi/resources"
print(f"[2] Un-versioned root: {root_url}")
resp2 = get(root_url)
print("    HTTP", resp2.status_code)
try:
    data2 = resp2.json()
    print("    Top-level keys:", list(data2.keys()))
    items2 = data2.get("items", [])
    print("    Resource count:", len(items2))
    if items2:
        names2 = sorted(
            item.get("name", "") for item in items2
            if any(kw in (item.get("name") or "").lower() for kw in ("cost", "item"))
        )
        print("    Candidates containing 'cost'/'item':")
        for n in names2:
            print("     -", n)
except Exception as exc:
    print("    (not JSON or unexpected shape):", exc)
print()

# --- Step 3: direct probe of known/likely Oracle Cost Management resource names ---
candidates = [
    "costItemCosts",
    "itemCosts",
    "costElements",
    "costOrganizations",
    "costBooks",
    "costScenarios",
    "receiptAccountingDistributions",
    "costAccountingDistributions",
    "itemCostProfiles",
    "landedCostInvoices",
]

print("[3] Direct probe of known Cost Management resource names:")
print("-" * 70)
for name in candidates:
    url = f"{BASE_URL}/fscmRestApi/resources/{REST_VERSION}/{name}"
    try:
        r = get(url, params={"limit": 1})
        tag = {
            200: "EXISTS (200) <-- usable",
            204: "EXISTS but empty (204)",
            401: "exists, but 401 Unauthorized",
            403: "exists, but 403 Forbidden (missing privilege)",
            404: "404 Not Found",
        }.get(r.status_code, f"HTTP {r.status_code}")
        print(f" {name:35s} {tag}")
    except Exception as exc:
        print(f" {name:35s} ERROR: {exc}")
print("-" * 70)
print(
    "\nSet ORACLE_COST_RESOURCE in backend/.env to whichever name above shows "
    "'EXISTS (200)', then restart python main.py.\n"
    "If everything in step 3 shows 404, but step 2 found real resources, use "
    "the closest match from step 2's candidate list instead."
)