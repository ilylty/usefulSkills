import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from audit import log_action
from executor import run_exec_command, to_json_bytes
from file_ops import path_exists, read_text_file, write_text_file
from guard import DangerousCommandError, ensure_safe
from sftp_client import sftp
from shell import ShellSession
from ssh_client import SSHConnectionError, ssh_client, test_connection
from sudo import run_sudo_command


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
            result = run_sudo_command(
                client,
                args.cmd,
                password=sudo_password,
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
    content: str
    if args.content is not None:
        content = args.content
    else:
        content = sys.stdin.read()

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
    p_shell_run.add_argument(
        "--allow-dangerous", action="store_true", help="Bypass dangerous command guard"
    )
    p_shell_run.set_defaults(func=cmd_shell_run)

    # stat
    p_stat = subparsers.add_parser("stat", help="Collect basic system information")
    _common_ssh_args(p_stat)
    p_stat.set_defaults(func=cmd_stat)

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
