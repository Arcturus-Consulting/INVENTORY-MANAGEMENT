# ORION Validator — Phase 1 (Period Inventory Valuation Report)

A deterministic, **no-LLM** validator for Oracle's Period Inventory Valuation
Report. The user drives it with a fixed set of typed commands (ATM-style) —
every response is produced by plain Python arithmetic and, optionally, a live
Oracle REST cross-check. No token usage, no API cost, no non-determinism.

## What it checks, per item row

| # | Check | Logic |
|---|---|---|
| ① | Quantity roll-forward | `Opening + Receipts + Issues == Closing Quantity` (Issues is already negative in Oracle's export) |
| ② | Inventory value calculation | `Closing Quantity × Unit Cost == Inventory Value` |
| ③ | Oracle live on-hand cross-check | Report's Closing Quantity vs. Oracle's current live on-hand balance (org 001) |
| ④ | Oracle live cost cross-check | Report's Unit Cost vs. Oracle's current live costed unit cost |

Plus one report-level check:

| # | Check | Logic |
|---|---|---|
| ⑤ | Total tie-out | Sum of every row's Inventory Value vs. the report's own `Total` row |

Checks ③ and ④ automatically show as **SKIPPED** (not failed) whenever Oracle
is unreachable or a specific privilege/resource isn't available — the tool
never crashes or blocks on Oracle being down. Checks ① ② ⑤ always run,
since they only need the uploaded file itself.

## Project layout

```
orion-validator-phase1/
├── run.sh                    # one-command start
├── backend/
│   ├── main.py                # FastAPI app: auth, upload, command, export routes
│   ├── report_parser.py       # finds the header row by content, extracts rows + Total row
│   ├── validator.py            # the 5 checks above, in plain Python
│   ├── oracle_client.py        # live Oracle REST cross-checks (fails soft, never crashes)
│   ├── commands.py             # the ATM-style command engine + typo/fuzzy correction
│   ├── requirements.txt
│   └── .env                    # reuses the same SCM_IMPL Oracle instance/creds you gave me
└── frontend/
    └── index.html               # command-driven chat UI (same visual style as the reference app)
```

## How to run it

```bash
cd orion-validator-phase1
chmod +x run.sh
./run.sh
```

Then open **http://localhost:8000** and sign in with `testing` / `123456`.

## How to test it, step by step

1. Attach a Period Inventory Valuation Report `.xlsx` (the attach 📎 button).
   You'll get: *"Report loaded: N item rows... Type VALIDATE to run all checks."*
2. Type `VALIDATE`. You'll get a PASS/FAIL scorecard.
3. If there are exceptions, type `EXCEPTIONS` for the list, or
   `DETAILS <item number>` for the full breakdown of one item.
4. Type `EXPORT` to download an Excel copy of every row's check results
   (failing rows highlighted).
5. Type `RESET` to clear the session and load a different report.

Type `HELP` any time to see the full command list, or `STATUS` to check
whether Oracle is currently reachable.

### Typo / robustness handling
- Unknown command → suggests the nearest valid command (`VALDATE` → *"Did you
  mean `VALIDATE`?"*).
- Unknown item number in `DETAILS` → suggests the nearest item number(s) that
  actually exist in the last validated report.
- Missing argument (e.g. bare `DETAILS`) → asks for it with an example.
- Malformed/wrong file type on upload → a specific error message, no crash.
- A row with missing required fields → surfaces as a clear, per-row exception
  instead of silently skipping or throwing.

## Notes on the Oracle cost cross-check (Check ④)

The exact REST resource name for item-level costs can vary by Oracle
instance/release. It's set via `ORACLE_COST_RESOURCE` in `.env` (default
`costItemCosts`). If Check ④ always shows "not found" on your instance,
update that value to whatever resource this SCM_IMPL instance actually
exposes — everything else keeps working regardless, since that check fails
soft (SKIPPED, not FAIL) rather than blocking the rest of the report.

## Explicitly out of scope for Phase 1

- No LLM calls anywhere in this codebase.
- No PR drafting, BPA lookups, or Create Accounting/journal validation —
  that's Phase 2, built on a separate module.
- No PDF parsing — upload the `.xlsx` export, not a PDF/print copy.
