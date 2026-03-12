import re
from typing import Iterable


class DangerousCommandError(Exception):
    """Raised when a command is rejected by the guard layer."""


_DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/(\s|$)",
    r"mkfs(\.|\s)",
    r"\bshutdown\b",
    r"\breboot\b",
    r"chmod\s+777\s+-R\s+/",
]


def is_dangerous(command: str, extra_patterns: Iterable[str] | None = None) -> bool:
    patterns = list(_DANGEROUS_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)
    for pattern in patterns:
        if re.search(pattern, command):
            return True
    return False


def ensure_safe(command: str, allow_dangerous: bool = False) -> None:
    if allow_dangerous:
        return
    if is_dangerous(command):
        raise DangerousCommandError(f"Command blocked by guard: {command!r}")
