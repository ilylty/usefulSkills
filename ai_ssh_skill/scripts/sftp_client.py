from contextlib import contextmanager
from typing import Iterator


@contextmanager
def sftp(client: "paramiko.SSHClient") -> Iterator["paramiko.SFTPClient"]:
    sftp_client = client.open_sftp()
    try:
        yield sftp_client
    finally:
        try:
            sftp_client.close()
        except Exception:
            pass
