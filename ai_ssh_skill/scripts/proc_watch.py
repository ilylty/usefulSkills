from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ProcRow:
    pid: int
    etimes_s: int
    args: str


def parse_ps_etimes_args(text: str) -> List[ProcRow]:
    """Parse `ps -eo pid=,etimes=,args=` output."""

    rows: List[ProcRow] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            et = int(parts[1])
        except ValueError:
            continue
        args = parts[2] if len(parts) >= 3 else ""
        rows.append(ProcRow(pid=pid, etimes_s=et, args=args))
    return rows


def match_pattern(row: ProcRow, pattern: str) -> bool:
    p = (pattern or "").lower()
    if not p:
        return False
    return p in row.args.lower()


def unique_preserve(items: List[str]) -> List[str]:
    seen: Dict[str, bool] = {}
    out: List[str] = []
    for x in items:
        if x in seen:
            continue
        seen[x] = True
        out.append(x)
    return out
