import json
import os
import time
from typing import Any, Dict


_AUDIT_LOG_PATH = os.environ.get("AI_SSH_AUDIT_LOG", "ai_ssh_audit.log")


def log_action(action: str, payload: Dict[str, Any]) -> None:
    """Append a single JSON line to the audit log.

    The payload should already be JSON-serializable.
    """

    record = {
        "ts": int(time.time() * 1000),
        "action": action,
        **payload,
    }
    try:
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Audit failures should not break primary functionality
        pass
