"""Deterministic validation engine for Period Inventory Valuation reports."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from oracle_client import oracle_check_item_cost, oracle_check_onhand_qty

QTY_ABS_TOL = Decimal("0.00001")
ORACLE_QTY_ABS_TOL = Decimal("0.01")
ORACLE_COST_ABS_TOL = Decimal("0.0001")
MONEY_ABS_TOL = Decimal("0.02")
DISPLAYED_UNIT_COST_HALF_STEP = Decimal("0.000005")
CURRENCY_HALF_STEP = Decimal("0.005")
MAX_ORACLE_WORKERS = min(max(int(os.getenv("ORACLE_MAX_WORKERS", "6")), 1), 16)


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _close(a: Decimal | None, b: Decimal | None, tolerance: Decimal) -> bool:
    return a is not None and b is not None and abs(a - b) <= tolerance


def _fmt(value) -> str:
    number = _decimal(value)
    if number is None:
        return "N/A"
    text = f"{number:,.5f}".rstrip("0").rstrip(".")
    return text or "0"


def _org_code_from_valuation_unit(valuation_unit: str) -> str | None:
    if not valuation_unit:
        return None
    match = re.search(r"(\d+)\s*$", valuation_unit.replace("\\", "").strip())
    return match.group(1) if match else None


def _cost_org_code_from_valuation_unit(valuation_unit: str) -> str | None:
    cleaned = (valuation_unit or "").replace("\\", "").strip()
    parts = cleaned.split("-")
    if len(parts) >= 3 and parts[-1].isdigit():
        return "-".join(parts[:-1])
    return None


def _value_tolerance(quantity: Decimal) -> Decimal:
    # The report displays Unit Cost to five decimals and Inventory Value to two.
    # This bound permits only the rounding that those displayed precisions imply.
    return max(MONEY_ABS_TOL, abs(quantity) * DISPLAYED_UNIT_COST_HALF_STEP + CURRENCY_HALF_STEP)


def _check_rollforward(row: dict) -> dict:
    opening = _decimal(row.get("opening_quantity")) or Decimal("0")
    receipts = _decimal(row.get("receipts")) or Decimal("0")
    issues = _decimal(row.get("issues")) or Decimal("0")
    closing = _decimal(row.get("closing_quantity"))
    expected = opening + receipts + issues
    if closing is None:
        return {"status": "fail", "detail": "Closing Quantity is missing from the report."}
    difference = closing - expected
    if _close(expected, closing, QTY_ABS_TOL):
        return {
            "status": "pass",
            "detail": (
                f"{_fmt(opening)} + {_fmt(receipts)} + ({_fmt(issues)}) = {_fmt(expected)}, "
                f"matching Closing Quantity {_fmt(closing)}."
            ),
        }
    return {
        "status": "fail",
        "detail": (
            f"Opening {_fmt(opening)} + Receipts {_fmt(receipts)} + Issues {_fmt(issues)} "
            f"= {_fmt(expected)}, but Closing Quantity is {_fmt(closing)} "
            f"(difference {_fmt(difference)})."
        ),
    }


def _check_value_calc(row: dict) -> dict:
    closing = _decimal(row.get("closing_quantity"))
    unit_cost = _decimal(row.get("unit_cost"))
    inventory_value = _decimal(row.get("inventory_value"))
    if closing is None or unit_cost is None or inventory_value is None:
        return {"status": "fail", "detail": "Closing Quantity, Unit Cost, or Inventory Value is missing."}
    expected = closing * unit_cost
    tolerance = _value_tolerance(closing)
    difference = inventory_value - expected
    if _close(expected, inventory_value, tolerance):
        return {
            "status": "pass",
            "detail": (
                f"{_fmt(closing)} x {_fmt(unit_cost)} = {_fmt(expected)}, matching Inventory Value "
                f"{_fmt(inventory_value)} within the {_fmt(tolerance)} display-rounding tolerance."
            ),
        }
    return {
        "status": "fail",
        "detail": (
            f"Closing Quantity {_fmt(closing)} x Unit Cost {_fmt(unit_cost)} = {_fmt(expected)}, "
            f"but Inventory Value is {_fmt(inventory_value)} (difference {_fmt(difference)}; "
            f"allowed {_fmt(tolerance)})."
        ),
    }


def _check_oracle_qty(row: dict, lookup: dict) -> dict:
    if not lookup.get("available"):
        return {"status": "skipped", "detail": lookup.get("reason", "Oracle on-hand lookup unavailable.")}
    closing = _decimal(row.get("closing_quantity"))
    live_qty = _decimal(lookup.get("onHandQty"))
    if closing is None:
        return {"status": "fail", "detail": "Closing Quantity is missing, so live quantity cannot be compared."}
    if _close(closing, live_qty, ORACLE_QTY_ABS_TOL):
        return {
            "status": "pass",
            "detail": (
                f"Oracle live on-hand {_fmt(live_qty)} matches Closing Quantity {_fmt(closing)} "
                f"for organization {lookup.get('orgCode', 'N/A')}."
            ),
        }
    return {
        "status": "fail",
        "detail": (
            f"Closing Quantity is {_fmt(closing)}, but Oracle live on-hand is {_fmt(live_qty)} "
            f"(difference {_fmt(closing - live_qty)})."
        ),
    }


def _check_oracle_cost(row: dict, lookup: dict) -> dict:
    if not lookup.get("available"):
        return {"status": "skipped", "detail": lookup.get("reason", "Oracle cost lookup unavailable.")}
    report_cost = _decimal(row.get("unit_cost"))
    live_cost = _decimal(lookup.get("unitCost"))
    if report_cost is None:
        return {"status": "fail", "detail": "Unit Cost is missing, so live cost cannot be compared."}
    if _close(report_cost, live_cost, ORACLE_COST_ABS_TOL):
        return {
            "status": "pass",
            "detail": f"Oracle live Unit Cost {_fmt(live_cost)} matches report Unit Cost {_fmt(report_cost)}.",
        }
    return {
        "status": "fail",
        "detail": (
            f"Report Unit Cost is {_fmt(report_cost)}, but Oracle live Unit Cost is {_fmt(live_cost)} "
            f"(difference {_fmt(report_cost - live_cost)})."
        ),
    }


def _row_status(checks: dict) -> str:
    statuses = {check["status"] for check in checks.values()}
    if "fail" in statuses:
        return "fail"
    if "skipped" in statuses:
        return "incomplete"
    return "pass"


def _run_live_lookups(rows: list[dict], metadata: dict) -> tuple[dict, dict]:
    cost_org_name = metadata.get("Cost Organization")
    cost_book = metadata.get("Cost Book")
    qty_keys = {
        (str(row.get("item", "")), _org_code_from_valuation_unit(row.get("valuation_unit", "")))
        for row in rows
    }
    cost_keys = {
        (
            str(row.get("item", "")),
            _cost_org_code_from_valuation_unit(row.get("valuation_unit", "")),
            cost_org_name,
            cost_book,
        )
        for row in rows
    }
    qty_results: dict[tuple, dict] = {}
    cost_results: dict[tuple, dict] = {}

    with ThreadPoolExecutor(max_workers=MAX_ORACLE_WORKERS, thread_name_prefix="orion-oracle") as pool:
        futures = {}
        for item, org_code in qty_keys:
            future = pool.submit(oracle_check_onhand_qty, item, org_code=org_code)
            futures[future] = ("qty", (item, org_code))
        for item, org_code, org_name, book in cost_keys:
            future = pool.submit(
                oracle_check_item_cost,
                item,
                cost_org_code=org_code,
                cost_org_name=org_name,
                cost_book=book,
            )
            futures[future] = ("cost", (item, org_code, org_name, book))
        for future in as_completed(futures):
            kind, key = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # defensive guard around client implementations
                result = {"available": False, "reason": f"Oracle lookup failed ({type(exc).__name__})."}
            (qty_results if kind == "qty" else cost_results)[key] = result
    return qty_results, cost_results


def _check_summary(results: list[dict]) -> dict:
    summary = {}
    for name in ("rollforward", "value_calc", "oracle_qty", "oracle_cost"):
        counts = {"pass": 0, "fail": 0, "skipped": 0}
        for result in results:
            counts[result["checks"][name]["status"]] += 1
        summary[name] = counts
    return summary


def validate_report(parsed: dict) -> dict:
    rows = parsed["rows"]
    metadata = parsed.get("metadata") or {}
    total_row = parsed.get("total_row")
    qty_results, cost_results = _run_live_lookups(rows, metadata)

    results = []
    running_value_sum = Decimal("0")
    for index, row in enumerate(rows, start=1):
        item = str(row.get("item", ""))
        org_code = _org_code_from_valuation_unit(row.get("valuation_unit", ""))
        cost_key = (
            item,
            _cost_org_code_from_valuation_unit(row.get("valuation_unit", "")),
            metadata.get("Cost Organization"),
            metadata.get("Cost Book"),
        )
        checks = {
            "rollforward": _check_rollforward(row),
            "value_calc": _check_value_calc(row),
            "oracle_qty": _check_oracle_qty(row, qty_results[(item, org_code)]),
            "oracle_cost": _check_oracle_cost(row, cost_results[cost_key]),
        }
        inventory_value = _decimal(row.get("inventory_value"))
        if inventory_value is not None:
            running_value_sum += inventory_value
        results.append(
            {
                "row_number": index,
                "row_key": f"{row.get('valuation_unit', '')}::{item}",
                "item": item,
                "description": row.get("description", ""),
                "valuation_unit": row.get("valuation_unit", ""),
                "row_data": row,
                "checks": checks,
                "status": _row_status(checks),
            }
        )

    if total_row is not None and _decimal(total_row.get("inventory_value")) is not None:
        reported_total = _decimal(total_row["inventory_value"])
        if _close(running_value_sum, reported_total, MONEY_ABS_TOL):
            total_check = {
                "status": "pass",
                "detail": (
                    f"Sum of {len(rows)} row Inventory Values {_fmt(running_value_sum)} matches "
                    f"the report Total {_fmt(reported_total)}."
                ),
            }
        else:
            total_check = {
                "status": "fail",
                "detail": (
                    f"Sum of {len(rows)} row Inventory Values is {_fmt(running_value_sum)}, but "
                    f"the report Total is {_fmt(reported_total)} "
                    f"(difference {_fmt(reported_total - running_value_sum)})."
                ),
            }
    else:
        total_check = {"status": "skipped", "detail": "No report Total row was available for tie-out."}

    pass_count = sum(result["status"] == "pass" for result in results)
    fail_count = sum(result["status"] == "fail" for result in results)
    incomplete_count = sum(result["status"] == "incomplete" for result in results)
    checks = _check_summary(results)
    skipped_check_count = sum(counts["skipped"] for counts in checks.values())
    live_completed = sum(checks[name]["pass"] + checks[name]["fail"] for name in ("oracle_qty", "oracle_cost"))
    live_expected = len(rows) * 2

    if fail_count or total_check["status"] == "fail":
        overall_status = "fail"
    elif incomplete_count or total_check["status"] == "skipped":
        overall_status = "incomplete"
    else:
        overall_status = "pass"

    return {
        "run_id": str(uuid4()),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall_status,
        "row_count": len(results),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "incomplete_count": incomplete_count,
        "skipped_count": incomplete_count,
        "skipped_check_count": skipped_check_count,
        "live_coverage": {"completed": live_completed, "expected": live_expected},
        "check_summary": checks,
        "computed_value_sum": float(running_value_sum),
        "total_check": total_check,
        "results": results,
    }
