import re
from typing import Any, Dict, List, Optional


def parse_systemctl_show(text: str) -> Dict[str, str]:
    """Parse `systemctl show` output (KEY=VALUE lines)."""

    data: Dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def hint_from_journal_lines(lines: List[str]) -> Optional[str]:
    blob = "\n".join(lines)
    patterns = [
        (r"json.*(parse|decode) error", "config.json parse error or invalid JSON"),
        (r"(yaml|toml).*(parse|decode) error", "config parse error"),
        (r"permission denied", "permission denied (check file ownership/permissions)"),
        (r"address already in use|EADDRINUSE", "port already in use"),
        (r"no such file|not found", "missing file or bad path"),
        (r"exec format error", "binary format mismatch"),
        (r"failed to load|unable to load", "failed to load config or resource"),
    ]
    for pat, hint in patterns:
        if re.search(pat, blob, flags=re.IGNORECASE):
            return hint
    return None
