import re
import time
import uuid
from typing import Any, Dict, List, Optional

from ssh_client import _import_paramiko


class ShellSession:
    """Minimal invoke_shell-based command runner using a marker strategy.

    This class is intentionally small but supports a few higher-level
    capabilities useful for AI:

    - run(): single command execution with marker + optional sudo 密码
    - read_until(): 读到匹配某个正则或超时
    - expect(): 多个正则里任意一个匹配
    - run_interactive(): 根据 steps 列表执行 send/expect 序列
    """

    def __init__(
        self,
        client: "paramiko.SSHClient",
        *,
        term: str = "xterm",
        width: int = 160,
        height: int = 48,
        encoding: str = "utf-8",
    ) -> None:
        paramiko = _import_paramiko()
        transport = client.get_transport()
        if transport is None:
            raise RuntimeError("SSH transport not available")

        channel = transport.open_session()
        channel.get_pty(term=term, width=width, height=height)
        channel.invoke_shell()

        self._channel = channel
        self._encoding = encoding

        # Drain initial banner/prompt with a small delay window
        time_limit = time.monotonic() + 2.0
        while time.monotonic() < time_limit and channel.recv_ready():
            channel.recv(4096)

    def close(self) -> None:
        try:
            self._channel.close()
        except Exception:
            pass

    def interrupt(self) -> None:
        # Ctrl+C
        self._channel.send("\x03")

    def send(self, text: str) -> None:
        """Send raw text to the shell, ensuring there is a trailing newline."""

        if not text.endswith("\n"):
            text = text + "\n"
        self._channel.send(text)

    def read_until(self, pattern: str, timeout: int = 10) -> Dict[str, Any]:
        """Read from the shell until regex pattern matches or timeout.

        Returns a structured dict so AI 可以根据 timed_out / match 等字段做决策。
        """

        compiled = re.compile(pattern)
        buffer = ""
        started = time.monotonic()
        timed_out = False

        while True:
            if self._channel.recv_ready():
                data = self._channel.recv(4096)
                if not data:
                    break
                chunk = data.decode(self._encoding, errors="replace")
                buffer += chunk

                m = compiled.search(buffer)
                if m:
                    return {
                        "success": True,
                        "pattern": pattern,
                        "match": m.group(0),
                        "buffer": buffer,
                        "timed_out": False,
                    }
            elif self._channel.closed or self._channel.exit_status_ready():
                break

            if time.monotonic() - started > timeout:
                timed_out = True
                break

            time.sleep(0.05)

        return {
            "success": False,
            "pattern": pattern,
            "match": None,
            "buffer": buffer,
            "timed_out": timed_out,
        }

    def expect(self, patterns: List[str], timeout: int = 10) -> Dict[str, Any]:
        """Wait until any of the given regex patterns matches.

        Returns index of matched pattern and the accumulated buffer.
        """

        compiled = [re.compile(p) for p in patterns]
        buffer = ""
        started = time.monotonic()
        timed_out = False

        while True:
            if self._channel.recv_ready():
                data = self._channel.recv(4096)
                if not data:
                    break
                chunk = data.decode(self._encoding, errors="replace")
                buffer += chunk

                for idx, regex in enumerate(compiled):
                    m = regex.search(buffer)
                    if m:
                        return {
                            "success": True,
                            "index": idx,
                            "pattern": patterns[idx],
                            "match": m.group(0),
                            "buffer": buffer,
                            "timed_out": False,
                        }
            elif self._channel.closed or self._channel.exit_status_ready():
                break

            if time.monotonic() - started > timeout:
                timed_out = True
                break

            time.sleep(0.05)

        return {
            "success": False,
            "index": None,
            "pattern": None,
            "match": None,
            "buffer": buffer,
            "timed_out": timed_out,
        }

    def run_interactive(
        self, steps: List[Dict[str, str]], timeout_per_step: int = 10
    ) -> Dict[str, Any]:
        """Run a sequence of {send, expect} steps.

        steps 形如：[{"send": "sudo su -", "expect": "[Pp]assword"}, ...]
        每一步内部使用 read_until()。
        """

        results: List[Dict[str, Any]] = []

        for step in steps:
            send_text = step.get("send", "")
            expect_pattern = step.get("expect")

            if send_text:
                self.send(send_text)

            if expect_pattern:
                res = self.read_until(expect_pattern, timeout=timeout_per_step)
            else:
                res = {
                    "success": True,
                    "pattern": None,
                    "match": None,
                    "buffer": "",
                    "timed_out": False,
                }

            results.append({"step": step, "result": res})

        overall_success = all(item["result"]["success"] for item in results)

        return {"success": overall_success, "steps": results}

    def run(
        self,
        command: str,
        timeout: int = 30,
        sudo_password: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a single command in the shell using marker-based completion.

        If sudo_password is provided, 简单检测 password 提示后自动输入密码。
        """

        marker = f"__AI_DONE__{uuid.uuid4().hex}__"
        wrapped = f"{command}\nprintf '\n{marker}%s\n' $?\n"

        self._channel.send(wrapped)

        buffer = ""
        started = time.monotonic()
        exit_code: Optional[int] = None
        timed_out = False
        password_sent = False

        marker_re = re.compile(re.escape(marker) + r"(\d+)")
        password_re = re.compile(r"[Pp]assword[^:]*:")

        while True:
            if self._channel.recv_ready():
                data = self._channel.recv(4096)
                if not data:
                    break
                chunk = data.decode(self._encoding, errors="replace")
                buffer += chunk

                # sudo password prompt handling
                if sudo_password and not password_sent:
                    if password_re.search(buffer):
                        self._channel.send(sudo_password + "\n")
                        password_sent = True

                m = marker_re.search(buffer)
                if m:
                    exit_code = int(m.group(1))
                    break
            elif self._channel.closed or self._channel.exit_status_ready():
                break

            if time.monotonic() - started > timeout:
                timed_out = True
                break

            time.sleep(0.05)

        duration_ms = int((time.monotonic() - started) * 1000)

        # Strip marker line from output
        cleaned = marker_re.sub("", buffer)

        success = bool(exit_code == 0 and not timed_out)

        # PTY 模式下 stdout/stderr 实际混在一起，这里约定：
        # - 正常成功时，把全部输出放在 stdout，stderr 为空；
        # - 失败或超时时，stderr 里也带上一份同样的文本，避免结果里 stderr 完全空白。
        stderr_text = "" if success and not timed_out else cleaned

        return {
            "success": success,
            "command": command,
            "output": cleaned,
            "stdout": cleaned,
            "stderr": stderr_text,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "interrupted": False,
            "duration_ms": duration_ms,
        }
