import os
import stat
from typing import Any, Dict, List, Optional

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


def path_type(client: "paramiko.SSHClient", path: str) -> str:
    with sftp(client) as s:
        st = s.stat(path)
    mode = st.st_mode
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "link"
    return "other"


def list_dir(
    client: "paramiko.SSHClient", path: str, *, all: bool = False, long: bool = False
) -> List[Dict[str, Any]]:
    with sftp(client) as s:
        attrs = s.listdir_attr(path)

    entries: List[Dict[str, Any]] = []
    for a in attrs:
        name = a.filename
        if not all and name.startswith("."):
            continue
        entry: Dict[str, Any] = {"name": name}
        if long:
            mode = a.st_mode
            if stat.S_ISDIR(mode):
                typ = "dir"
            elif stat.S_ISREG(mode):
                typ = "file"
            elif stat.S_ISLNK(mode):
                typ = "link"
            else:
                typ = "other"
            entry.update(
                {
                    "type": typ,
                    "size": int(a.st_size),
                    "mtime": int(a.st_mtime),
                    "mode": int(mode),
                }
            )
        entries.append(entry)
    return entries


def mkdir(client: "paramiko.SSHClient", path: str, *, parents: bool = False) -> None:
    with sftp(client) as s:
        if not parents:
            s.mkdir(path)
            return

        parts = [p for p in path.split("/") if p]
        cur = "/" if path.startswith("/") else ""
        for part in parts:
            cur = (cur.rstrip("/") + "/" + part) if cur else part
            try:
                s.stat(cur)
            except IOError:
                s.mkdir(cur)


def mv(
    client: "paramiko.SSHClient", src: str, dst: str, *, overwrite: bool = False
) -> None:
    with sftp(client) as s:
        if not overwrite:
            try:
                s.stat(dst)
                raise FileExistsError(dst)
            except IOError:
                pass
        s.posix_rename(src, dst)


def rm(
    client: "paramiko.SSHClient",
    path: str,
    *,
    recursive: bool = False,
    force: bool = False,
) -> None:
    with sftp(client) as s:
        try:
            st = s.stat(path)
        except IOError:
            if force:
                return
            raise

        if stat.S_ISDIR(st.st_mode):
            if not recursive:
                s.rmdir(path)
                return
            for name in s.listdir(path):
                if name in (".", ".."):
                    continue
                child = path.rstrip("/") + "/" + name
                rm(client, child, recursive=True, force=force)
            try:
                s.rmdir(path)
            except IOError:
                if not force:
                    raise
        else:
            try:
                s.remove(path)
            except IOError:
                if not force:
                    raise


def cp(
    client: "paramiko.SSHClient",
    src: str,
    dst: str,
    *,
    recursive: bool = False,
    overwrite: bool = False,
) -> None:
    with sftp(client) as s:
        st = s.stat(src)
        if stat.S_ISDIR(st.st_mode):
            if not recursive:
                raise IsADirectoryError(src)
            try:
                s.stat(dst)
            except IOError:
                s.mkdir(dst)
            for name in s.listdir(src):
                if name in (".", ".."):
                    continue
                cp(
                    client,
                    src.rstrip("/") + "/" + name,
                    dst.rstrip("/") + "/" + name,
                    recursive=True,
                    overwrite=overwrite,
                )
            return

        # file
        if not overwrite:
            try:
                s.stat(dst)
                raise FileExistsError(dst)
            except IOError:
                pass
        with s.open(src, "rb") as rf:
            with s.open(dst, "wb") as wf:
                while True:
                    chunk = rf.read(1024 * 1024)
                    if not chunk:
                        break
                    wf.write(chunk)
