import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class JobSpec:
    pid: int
    log: str
    exit_file: str
    started_ts_ms: int
    cmd: str
    cwd: Optional[str] = None


def encode_job_id(spec: JobSpec) -> str:
    payload = {
        "pid": spec.pid,
        "log": spec.log,
        "exit_file": spec.exit_file,
        "started_ts_ms": spec.started_ts_ms,
        "cmd": spec.cmd,
        "cwd": spec.cwd,
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_job_id(job_id: str) -> Dict[str, Any]:
    pad = "=" * (-len(job_id) % 4)
    raw = base64.urlsafe_b64decode((job_id + pad).encode("ascii"))
    return json.loads(raw.decode("utf-8"))


def now_ms() -> int:
    return int(time.time() * 1000)
