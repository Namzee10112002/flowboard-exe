import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STORAGE_DIR = Path(os.getenv("FLOWBOARD_STORAGE", ROOT / "storage"))
DB_PATH = Path(os.getenv("FLOWBOARD_DB", STORAGE_DIR / "flowboard.db"))

HTTP_PORT = int(os.getenv("FLOWBOARD_HTTP_PORT", "8101"))
WS_HOST = os.getenv("FLOWBOARD_WS_HOST", "127.0.0.1")
EXTENSION_WS_PORT = int(os.getenv("FLOWBOARD_EXT_WS_PORT", "9323"))

PLANNER_MODEL = os.getenv("FLOWBOARD_PLANNER_MODEL", "claude-sonnet-4-6")
# "cli" → always use claude CLI; "mock" → always mock; "auto" → CLI if available,
# otherwise mock. Default auto.
PLANNER_BACKEND = os.getenv("FLOWBOARD_PLANNER_BACKEND", "auto")

LICENSE_SHEET_URL = os.getenv(
    "FLOWBOARD_LICENSE_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vSxztM7iZT40KEpIzVzvGY8Nhs-TqvF57lxITQpfqaWonkb1rt4jVFVMcoCywtTVObYIiIrpDMpZyD1/pub?gid=0&single=true&output=tsv",
)
LICENSE_CACHE_PATH = Path(
    os.getenv("FLOWBOARD_LICENSE_CACHE", Path.home() / ".flowboard" / "license.json")
)
_LICENSE_REQUIRED_ENV = os.getenv("FLOWBOARD_LICENSE_REQUIRED")
LICENSE_REQUIRED = (
    _LICENSE_REQUIRED_ENV.lower() in {"1", "true", "yes", "on"}
    if _LICENSE_REQUIRED_ENV is not None
    else bool(getattr(sys, "frozen", False))
)
LICENSE_OFFLINE_GRACE_DAYS = int(os.getenv("FLOWBOARD_LICENSE_OFFLINE_GRACE_DAYS", "3"))
LICENSE_HTTP_TIMEOUT_SECONDS = float(os.getenv("FLOWBOARD_LICENSE_HTTP_TIMEOUT_SECONDS", "8"))

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
