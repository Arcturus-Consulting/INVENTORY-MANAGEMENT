"""
ORION Validator - one-off helper.

Fetches a single unfiltered row from the itemCosts resource and prints its
field names + values, so we know the correct field to filter by (item number)
and the correct field to read (unit cost) - rather than guessing.

Run from the backend/ folder:
    python inspect_item_costs.py
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
RESOURCE = os.getenv("ORACLE_COST_RESOURCE", "itemCosts").strip().strip('"')
AUTH = HTTPBasicAuth(USERNAME, PASSWORD)

url = f"{BASE_URL}/fscmRestApi/resources/{REST_VERSION}/{RESOURCE}"
print(f"Fetching: {url}?limit=1\n")

resp = requests.get(
    url,
    headers={"Accept": "application/json"},
    auth=AUTH,
    params={"limit": 1},
    timeout=30,
)
print("HTTP status:", resp.status_code)
resp.raise_for_status()

data = resp.json()
items = data.get("items", [])
print("Row count returned:", len(items))

if not items:
    print("\nNo rows at all (resource is empty for this user/context). "
          "Try again with a specific item you know exists, e.g. add "
          "params={'q': \"ItemNumber='ESB500000'\"} - but first we need a "
          "field name, so an empty result here means we should check the "
          "'describe' endpoint instead:")
    print(f"  {url}/describe")
else:
    row = items[0]
    print("\nField names and sample values on this resource:")
    print("-" * 60)
    for key, value in row.items():
        if key == "links":
            continue
        print(f" {key:35s} = {value!r}")
    print("-" * 60)
    print(
        "\nLook for the field that holds the item number (likely 'ItemNumber' "
        "or 'InventoryItemNumber') and the field that holds unit cost (likely "
        "'ItemCost', 'UnitCost', or 'RollupCost'). Tell me both field names."
    )