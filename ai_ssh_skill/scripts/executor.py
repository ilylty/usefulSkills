import json
import shlex
import time
from typing import Any, Dict, Optional

from ssh_client import _import_paramiko


def _build_remote_command(
    command: str, cwd: Optional[str], env: Optional[Dict[str, str]]
) -> str:
    parts = []
    if env:
        exports = [f"{k}={shlex.quote(v)}" for k, v in env.items()]
        parts.append("env " + " ".join(exports))
    if cwd:
        parts.append("cd " + shlex.quote(cwd) + " &&")
    parts.append(command)
    return " ".join(parts)


def run_exec_command(
    client: "paramiko.SSHClient",
    command: str,
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Run a non-interactive command via exec_command.

    Returns a structured JSON-serializable dict.
    """

    paramiko = _import_paramiko()

    full_command = _build_remote_command(command, cwd=cwd, env=env)
    started = time.monotonic()
    timed_out = False

    try:
        stdin, stdout, stderr = client.exec_command(
            full_command,
            get_pty=False,
            timeout=timeout,
        )
        # Ensure read operations time out
        stdout.channel.settimeout(timeout)
        stderr.channel.settimeout(timeout)

        out_data = stdout.read()
        err_data = stderr.read()
        exit_code = stdout.channel.recv_exit_status()
    except (paramiko.SSHException, OSError) as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
            "timed_out": False,
            "interrupted": False,
            "duration_ms": duration_ms,
        }
    except TimeoutError:
        timed_out = True
        exit_code = None
        out_data = b""
        err_data = b"Timed out while waiting for command output"
    except Exception as exc:  # pragma: no cover - defensive
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
            "timed_out": False,
            "interrupted": False,
            "duration_ms": duration_ms,
        }

    duration_ms = int((time.monotonic() - started) * 1000)

    stdout_text = out_data.decode("utf-8", errors="replace") if out_data else ""
    stderr_text = err_data.decode("utf-8", errors="replace") if err_data else ""

    success = bool(exit_code == 0 and not timed_out)

    return {
        "success": success,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "interrupted": False,
        "duration_ms": duration_ms,
    }


def to_json_bytes(data: Dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
