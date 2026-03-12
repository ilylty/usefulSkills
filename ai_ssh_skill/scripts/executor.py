import json
import shlex
import socket
import time
from typing import Any, Dict, Optional

from ssh_client import _import_paramiko


def _build_remote_command(
    command: str, cwd: Optional[str], env: Optional[Dict[str, str]]
) -> str:
    # Paramiko exec_command does not run inside an interactive shell, so shell
    # builtins like `cd` won't work if invoked as a program (e.g. `env ... cd`).
    # When cwd/env are used, wrap in `sh -lc` so we can reliably `cd` and
    # `export` before running the provided command string.
    if not cwd and not env:
        return command

    script_parts = []
    if cwd:
        script_parts.append("cd " + shlex.quote(cwd))
    if env:
        for k, v in env.items():
            # Keep it simple: assume keys are well-formed shell identifiers.
            script_parts.append(f"export {k}={shlex.quote(v)}")
    script_parts.append(command)
    script = "; ".join(script_parts)
    return "sh -lc " + shlex.quote(script)


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
    reason: Optional[str] = None

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
    except TimeoutError:
        timed_out = True
        reason = "timeout"
        exit_code = None
        out_data = b""
        err_data = b"Timed out while waiting for command output"
    except socket.timeout:
        timed_out = True
        reason = "timeout"
        exit_code = None
        out_data = b""
        err_data = b"Timed out while waiting for command output"
    except (paramiko.SSHException, OSError) as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
            "timed_out": False,
            "interrupted": False,
            "reason": "ssh_error",
            "duration_ms": duration_ms,
            "command": command,
            "remote_command": full_command,
            "cwd": cwd,
            "env": env or {},
        }
    except Exception as exc:  # pragma: no cover - defensive
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
            "timed_out": False,
            "interrupted": False,
            "reason": "unknown_error",
            "duration_ms": duration_ms,
            "command": command,
            "remote_command": full_command,
            "cwd": cwd,
            "env": env or {},
        }

    duration_ms = int((time.monotonic() - started) * 1000)

    stdout_text = out_data.decode("utf-8", errors="replace") if out_data else ""
    stderr_text = err_data.decode("utf-8", errors="replace") if err_data else ""

    success = bool(exit_code == 0 and not timed_out)

    if exit_code is None and reason is None:
        reason = "timeout" if timed_out else "unknown"

    return {
        "success": success,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "interrupted": False,
        "reason": reason,
        "duration_ms": duration_ms,
        "command": command,
        "remote_command": full_command,
        "cwd": cwd,
        "env": env or {},
    }


def to_json_bytes(data: Dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
