import os
from typing import Dict, Optional

from sftp_client import sftp


def read_text_file(
    client: "paramiko.SSHClient", path: str, encoding: str = "utf-8"
) -> str:
    with sftp(client) as s:
        with s.open(path, "r") as f:
            data = f.read()
    if isinstance(data, bytes):
        return data.decode(encoding, errors="replace")
    return data


def write_text_file(
    client: "paramiko.SSHClient",
    path: str,
    content: str,
    *,
    encoding: str = "utf-8",
    append: bool = False,
    backup: bool = False,
) -> Dict[str, Optional[str]]:
    with sftp(client) as s:
        if backup:
            try:
                backup_path = path + ".bak"
                s.posix_rename(path, backup_path)
            except IOError:
                backup_path = None
        else:
            backup_path = None

        mode = "a" if append else "w"
        with s.open(path, mode) as f:
            if isinstance(content, str):
                f.write(content)
            else:
                f.write(content.decode(encoding, errors="replace"))

    return {"backup_path": backup_path}


def path_exists(client: "paramiko.SSHClient", path: str) -> bool:
    with sftp(client) as s:
        try:
            s.stat(path)
            return True
        except IOError:
            return False
