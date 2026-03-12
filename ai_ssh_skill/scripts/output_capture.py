import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _ensure_parent(path: str) -> None:
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _default_output_path(prefix: str, ext: str = "log") -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    base = Path(__file__).resolve().parent / "outputs"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"{prefix}-{ts}.{ext}")


def capture_output(
    result: Dict[str, Any],
    *,
    requested_path: Optional[str],
    max_inline_chars: int,
    prefix: str,
) -> Dict[str, Any]:
    """Optionally save large outputs to a local file and truncate inline fields.

    Mutates and returns result.
    """

    stdout = result.get("stdout") or ""
    stderr = result.get("stderr") or ""

    combined_len = len(stdout) + len(stderr)
    should_save = bool(requested_path) or combined_len > max_inline_chars

    if not should_save:
        result["truncated"] = False
        result["output_file"] = None
        return result

    out_path = requested_path or _default_output_path(prefix)
    _ensure_parent(out_path)

    # Write a simple, grep-friendly log format
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# ai-ssh output capture\n")
        for key in ("host", "command", "cwd"):
            if key in result and result[key] is not None:
                f.write(f"# {key}: {result[key]}\n")
        f.write("\n--- stdout ---\n")
        f.write(stdout)
        if not stdout.endswith("\n"):
            f.write("\n")
        f.write("\n--- stderr ---\n")
        f.write(stderr)
        if not stderr.endswith("\n"):
            f.write("\n")

    def _clip(text: str) -> str:
        if len(text) <= max_inline_chars:
            return text
        head = text[:max_inline_chars]
        return head + "\n(truncated; full output saved to output_file)\n"

    result["stdout"] = _clip(stdout)
    result["stderr"] = _clip(stderr)
    if "output" in result and isinstance(result.get("output"), str):
        result["output"] = _clip(result["output"])  # type: ignore[assignment]

    result["truncated"] = combined_len > max_inline_chars
    result["output_file"] = out_path
    return result
