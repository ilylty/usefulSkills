import re
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class NetCounters:
    rx_bytes: int
    tx_bytes: int


def parse_proc_net_dev(text: str) -> Dict[str, NetCounters]:
    """Parse /proc/net/dev.

    Returns {iface: NetCounters(rx_bytes, tx_bytes)}.
    """

    counters: Dict[str, NetCounters] = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        if "|" in line:
            continue
        name, rest = line.split(":", 1)
        iface = name.strip()
        fields = rest.split()
        if len(fields) < 16:
            continue
        try:
            rx = int(fields[0])
            tx = int(fields[8])
        except ValueError:
            continue
        counters[iface] = NetCounters(rx_bytes=rx, tx_bytes=tx)
    return counters


_IP_ROUTE_DEV_RE = re.compile(r"\bdev\s+(\S+)")


def parse_default_iface(ip_route_get_output: str) -> str | None:
    m = _IP_ROUTE_DEV_RE.search(ip_route_get_output or "")
    if not m:
        return None
    return m.group(1)


def pick_busy_iface(counters: Dict[str, NetCounters]) -> str | None:
    best = None
    best_total = -1
    for iface, c in counters.items():
        if iface == "lo":
            continue
        total = c.rx_bytes + c.tx_bytes
        if total > best_total:
            best_total = total
            best = iface
    return best


def kbps(delta_bytes: int, interval_s: float) -> float:
    if interval_s <= 0:
        return 0.0
    return (delta_bytes * 8.0) / 1000.0 / interval_s


def deltas(prev: NetCounters, curr: NetCounters) -> Tuple[int, int]:
    return (
        max(0, curr.rx_bytes - prev.rx_bytes),
        max(0, curr.tx_bytes - prev.tx_bytes),
    )
