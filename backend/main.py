"""
ORION Validator - Phase 1
FastAPI Backend

No LLM is called anywhere in this backend. The user interacts through a small,
fixed set of typed commands (see commands.py). File parsing (report_parser.py)
and validation (validator.py) are pure deterministic Python + optional live
Oracle REST cross-checks (oracle_client.py).
"""

import logging
import secrets
from io import BytesIO
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from commands import handle_command
from oracle_client import get_oracle_connection_status
from report_parser import parse_valuation_report

app = FastAPI(title="ORION Validator - Phase 1", version="1.0.0")
FRONTEND_INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"

AUTH_USERNAME = "testing"
AUTH_PASSWORD = "123456"

# token -> session state: {"report": {...} | None, "validation": {...} | None}
SESSIONS: dict[str, dict] = {}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orion_validator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    username: str
    password: str


class CommandRequest(BaseModel):
    command: str


def require_auth(authorization: str = Header(default="")) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token not in SESSIONS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required.")
    return token


@app.get("/")
def frontend():
    if not FRONTEND_INDEX.exists():
        raise HTTPException(404, "Frontend file not found.")
    return FileResponse(
        FRONTEND_INDEX,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
    )


@app.post("/api/login")
def login(req: LoginRequest):
    if not secrets.compare_digest(req.username, AUTH_USERNAME) or not secrets.compare_digest(
        req.password, AUTH_PASSWORD
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {"report": None, "validation": None}
    return {"success": True, "token": token, "username": req.username}


@app.post("/api/logout")
def logout(token: str = Depends(require_auth)):
    SESSIONS.pop(token, None)
    return {"success": True}


@app.get("/api/health")
def health(_: str = Depends(require_auth)):
    return {"status": "ok", "service": "ORION Validator", "version": "1.0.0", "oracle": get_oracle_connection_status()}


@app.post("/api/upload")
async def upload_report(token: str = Depends(require_auth), file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Please upload the .xlsx export of the Period Inventory Valuation Report. "
            f"'{file.filename}' is not an Excel file.",
        )

    contents = await file.read()
    try:
        parsed = parse_valuation_report(contents)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception:
        logger.exception("Unexpected error parsing uploaded report.")
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This file could not be read. Please confirm it is an unedited Period Inventory "
            "Valuation Report export from Oracle.",
        )

    SESSIONS[token]["report"] = parsed
    SESSIONS[token]["validation"] = None

    meta = parsed.get("metadata") or {}
    return {
        "success": True,
        "filename": file.filename,
        "row_count": parsed["row_count"],
        "metadata": meta,
        "message": (
            f"Report loaded: **{parsed['row_count']} item rows**"
            + (f", Period {meta.get('Period')}" if meta.get("Period") else "")
            + (f", {meta.get('Cost Organization')}" if meta.get("Cost Organization") else "")
            + ".\n\nType `VALIDATE` to run all checks."
        ),
    }


@app.post("/api/command")
def command(req: CommandRequest, token: str = Depends(require_auth)):
    session = SESSIONS[token]
    result = handle_command(req.command, session)

    if result.get("type") == "export":
        # Client will follow up with GET /api/export to actually download the bytes;
        # we stash the freshly generated workbook in the session for that request.
        session["_last_export"] = result["file_bytes"]
        session["_last_export_name"] = result["filename"]
        return {"success": True, "type": "text", "message": result["message"] + " (Download starting…)", "download": True}

    return {"success": True, **result}


@app.get("/api/export")
def export(token: str = Depends(require_auth)):
    session = SESSIONS[token]
    file_bytes = session.get("_last_export")
    filename = session.get("_last_export_name", "ORION_Validation_Report.xlsx")
    if not file_bytes:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No export is ready. Type `EXPORT` in the chat first.")

    return StreamingResponse(
        BytesIO(file_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
