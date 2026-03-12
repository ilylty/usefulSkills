import time
from typing import Any, Dict, Optional

from ssh_client import _import_paramiko


def run_sudo_command(
    client: "paramiko.SSHClient",
    command: str,
    *,
    password: str,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Run a command with sudo, feeding the password via stdin.

    Uses a PTY because many sudo configurations require it.
    """

    paramiko = _import_paramiko()

    wrapped = f"sudo -S -p '' {command}"
    started = time.monotonic()

    try:
        stdin, stdout, stderr = client.exec_command(
            wrapped,
            get_pty=True,
            timeout=timeout,
        )

        stdin.write(password + "\n")
        stdin.flush()

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

    duration_ms = int((time.monotonic() - started) * 1000)
    stdout_text = out_data.decode("utf-8", errors="replace") if out_data else ""
    stderr_text = err_data.decode("utf-8", errors="replace") if err_data else ""
    success = bool(exit_code == 0)

    return {
        "success": success,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "exit_code": exit_code,
        "timed_out": False,
        "interrupted": False,
        "duration_ms": duration_ms,
    }
