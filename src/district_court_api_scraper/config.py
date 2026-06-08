from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency bootstrap
    load_dotenv = None


DEFAULT_BASE_URL = "https://services.ecourts.gov.in/ecourtindia_v6"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

TASK_DISCOVER = "DISCOVER"
TASK_ACT_LIST = "ACT_LIST"
TASK_CASE_DETAIL = "CASE_DETAIL"
TASK_HEARING_DETAIL = "HEARING_DETAIL"
TASK_CNR_CASE = "CNR_CASE"

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_FAILED = "FAILED"


def load_project_env(project_root: Path) -> Path | None:
    env_path = project_root / ".env"
    if not env_path.exists():
        return None
    if load_dotenv is not None:
        load_dotenv(env_path, override=False)
        return env_path
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return env_path


@dataclass(slots=True)
class RunPaths:
    run_id: str
    run_root: Path
    raw_case_lists: Path
    raw_case_details: Path
    raw_hearings: Path
    raw_pdfs: Path
    normalized_dir: Path
    exports_dir: Path
    case_list_items_jsonl: Path
    case_details_jsonl: Path
    hearings_jsonl: Path
    incomplete_tasks_jsonl: Path
    cases_csv: Path
    hearings_csv: Path


@dataclass(slots=True)
class AppConfig:
    project_root: Path
    base_url: str = DEFAULT_BASE_URL
    user_agent: str = DEFAULT_USER_AGENT
    min_delay_seconds: float = 2.5
    max_retries: int = 4
    request_timeout_seconds: int = 30
    proxy_file: str | None = None

    @property
    def state_dir(self) -> Path:
        path = self.project_root / "state"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def queue_db_path(self) -> Path:
        return self.state_dir / "tasks.db"

    @property
    def output_runs_dir(self) -> Path:
        path = self.project_root / "output" / "runs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def default(cls, project_root: Path | None = None) -> "AppConfig":
        if project_root is None:
            project_root = Path(__file__).resolve().parents[2]
        load_project_env(project_root)
        return cls(
            project_root=project_root,
            base_url=os.getenv("ECOURTS_BASE_URL", DEFAULT_BASE_URL).strip(),
            min_delay_seconds=float(os.getenv("MIN_REQUEST_DELAY", "2.5")),
            max_retries=int(os.getenv("HTTP_MAX_RETRIES", "4")),
            request_timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            proxy_file=(os.getenv("ECOURTS_PROXY_FILE", "").strip() or None),
        )

    def ensure_run_paths(self, run_id: str) -> RunPaths:
        run_root = self.output_runs_dir / run_id
        raw_case_lists = run_root / "raw" / "case_lists"
        raw_case_details = run_root / "raw" / "case_details"
        raw_hearings = run_root / "raw" / "hearings"
        raw_pdfs = run_root / "raw" / "pdfs"
        normalized_dir = run_root / "normalized"
        exports_dir = run_root / "exports"

        for path in (
            raw_case_lists,
            raw_case_details,
            raw_hearings,
            raw_pdfs,
            normalized_dir,
            exports_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        return RunPaths(
            run_id=run_id,
            run_root=run_root,
            raw_case_lists=raw_case_lists,
            raw_case_details=raw_case_details,
            raw_hearings=raw_hearings,
            raw_pdfs=raw_pdfs,
            normalized_dir=normalized_dir,
            exports_dir=exports_dir,
            case_list_items_jsonl=normalized_dir / "case_list_items.jsonl",
            case_details_jsonl=normalized_dir / "case_details.jsonl",
            hearings_jsonl=normalized_dir / "hearings.jsonl",
            incomplete_tasks_jsonl=normalized_dir / "incomplete_tasks.jsonl",
            cases_csv=exports_dir / "cases.csv",
            hearings_csv=exports_dir / "hearings.csv",
        )


def new_run_id(prefix: str = "run") -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{now}_{uuid4().hex[:8]}"
