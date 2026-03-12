import re
import shlex
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class TmuxResult:
    output: str
    exit_code: Optional[int]


_EXIT_RE = re.compile(r"__AI_EXIT__:(\d+)")


def tmux_has(tmux_check_stdout: str) -> bool:
    return (tmux_check_stdout or "").strip() == "0"


def build_open_cmd(session: str, *, cwd: Optional[str] = None) -> str:
    sess = shlex.quote(session)
    if cwd:
        return f"tmux new-session -d -s {sess} -c {shlex.quote(cwd)}"
    return f"tmux new-session -d -s {sess}"


def build_send_cmd(session: str, cmd: str, marker: str) -> str:
    sess = shlex.quote(session)
    # Use Enter to execute; then print exit code marker.
    safe_cmd = cmd
    exit_line = f"printf '\\n__AI_EXIT__:%s\\n' $?; printf '\\n{marker}\\n'"
    payload = safe_cmd + "\n" + exit_line
    return f"tmux send-keys -t {sess} {shlex.quote(payload)} Enter"


def build_capture_cmd(session: str, *, lines: int = 200) -> str:
    sess = shlex.quote(session)
    return f"tmux capture-pane -p -t {sess} -S -{int(lines)}"


def parse_exit_code(output: str) -> Optional[int]:
    m = None
    for m in _EXIT_RE.finditer(output or ""):
        pass
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None
