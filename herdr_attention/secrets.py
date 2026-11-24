"""Secrets in the GNOME keyring via ``secret-tool``.

Values are passed on stdin and never printed or logged. Each secret is
stored with attributes ``service=<keyring.service>`` and ``key=<name>``.
"""

from __future__ import annotations

import shutil
import subprocess

KNOWN_KEYS = {
    "ntfy-token": "ntfy access token used to send phone alerts",
    "ntfy-phone-password": "password for the ntfy phone app login (herdr-phone)",
    "dashboard-passphrase": "passphrase for the phone dashboard login",
}


class SecretError(RuntimeError):
    pass


class Keyring:
    def __init__(self, service: str):
        if not service:
            raise SecretError("keyring service name is empty")
        self.service = service

    @staticmethod
    def available() -> bool:
        return shutil.which("secret-tool") is not None

    def get(self, key: str) -> str | None:
        if not self.available():
            return None
        result = subprocess.run(
            ["secret-tool", "lookup", "service", self.service, "key", key],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout.rstrip("\n") or None

    def set(self, key: str, value: str) -> None:
        if not self.available():
            raise SecretError("secret-tool is not installed")
        if not value:
            raise SecretError("refusing to store an empty secret")
        label = f"{self.service}: {KNOWN_KEYS.get(key, key)}"
        result = subprocess.run(
            ["secret-tool", "store", "--label", label, "service", self.service, "key", key],
            input=value, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise SecretError(f"secret-tool store failed (exit {result.returncode})")

    def delete(self, key: str) -> None:
        if self.available():
            subprocess.run(["secret-tool", "clear", "service", self.service, "key", key],
                           capture_output=True, timeout=10)

    def has(self, key: str) -> bool:
        return self.get(key) is not None
