import argparse
import json
import os
import shlex
import sys
from typing import Any, Dict, List, Optional

from audit import log_action
from executor import run_exec_command, to_json_bytes
from file_ops import (
    cp as sftp_cp,
    list_dir,
    mkdir as sftp_mkdir,
    mv as sftp_mv,
    path_exists,
    path_type,
    read_text_file,
    rm as sftp_rm,
    write_text_file,
)
from guard import DangerousCommandError, ensure_safe
from output_capture import capture_output
from sftp_client import sftp
from shell import ShellSession
from ssh_client import SSHConnectionError, ssh_client, test_connection
from sudo import run_sudo_command
from systemd import hint_from_journal_lines, parse_systemctl_show
from jobs import JobSpec, decode_job_id, encode_job_id, now_ms
from net_watch import (
    deltas as net_deltas,
    kbps as net_kbps,
    parse_default_iface,
    parse_proc_net_dev,
    pick_busy_iface,
)
from proc_watch import match_pattern, parse_ps_etimes_args, unique_preserve
from tmux_shell import (
    build_capture_cmd,
    build_open_cmd,
    build_send_cmd,
    parse_exit_code as tmux_parse_exit_code,
)


def _common_ssh_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", required=True, help="Target host or IP")
    parser.add_argument("--port", type=int, default=22, help="SSH port")
    parser.add_argument("--user", "--username", dest="username", required=True)
    parser.add_argument("--password", dest="password", help="Password auth")
    parser.add_argument("--key", dest="key_path", help="Private key path")
    parser.add_argument("--passphrase", dest="passphrase", help="Key passphrase")
    parser.add_argument(
        "--timeout", type=int, default=30, help="Connection/command timeout (s)"
    )


def _parse_env(env_list: Optional[List[str]]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not env_list:
        return env
    for item in env_list:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        env[k] = v
    return env


def _load_content_arg(value: Optional[str]) -> str:
    """Load content from a literal string, @file, or stdin when None."""

    if value is None:
        return sys.stdin.read()
    if value.startswith("@") and len(value) > 1:
        path = value[1:]
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return value


def _add_output_capture_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-file",
        dest="output_file",
        help="Save full output to a local file",
    )
    parser.add_argument(
        "--max-inline-chars",
        dest="max_inline_chars",
        type=int,
        default=20000,
        help="Max chars kept inline before truncation",
    )


