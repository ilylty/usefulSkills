import socket
from contextlib import contextmanager
from typing import Optional


class SSHConnectionError(Exception):
    """Raised when an SSH connection cannot be established."""


def _import_paramiko():  # lazy import to give nicer error if missing
    try:
        import paramiko  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime environment detail
        raise RuntimeError(
            "Paramiko is required. Install with: pip install paramiko"
        ) from exc
    return paramiko


@contextmanager
def ssh_client(
    host: str,
    port: int = 22,
    username: Optional[str] = None,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    passphrase: Optional[str] = None,
    timeout: int = 10,
) -> "paramiko.SSHClient":
    """Yield a connected Paramiko SSHClient.

    Supports password or key authentication. Host keys are not strictly
    verified here and unknown hosts are auto-added so the tool is easy to
    use as a generic remote executor.
    """

    paramiko = _import_paramiko()

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    pkey = None
    if key_path:
        try:
            pkey = paramiko.Ed25519Key.from_private_key_file(
                key_path, password=passphrase
            )
        except Exception:
            # Fallback to RSA if Ed25519 fails
            pkey = paramiko.RSAKey.from_private_key_file(key_path, password=passphrase)

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            pkey=pkey,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
    except (paramiko.SSHException, socket.error) as exc:
        client.close()
        raise SSHConnectionError(str(exc)) from exc

    try:
        yield client
    finally:
        try:
            client.close()
        except Exception:
            pass


def test_connection(**kwargs) -> bool:
    """Quickly test whether we can connect with the given parameters."""

    try:
        with ssh_client(**kwargs):
            return True
    except SSHConnectionError:
        return False