def cmd_connect(args: argparse.Namespace) -> int:
    ok = test_connection(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        key_path=args.key_path,
        passphrase=args.passphrase,
        timeout=args.timeout,
    )
    result = {"success": ok, "host": args.host, "port": args.port}
    log_action("connect", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0 if ok else 1


def cmd_exec(args: argparse.Namespace) -> int:
    try:
        ensure_safe(args.cmd, allow_dangerous=args.allow_dangerous)
    except DangerousCommandError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "command": args.cmd,
        }
        log_action("exec_blocked", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    env = _parse_env(args.env)

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            result = run_exec_command(
                client,
                args.cmd,
                cwd=args.cwd,
                env=env,
                timeout=args.timeout,
            )
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "command": args.cmd,
        }

    result["host"] = args.host
    result["command"] = args.cmd
    result["cwd"] = args.cwd
    result["env"] = env
    capture_output(
        result,
        requested_path=args.output_file,
        max_inline_chars=args.max_inline_chars,
        prefix="exec",
    )
    log_action("exec", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0 if result.get("success") else 1


def cmd_sudo(args: argparse.Namespace) -> int:
    sudo_password = args.sudo_password or args.password
    if not sudo_password:
        result = {
            "success": False,
            "error": "sudo requires --sudo-password or --password",
            "command": args.cmd,
        }
        log_action("sudo_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    try:
        ensure_safe(args.cmd, allow_dangerous=args.allow_dangerous)
    except DangerousCommandError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "command": args.cmd,
        }
        log_action("sudo_blocked", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            # Some minimal distros (or root environments) may not have sudo.
            sudo_check = run_exec_command(
                client,
                "command -v sudo >/dev/null 2>&1; echo $?",
                timeout=args.timeout,
            )
            if sudo_check.get("stdout", "").strip().endswith("0"):
                result = run_sudo_command(
                    client,
                    args.cmd,
                    password=sudo_password,
                    timeout=args.timeout,
                )
            else:
                # If user is already root, run without sudo.
                whoami = run_exec_command(client, "id -u", timeout=args.timeout)
                if whoami.get("stdout", "").strip() == "0":
                    result = run_exec_command(client, args.cmd, timeout=args.timeout)
                    result["sudo_skipped"] = True
                else:
                    result = {
                        "success": False,
                        "stdout": "",
                        "stderr": "sudo not found on remote host and user is not root",
                        "exit_code": 127,
                        "timed_out": False,
                        "interrupted": False,
                        "reason": "sudo_not_found",
                        "duration_ms": 0,
                        "command": args.cmd,
                        "sudo_skipped": False,
                    }
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "command": args.cmd,
        }

    result["host"] = args.host
    result["command"] = args.cmd
    result["cwd"] = None
    result["env"] = {}
    capture_output(
        result,
        requested_path=args.output_file,
        max_inline_chars=args.max_inline_chars,
        prefix="sudo",
    )
    log_action("sudo", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0 if result.get("success") else 1


def cmd_upload(args: argparse.Namespace) -> int:
    if not os.path.exists(args.local):
        result = {
            "success": False,
            "error": f"Local path does not exist: {args.local}",
        }
        log_action("upload_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    with ssh_client(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        key_path=args.key_path,
        passphrase=args.passphrase,
        timeout=args.timeout,
    ) as client:
        with sftp(client) as s:
            if not args.overwrite:
                try:
                    s.stat(args.remote)
                    result = {
                        "success": False,
                        "error": f"Remote path already exists: {args.remote}",
                    }
                    log_action("upload_error", result)
                    sys.stdout.buffer.write(to_json_bytes(result))
                    sys.stdout.write("\n")
                    return 1
                except IOError:
                    pass

            s.put(args.local, args.remote)
            size = os.path.getsize(args.local)

    result = {
        "success": True,
        "local": args.local,
        "remote": args.remote,
        "bytes": size,
        "host": args.host,
    }
    log_action("upload", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    if os.path.exists(args.local) and not args.overwrite:
        result = {
            "success": False,
            "error": f"Local path already exists: {args.local}",
            "local": args.local,
            "remote": args.remote,
            "host": args.host,
        }
        log_action("download_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            with sftp(client) as s:
                s.get(args.remote, args.local)
                size = os.path.getsize(args.local)

    except (SSHConnectionError, OSError, IOError) as exc:
        # 可选：下载失败时清理已生成的半成品文件
        try:
            if os.path.exists(args.local):
                os.remove(args.local)
        except Exception:
            pass

        result = {
            "success": False,
            "error": str(exc),
            "local": args.local,
            "remote": args.remote,
            "host": args.host,
        }
        log_action("download_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {
        "success": True,
        "local": args.local,
        "remote": args.remote,
        "bytes": size,
        "host": args.host,
    }
    log_action("download", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            content = read_text_file(client, args.path)
    except SSHConnectionError as exc:
        result = {"success": False, "error": str(exc), "path": args.path}
        log_action("read_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {"success": True, "path": args.path, "content": content}
    log_action("read", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    content = _load_content_arg(args.content)

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            info = write_text_file(
                client,
                args.path,
                content,
                append=args.append,
                backup=args.backup,
            )
    except SSHConnectionError as exc:
        result = {"success": False, "error": str(exc), "path": args.path}
        log_action("write_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {"success": True, "path": args.path, **info}
    log_action("write", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_shell_run(args: argparse.Namespace) -> int:
    try:
        ensure_safe(args.cmd, allow_dangerous=args.allow_dangerous)
    except DangerousCommandError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "command": args.cmd,
        }
        log_action("shell_blocked", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            session = ShellSession(client)
            result = session.run(
                args.cmd,
                timeout=args.timeout,
                sudo_password=args.sudo_password,
            )
            session.close()
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "command": args.cmd,
        }

    result["host"] = args.host
    capture_output(
        result,
        requested_path=args.output_file,
        max_inline_chars=args.max_inline_chars,
        prefix="shell-run",
    )
    log_action("shell_run", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0 if result.get("success") else 1


def cmd_stat(args: argparse.Namespace) -> int:
    # Minimal system info collection using exec_command
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            uname = run_exec_command(client, "uname -a", timeout=args.timeout)
            os_release = run_exec_command(
                client, "cat /etc/os-release", timeout=args.timeout
            )
            uptime = run_exec_command(client, "uptime", timeout=args.timeout)
            df = run_exec_command(client, "df -h", timeout=args.timeout)
    except SSHConnectionError as exc:
        result = {"success": False, "error": str(exc)}
        log_action("stat_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {
        "success": True,
        "host": args.host,
        "uname": uname,
        "os_release": os_release,
        "uptime": uptime,
        "disk": df,
    }
    log_action("stat", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_service_fail_summary(args: argparse.Namespace) -> int:
    unit = args.unit
    tail_n = args.tail_lines
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            show = run_exec_command(
                client,
                "systemctl show "
                + unit
                + " -p LoadState -p ActiveState -p SubState -p NRestarts -p ExecMainStatus -p Result -p ExecMainExitTimestamp",
                timeout=args.timeout,
            )
            journal = run_exec_command(
                client,
                f"journalctl -u {unit} -n {tail_n} --no-pager -o cat",
                timeout=args.timeout,
            )
    except SSHConnectionError as exc:
        result = {"success": False, "error": str(exc), "unit": unit, "host": args.host}
        log_action("service_fail_summary_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    show_kv = parse_systemctl_show(show.get("stdout", ""))
    if show_kv.get("LoadState") == "not-found":
        result = {
            "success": False,
            "unit": unit,
            "host": args.host,
            "reason": "unit_not_found",
            "systemctl_show": show_kv,
        }
        log_action("service_fail_summary", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1
    tail_lines = [
        ln for ln in (journal.get("stdout", "") or "").splitlines() if ln.strip()
    ]
    hint = hint_from_journal_lines(tail_lines)

    exit_code: Optional[int] = None
    try:
        if show_kv.get("ExecMainStatus"):
            exit_code = int(show_kv["ExecMainStatus"])
    except Exception:
        exit_code = None

    result = {
        "success": True,
        "unit": unit,
        "host": args.host,
        "active_state": show_kv.get("ActiveState"),
        "sub_state": show_kv.get("SubState"),
        "restart_count": int(show_kv.get("NRestarts", "0") or "0"),
        "last_exit_code": exit_code,
        "last_restart_time": show_kv.get("ExecMainExitTimestamp"),
        "tail": tail_lines,
        "hint": hint,
        "systemctl_show": show_kv,
    }
    log_action("service_fail_summary", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_service_watch(args: argparse.Namespace) -> int:
    unit = args.unit
    duration_s = args.duration
    interval_s = args.interval
    poll_count = max(1, int(duration_s / interval_s))

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            first = run_exec_command(
                client,
                f"systemctl show {unit} -p LoadState -p MainPID -p NRestarts -p ExecMainStatus -p ActiveState -p SubState",
                timeout=args.timeout,
            )
            first_kv = parse_systemctl_show(first.get("stdout", ""))
            if first_kv.get("LoadState") == "not-found":
                result = {
                    "success": False,
                    "unit": unit,
                    "host": args.host,
                    "reason": "unit_not_found",
                    "systemctl_show": first_kv,
                }
                log_action("service_watch", result)
                sys.stdout.buffer.write(to_json_bytes(result))
                sys.stdout.write("\n")
                return 1
            restart_start = int(first_kv.get("NRestarts", "0") or "0")

            pids_seen: List[int] = []
            last_error: Optional[str] = None
            last_exit_code: Optional[int] = None
            active_state = first_kv.get("ActiveState")
            sub_state = first_kv.get("SubState")

            for _ in range(poll_count):
                current = run_exec_command(
                    client,
                    f"systemctl show {unit} -p LoadState -p MainPID -p NRestarts -p ExecMainStatus -p ActiveState -p SubState",
                    timeout=args.timeout,
                )
                kv = parse_systemctl_show(current.get("stdout", ""))
                active_state = kv.get("ActiveState")
                sub_state = kv.get("SubState")

                try:
                    pid = int(kv.get("MainPID", "0") or "0")
                except Exception:
                    pid = 0
                if pid and pid not in pids_seen:
                    pids_seen.append(pid)

                try:
                    last_exit_code = int(kv.get("ExecMainStatus", "0") or "0")
                except Exception:
                    last_exit_code = None

                # only pull journal tail when restart count bumps
                try:
                    restarts_now = int(kv.get("NRestarts", "0") or "0")
                except Exception:
                    restarts_now = restart_start

                if restarts_now > restart_start and args.journal_tail > 0:
                    j = run_exec_command(
                        client,
                        f"journalctl -u {unit} -n {args.journal_tail} --no-pager -o cat",
                        timeout=args.timeout,
                    )
                    lines = [
                        ln
                        for ln in (j.get("stdout", "") or "").splitlines()
                        if ln.strip()
                    ]
                    last_error = lines[-1] if lines else last_error
                    restart_start = restarts_now

                # sleep on remote to avoid local drift and extra tool complexity
                run_exec_command(
                    client,
                    f"sleep {interval_s}",
                    timeout=max(args.timeout, interval_s + 5),
                )

            end = run_exec_command(
                client,
                f"systemctl show {unit} -p LoadState -p MainPID -p NRestarts -p ExecMainStatus -p ActiveState -p SubState",
                timeout=args.timeout,
            )
    except SSHConnectionError as exc:
        result = {"success": False, "error": str(exc), "unit": unit, "host": args.host}
        log_action("service_watch_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    end_kv = parse_systemctl_show(end.get("stdout", ""))
    try:
        restart_end = int(end_kv.get("NRestarts", "0") or "0")
    except Exception:
        restart_end = 0

    restart_count_start = int(first_kv.get("NRestarts", "0") or "0")
    restart_delta = restart_end - restart_count_start
    status = "flapping" if restart_delta >= args.flap_threshold else "stable"

    result = {
        "success": True,
        "unit": unit,
        "host": args.host,
        "duration_s": duration_s,
        "interval_s": interval_s,
        "restart_count_start": restart_count_start,
        "restart_count_end": restart_end,
        "pids_seen": pids_seen,
        "status": status,
        "last_exit_code": last_exit_code,
        "last_error": last_error,
        "active_state": active_state,
        "sub_state": sub_state,
    }
    log_action("service_watch", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_job_run(args: argparse.Namespace) -> int:
    try:
        ensure_safe(args.cmd, allow_dangerous=args.allow_dangerous)
    except DangerousCommandError as exc:
        result = {"success": False, "error": str(exc), "command": args.cmd}
        log_action("job_run_blocked", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    log_path = args.log
    exit_path = args.exit_file
    started_ts = now_ms()

    log_q = shlex.quote(log_path)
    exit_q = shlex.quote(exit_path)
    cmd_q = shlex.quote(args.cmd)

    # Start job in background and print PID.
    # Use a subshell so we can persist exit code when the command completes.
    script_parts = []
    if args.cwd:
        script_parts.append("cd " + shlex.quote(args.cwd))
    script_parts.append(
        "( nohup sh -lc "
        + cmd_q
        + " > "
        + log_q
        + " 2>&1; echo $? > "
        + exit_q
        + " ) < /dev/null & echo $!"
    )
    script = "; ".join(script_parts)
    remote = "sh -lc " + shlex.quote(script)

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            res = run_exec_command(client, remote, timeout=args.timeout)
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "command": args.cmd,
            "host": args.host,
        }
        log_action("job_run_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    pid = None
    try:
        pid = int((res.get("stdout", "") or "").strip().splitlines()[-1])
    except Exception:
        pid = None

    if not res.get("success") or not pid:
        result = {
            "success": False,
            "error": "failed to start job",
            "host": args.host,
            "command": args.cmd,
            "stdout": res.get("stdout", ""),
            "stderr": res.get("stderr", ""),
            "exit_code": res.get("exit_code"),
            "reason": res.get("reason"),
        }
        log_action("job_run", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    spec = JobSpec(
        pid=pid,
        log=log_path,
        exit_file=exit_path,
        started_ts_ms=started_ts,
        cmd=args.cmd,
        cwd=args.cwd,
    )
    job_id = encode_job_id(spec)

    result = {
        "success": True,
        "job_id": job_id,
        "pid": pid,
        "log": log_path,
        "exit_file": exit_path,
        "status": "running",
        "host": args.host,
        "command": args.cmd,
        "cwd": args.cwd,
    }
    log_action("job_run", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_job_status(args: argparse.Namespace) -> int:
    try:
        meta = decode_job_id(args.job_id)
        pid = int(meta["pid"])
        log_path = str(meta["log"])
        exit_path = str(meta["exit_file"])
    except Exception as exc:
        result = {
            "success": False,
            "error": f"invalid job id: {exc}",
            "job_id": args.job_id,
        }
        log_action("job_status_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            ps = run_exec_command(client, f"ps -p {pid} -o pid=", timeout=args.timeout)
            running = bool((ps.get("stdout", "") or "").strip())

            exit_code = None
            # If exit file exists, read it.
            ex = run_exec_command(
                client,
                f"test -f {exit_path} && cat {exit_path} || true",
                timeout=args.timeout,
            )
            raw_exit = (ex.get("stdout", "") or "").strip()
            if raw_exit.isdigit():
                exit_code = int(raw_exit)

            tail = run_exec_command(
                client,
                f"test -f {log_path} && tail -n {args.tail_lines} {log_path} || true",
                timeout=args.timeout,
            )
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "job_id": args.job_id,
            "host": args.host,
        }
        log_action("job_status_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    status = (
        "running" if running else ("finished" if exit_code is not None else "unknown")
    )
    tail_lines = [ln for ln in (tail.get("stdout", "") or "").splitlines()]

    result = {
        "success": True,
        "job_id": args.job_id,
        "pid": pid,
        "status": status,
        "exit_code": exit_code,
        "tail": tail_lines,
        "log": log_path,
        "exit_file": exit_path,
        "host": args.host,
    }
    log_action("job_status", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_net_watch(args: argparse.Namespace) -> int:
    duration_s = args.duration
    interval_s = args.interval
    threshold = args.threshold_kbps

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            iface = args.iface
            if iface == "auto":
                route = run_exec_command(
                    client,
                    "ip route get 1.1.1.1 2>/dev/null || true",
                    timeout=args.timeout,
                )
                iface = parse_default_iface(route.get("stdout", "")) or "auto"
            if iface == "auto":
                dev0 = run_exec_command(
                    client, "cat /proc/net/dev", timeout=args.timeout
                )
                counters0 = parse_proc_net_dev(dev0.get("stdout", ""))
                picked = pick_busy_iface(counters0)
                iface = picked or "eth0"

            samples_rx: list[float] = []
            samples_tx: list[float] = []

            dev_prev = run_exec_command(
                client, "cat /proc/net/dev", timeout=args.timeout
            )
            counters_prev = parse_proc_net_dev(dev_prev.get("stdout", ""))
            prev = counters_prev.get(iface)
            if prev is None:
                result = {
                    "success": False,
                    "host": args.host,
                    "reason": "iface_not_found",
                    "iface": iface,
                }
                log_action("net_watch", result)
                sys.stdout.buffer.write(to_json_bytes(result))
                sys.stdout.write("\n")
                return 1

            n = max(1, int(duration_s / interval_s))
            for _ in range(n):
                run_exec_command(
                    client,
                    f"sleep {interval_s}",
                    timeout=max(args.timeout, interval_s + 5),
                )
                dev_curr = run_exec_command(
                    client, "cat /proc/net/dev", timeout=args.timeout
                )
                counters_curr = parse_proc_net_dev(dev_curr.get("stdout", ""))
                curr = counters_curr.get(iface)
                if curr is None:
                    continue
                drx, dtx = net_deltas(prev, curr)
                samples_rx.append(net_kbps(drx, interval_s))
                samples_tx.append(net_kbps(dtx, interval_s))
                prev = curr
    except SSHConnectionError as exc:
        result = {"success": False, "error": str(exc), "host": args.host}
        log_action("net_watch_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    avg_rx = sum(samples_rx) / len(samples_rx) if samples_rx else 0.0
    avg_tx = sum(samples_tx) / len(samples_tx) if samples_tx else 0.0
    active = bool(avg_rx >= threshold)

    result = {
        "success": True,
        "host": args.host,
        "iface": iface,
        "avg_rx_kbps": round(avg_rx, 1),
        "avg_tx_kbps": round(avg_tx, 1),
        "active_download": active,
        "samples_rx_kbps": [round(x, 1) for x in samples_rx],
        "samples_tx_kbps": [round(x, 1) for x in samples_tx],
        "duration_s": duration_s,
        "interval_s": interval_s,
        "threshold_kbps": threshold,
    }
    log_action("net_watch", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_proc_watch(args: argparse.Namespace) -> int:
    duration_s = args.duration
    interval_s = args.interval
    pattern = args.pattern
    min_life_ms = args.min_lifetime_ms

    seen: dict[int, int] = {}
    commands: list[str] = []
    spawn = 0

    n = max(1, int(duration_s / interval_s))

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            for tick in range(n):
                ps = run_exec_command(
                    client, "ps -eo pid=,etimes=,args=", timeout=args.timeout
                )
                rows = parse_ps_etimes_args(ps.get("stdout", ""))
                window_elapsed = (tick + 1) * interval_s

                for row in rows:
                    if not match_pattern(row, pattern):
                        continue
                    # Only track processes that started during watch window.
                    if row.etimes_s > window_elapsed:
                        continue
                    if row.pid not in seen:
                        seen[row.pid] = tick
                        spawn += 1
                        if row.args:
                            commands.append(row.args)

                run_exec_command(
                    client,
                    f"sleep {interval_s}",
                    timeout=max(args.timeout, interval_s + 5),
                )
    except SSHConnectionError as exc:
        result = {"success": False, "error": str(exc), "host": args.host}
        log_action("proc_watch_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    # Lifetime estimate: if we only saw the spawn tick, assume it survived at least interval.
    lifetimes_s: list[float] = []
    for first_tick in seen.values():
        # Conservative lower bound in seconds
        life = max(interval_s, (n - first_tick) * interval_s)
        lifetimes_s.append(float(life))

    # Filter by min lifetime
    min_life_s = min_life_ms / 1000.0
    lifetimes_s = [x for x in lifetimes_s if x >= min_life_s]

    avg_life = sum(lifetimes_s) / len(lifetimes_s) if lifetimes_s else 0.0
    status = "unstable" if spawn >= args.unstable_threshold else "stable"

    result = {
        "success": True,
        "host": args.host,
        "pattern": pattern,
        "window_s": duration_s,
        "interval_s": interval_s,
        "spawn_count": spawn,
        "pids": sorted(list(seen.keys())),
        "avg_lifetime_s": round(avg_life, 1),
        "status": status,
        "commands": unique_preserve(commands),
        "min_lifetime_ms": min_life_ms,
    }
    log_action("proc_watch", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_write_json(args: argparse.Namespace) -> int:
    content = _load_content_arg(args.content)
    try:
        json.loads(content)
    except Exception as exc:
        result = {
            "success": False,
            "path": args.path,
            "written": False,
            "valid": False,
            "error": str(exc),
            "backup_path": None,
            "host": args.host,
        }
        log_action("write_json", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            info = write_text_file(
                client,
                args.path,
                content,
                append=False,
                backup=args.backup,
            )
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "path": args.path,
            "written": False,
            "valid": True,
            "error": str(exc),
            "backup_path": None,
            "host": args.host,
        }
        log_action("write_json_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {
        "success": True,
        "path": args.path,
        "written": True,
        "valid": True,
        "error": None,
        "backup_path": info.get("backup_path"),
        "host": args.host,
    }
    log_action("write_json", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_file_exists(args: argparse.Namespace) -> int:
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            exists = path_exists(client, args.path)
            typ = path_type(client, args.path) if exists else None
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "path": args.path,
            "host": args.host,
        }
        log_action("file_exists_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {
        "success": True,
        "path": args.path,
        "exists": exists,
        "type": typ,
        "host": args.host,
    }
    log_action("file_exists", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_file_ls(args: argparse.Namespace) -> int:
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            entries = list_dir(client, args.path, all=args.all, long=args.long)
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "path": args.path,
            "host": args.host,
        }
        log_action("file_ls_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1
    except Exception as exc:
        result = {
            "success": False,
            "error": str(exc),
            "path": args.path,
            "host": args.host,
        }
        log_action("file_ls_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {"success": True, "path": args.path, "entries": entries, "host": args.host}
    log_action("file_ls", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_file_mkdir(args: argparse.Namespace) -> int:
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            sftp_mkdir(client, args.path, parents=args.parents)
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "path": args.path,
            "host": args.host,
        }
        log_action("file_mkdir_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1
    except Exception as exc:
        result = {
            "success": False,
            "error": str(exc),
            "path": args.path,
            "host": args.host,
        }
        log_action("file_mkdir_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {"success": True, "path": args.path, "host": args.host}
    log_action("file_mkdir", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_file_mv(args: argparse.Namespace) -> int:
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            sftp_mv(client, args.src, args.dst, overwrite=args.overwrite)
    except FileExistsError as exc:
        result = {
            "success": False,
            "error": f"destination exists: {exc}",
            "src": args.src,
            "dst": args.dst,
            "host": args.host,
        }
        log_action("file_mv_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1
    except (SSHConnectionError, Exception) as exc:
        result = {
            "success": False,
            "error": str(exc),
            "src": args.src,
            "dst": args.dst,
            "host": args.host,
        }
        log_action("file_mv_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {"success": True, "src": args.src, "dst": args.dst, "host": args.host}
    log_action("file_mv", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_file_rm(args: argparse.Namespace) -> int:
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            sftp_rm(client, args.path, recursive=args.recursive, force=args.force)
    except (SSHConnectionError, Exception) as exc:
        result = {
            "success": False,
            "error": str(exc),
            "path": args.path,
            "host": args.host,
        }
        log_action("file_rm_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {
        "success": True,
        "path": args.path,
        "recursive": args.recursive,
        "force": args.force,
        "host": args.host,
    }
    log_action("file_rm", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_file_cp(args: argparse.Namespace) -> int:
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            sftp_cp(
                client,
                args.src,
                args.dst,
                recursive=args.recursive,
                overwrite=args.overwrite,
            )
    except FileExistsError as exc:
        result = {
            "success": False,
            "error": f"destination exists: {exc}",
            "src": args.src,
            "dst": args.dst,
            "host": args.host,
        }
        log_action("file_cp_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1
    except IsADirectoryError as exc:
        result = {
            "success": False,
            "error": f"source is a directory (use --recursive): {exc}",
            "src": args.src,
            "dst": args.dst,
            "host": args.host,
        }
        log_action("file_cp_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1
    except (SSHConnectionError, Exception) as exc:
        result = {
            "success": False,
            "error": str(exc),
            "src": args.src,
            "dst": args.dst,
            "host": args.host,
        }
        log_action("file_cp_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {
        "success": True,
        "src": args.src,
        "dst": args.dst,
        "recursive": args.recursive,
        "overwrite": args.overwrite,
        "host": args.host,
    }
    log_action("file_cp", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_shell_open(args: argparse.Namespace) -> int:
    session = args.session
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            chk = run_exec_command(
                client, "command -v tmux >/dev/null 2>&1; echo $?", timeout=args.timeout
            )
            if (chk.get("stdout", "") or "").strip() != "0":
                result = {
                    "success": False,
                    "reason": "tmux_not_found",
                    "host": args.host,
                    "session": session,
                    "hint": "install tmux on the remote host (apt-get install -y tmux) or use shell-run",
                }
                log_action("shell_open", result)
                sys.stdout.buffer.write(to_json_bytes(result))
                sys.stdout.write("\n")
                return 1

            res = run_exec_command(
                client, build_open_cmd(session, cwd=args.cwd), timeout=args.timeout
            )
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "host": args.host,
            "session": session,
        }
        log_action("shell_open_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    if not res.get("success"):
        result = {
            "success": False,
            "host": args.host,
            "session": session,
            "reason": res.get("reason"),
            "stdout": res.get("stdout", ""),
            "stderr": res.get("stderr", ""),
        }
        log_action("shell_open", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {"success": True, "host": args.host, "session": session, "status": "open"}
    log_action("shell_open", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0


def cmd_shell_send(args: argparse.Namespace) -> int:
    marker = f"__AI_DONE__{os.getpid()}__{int(now_ms())}__"
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            send = run_exec_command(
                client,
                build_send_cmd(args.session, args.cmd, marker),
                timeout=args.timeout,
            )
            cap = run_exec_command(
                client,
                build_capture_cmd(args.session, lines=args.capture_lines),
                timeout=args.timeout,
            )
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "host": args.host,
            "session": args.session,
        }
        log_action("shell_send_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    output = cap.get("stdout", "") or ""
    exit_code = tmux_parse_exit_code(output)
    success = bool(send.get("success") and exit_code == 0)

    result = {
        "success": success,
        "host": args.host,
        "session": args.session,
        "command": args.cmd,
        "output": output,
        "exit_code": exit_code,
        "timed_out": False,
        "reason": None,
    }
    capture_output(
        result,
        requested_path=args.output_file,
        max_inline_chars=args.max_inline_chars,
        prefix="shell-send",
    )
    log_action("shell_send", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0 if success else 1


def cmd_shell_close(args: argparse.Namespace) -> int:
    try:
        with ssh_client(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            key_path=args.key_path,
            passphrase=args.passphrase,
            timeout=args.timeout,
        ) as client:
            res = run_exec_command(
                client,
                f"tmux kill-session -t {shlex.quote(args.session)}",
                timeout=args.timeout,
            )
    except SSHConnectionError as exc:
        result = {
            "success": False,
            "error": str(exc),
            "host": args.host,
            "session": args.session,
        }
        log_action("shell_close_error", result)
        sys.stdout.buffer.write(to_json_bytes(result))
        sys.stdout.write("\n")
        return 1

    result = {
        "success": bool(res.get("success")),
        "host": args.host,
        "session": args.session,
        "status": "closed" if res.get("success") else "unknown",
        "stdout": res.get("stdout", ""),
        "stderr": res.get("stderr", ""),
        "exit_code": res.get("exit_code"),
        "reason": res.get("reason"),
    }
    log_action("shell_close", result)
    sys.stdout.buffer.write(to_json_bytes(result))
    sys.stdout.write("\n")
    return 0 if result.get("success") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-ssh", description="AI-friendly SSH CLI using Paramiko"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # connect
    p_connect = subparsers.add_parser("connect", help="Test SSH connection")
    _common_ssh_args(p_connect)
    p_connect.set_defaults(func=cmd_connect)

    # exec
    p_exec = subparsers.add_parser("exec", help="Run a non-interactive command")
    _common_ssh_args(p_exec)
    p_exec.add_argument("--cmd", required=True, help="Command to execute")
    p_exec.add_argument("--cwd", help="Remote working directory")
    p_exec.add_argument("--env", action="append", help="KEY=VALUE pairs", default=[])
    _add_output_capture_args(p_exec)
    p_exec.add_argument(
        "--allow-dangerous", action="store_true", help="Bypass dangerous command guard"
    )
    p_exec.set_defaults(func=cmd_exec)

    # sudo
    p_sudo = subparsers.add_parser("sudo", help="Run a command via sudo")
    _common_ssh_args(p_sudo)
    p_sudo.add_argument("--cmd", required=True, help="Command to execute with sudo")
    p_sudo.add_argument(
        "--sudo-password", help="Password for sudo (defaults to --password)"
    )
    _add_output_capture_args(p_sudo)
    p_sudo.add_argument(
        "--allow-dangerous", action="store_true", help="Bypass dangerous command guard"
    )
    p_sudo.set_defaults(func=cmd_sudo)

    # upload
    p_upload = subparsers.add_parser("upload", help="Upload a local file")
    _common_ssh_args(p_upload)
    p_upload.add_argument("--local", required=True, help="Local path")
    p_upload.add_argument("--remote", required=True, help="Remote path")
    p_upload.add_argument(
        "--overwrite", action="store_true", help="Overwrite remote file if exists"
    )
    p_upload.set_defaults(func=cmd_upload)

    # download
    p_download = subparsers.add_parser("download", help="Download a remote file")
    _common_ssh_args(p_download)
    p_download.add_argument("--remote", required=True, help="Remote path")
    p_download.add_argument("--local", required=True, help="Local path")
    p_download.add_argument(
        "--overwrite", action="store_true", help="Overwrite local file if exists"
    )
    p_download.set_defaults(func=cmd_download)

    # read
    p_read = subparsers.add_parser("read", help="Read a remote text file")
    _common_ssh_args(p_read)
    p_read.add_argument("--path", required=True, help="Remote file path")
    p_read.set_defaults(func=cmd_read)

    # write
    p_write = subparsers.add_parser("write", help="Write to a remote text file")
    _common_ssh_args(p_write)
    p_write.add_argument("--path", required=True, help="Remote file path")
    p_write.add_argument(
        "--content", help="Content to write (defaults to stdin if omitted)"
    )
    p_write.add_argument(
        "--append", action="store_true", help="Append instead of overwrite"
    )
    p_write.add_argument(
        "--backup", action="store_true", help="Backup existing file with .bak suffix"
    )
    p_write.set_defaults(func=cmd_write)

    # shell-run
    p_shell_run = subparsers.add_parser(
        "shell-run", help="Run a command via PTY shell with marker"
    )
    _common_ssh_args(p_shell_run)
    p_shell_run.add_argument("--cmd", required=True, help="Command to execute in shell")
    p_shell_run.add_argument(
        "--sudo-password",
        help="Optional sudo password for commands that prompt for it",
    )
    _add_output_capture_args(p_shell_run)
    p_shell_run.add_argument(
        "--allow-dangerous", action="store_true", help="Bypass dangerous command guard"
    )
    p_shell_run.set_defaults(func=cmd_shell_run)

    # stat
    p_stat = subparsers.add_parser("stat", help="Collect basic system information")
    _common_ssh_args(p_stat)
    p_stat.set_defaults(func=cmd_stat)

    # service-fail-summary
    p_sfs = subparsers.add_parser(
        "service-fail-summary", help="Summarize last service failure"
    )
    _common_ssh_args(p_sfs)
    p_sfs.add_argument("--unit", required=True, help="systemd unit name")
    p_sfs.add_argument(
        "--tail-lines",
        dest="tail_lines",
        type=int,
        default=20,
        help="journal tail lines",
    )
    p_sfs.set_defaults(func=cmd_service_fail_summary)

    # service-watch
    p_sw = subparsers.add_parser(
        "service-watch", help="Watch a systemd unit for restart loops"
    )
    _common_ssh_args(p_sw)
    p_sw.add_argument("--unit", required=True, help="systemd unit name")
    p_sw.add_argument("--duration", type=int, default=60, help="watch duration (s)")
    p_sw.add_argument("--interval", type=int, default=2, help="poll interval (s)")
    p_sw.add_argument(
        "--journal-tail",
        dest="journal_tail",
        type=int,
        default=5,
        help="journal tail lines on restart",
    )
    p_sw.add_argument(
        "--flap-threshold",
        dest="flap_threshold",
        type=int,
        default=2,
        help="restart delta to call flapping",
    )
    p_sw.set_defaults(func=cmd_service_watch)

    # job-run
    p_jr = subparsers.add_parser(
        "job-run", help="Run a long command as a background job"
    )
    _common_ssh_args(p_jr)
    p_jr.add_argument("--cmd", required=True, help="Command to execute")
    p_jr.add_argument("--cwd", help="Remote working directory")
    p_jr.add_argument(
        "--log",
        required=True,
        help="Remote log file path (stdout+stderr)",
    )
    p_jr.add_argument(
        "--exit-file",
        dest="exit_file",
        required=True,
        help="Remote path to write exit code",
    )
    p_jr.add_argument(
        "--allow-dangerous", action="store_true", help="Bypass dangerous command guard"
    )
    p_jr.set_defaults(func=cmd_job_run)

    # job-status
    p_js = subparsers.add_parser("job-status", help="Check status of a background job")
    _common_ssh_args(p_js)
    p_js.add_argument(
        "--job-id", dest="job_id", required=True, help="Job id from job-run"
    )
    p_js.add_argument(
        "--tail-lines",
        dest="tail_lines",
        type=int,
        default=20,
        help="Tail last N log lines",
    )
    p_js.set_defaults(func=cmd_job_status)

    # net-watch
    p_nw = subparsers.add_parser(
        "net-watch", help="Watch network throughput via /proc/net/dev"
    )
    _common_ssh_args(p_nw)
    p_nw.add_argument("--iface", default="auto", help="Interface name or auto")
    p_nw.add_argument("--duration", type=int, default=60, help="watch duration (s)")
    p_nw.add_argument("--interval", type=int, default=1, help="sample interval (s)")
    p_nw.add_argument(
        "--threshold-kbps",
        dest="threshold_kbps",
        type=int,
        default=200,
        help="RX kbps threshold for active_download",
    )
    p_nw.set_defaults(func=cmd_net_watch)

    # proc-watch
    p_pw = subparsers.add_parser(
        "proc-watch", help="Watch for process churn by pattern"
    )
    _common_ssh_args(p_pw)
    p_pw.add_argument(
        "--pattern", required=True, help="substring pattern to match in args"
    )
    p_pw.add_argument("--duration", type=int, default=60, help="watch duration (s)")
    p_pw.add_argument("--interval", type=int, default=1, help="sample interval (s)")
    p_pw.add_argument(
        "--min-lifetime-ms",
        dest="min_lifetime_ms",
        type=int,
        default=200,
        help="minimum lifetime to count (ms)",
    )
    p_pw.add_argument(
        "--unstable-threshold",
        dest="unstable_threshold",
        type=int,
        default=3,
        help="spawn count to call unstable",
    )
    p_pw.set_defaults(func=cmd_proc_watch)

    # write-json
    p_wj = subparsers.add_parser(
        "write-json", help="Write a remote JSON file after validation"
    )
    _common_ssh_args(p_wj)
    p_wj.add_argument("--path", required=True, help="Remote JSON file path")
    p_wj.add_argument(
        "--content",
        required=False,
        help="JSON content or @localfile (defaults to stdin if omitted)",
    )
    p_wj.add_argument(
        "--backup", action="store_true", help="Backup existing file with .bak suffix"
    )
    p_wj.set_defaults(func=cmd_write_json)

    # file-exists
    p_fe = subparsers.add_parser(
        "file-exists", help="Check whether a remote path exists"
    )
    _common_ssh_args(p_fe)
    p_fe.add_argument("--path", required=True, help="Remote path")
    p_fe.set_defaults(func=cmd_file_exists)

    # file-ls
    p_fl = subparsers.add_parser("file-ls", help="List a remote directory")
    _common_ssh_args(p_fl)
    p_fl.add_argument("--path", required=True, help="Remote directory")
    p_fl.add_argument("--all", action="store_true", help="Include dotfiles")
    p_fl.add_argument("--long", action="store_true", help="Include metadata")
    p_fl.set_defaults(func=cmd_file_ls)

    # file-mkdir
    p_fm = subparsers.add_parser("file-mkdir", help="Create a remote directory")
    _common_ssh_args(p_fm)
    p_fm.add_argument("--path", required=True, help="Remote directory")
    p_fm.add_argument("--parents", action="store_true", help="Create parents as needed")
    p_fm.set_defaults(func=cmd_file_mkdir)

    # file-mv
    p_fmv = subparsers.add_parser("file-mv", help="Move/rename a remote path")
    _common_ssh_args(p_fmv)
    p_fmv.add_argument("--src", required=True, help="Source path")
    p_fmv.add_argument("--dst", required=True, help="Destination path")
    p_fmv.add_argument(
        "--overwrite", action="store_true", help="Overwrite if destination exists"
    )
    p_fmv.set_defaults(func=cmd_file_mv)

    # file-rm
    p_frm = subparsers.add_parser("file-rm", help="Remove a remote file or directory")
    _common_ssh_args(p_frm)
    p_frm.add_argument("--path", required=True, help="Remote path")
    p_frm.add_argument(
        "--recursive", action="store_true", help="Remove directories recursively"
    )
    p_frm.add_argument("--force", action="store_true", help="Ignore missing paths")
    p_frm.set_defaults(func=cmd_file_rm)

    # file-cp
    p_fcp = subparsers.add_parser("file-cp", help="Copy a remote file or directory")
    _common_ssh_args(p_fcp)
    p_fcp.add_argument("--src", required=True, help="Source path")
    p_fcp.add_argument("--dst", required=True, help="Destination path")
    p_fcp.add_argument(
        "--recursive", action="store_true", help="Copy directories recursively"
    )
    p_fcp.add_argument("--overwrite", action="store_true", help="Overwrite destination")
    p_fcp.set_defaults(func=cmd_file_cp)

    # shell-open
    p_so = subparsers.add_parser(
        "shell-open", help="Open a persistent shell session (tmux backend)"
    )
    _common_ssh_args(p_so)
    p_so.add_argument("--session", required=True, help="Session id")
    p_so.add_argument("--cwd", help="Remote working directory")
    p_so.set_defaults(func=cmd_shell_open)

    # shell-send
    p_ss = subparsers.add_parser(
        "shell-send", help="Send a command to a persistent shell session"
    )
    _common_ssh_args(p_ss)
    p_ss.add_argument("--session", required=True, help="Session id")
    p_ss.add_argument("--cmd", required=True, help="Command to send")
    p_ss.add_argument(
        "--capture-lines",
        dest="capture_lines",
        type=int,
        default=200,
        help="How many lines to capture from tmux pane",
    )
    _add_output_capture_args(p_ss)
    p_ss.set_defaults(func=cmd_shell_send)

    # shell-close
    p_sc = subparsers.add_parser("shell-close", help="Close a persistent shell session")
    _common_ssh_args(p_sc)
    p_sc.add_argument("--session", required=True, help="Session id")
    p_sc.set_defaults(func=cmd_shell_close)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1
    return func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
