"""The single API key this proxy demands from its clients."""

from __future__ import annotations

import json
import os
import secrets
import threading

import app_paths

KEY_FILE = os.path.join(app_paths.user_config_dir(), "api-key.json")
KEY_PREFIX = "sk-ghcpexcel-"
SOURCE_GENERATED = "generated"
SOURCE_CUSTOM = "custom"


def _new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def validate_custom_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("key 必须是字符串")
    key = value.strip()
    if not 16 <= len(key) <= 128:
        raise ValueError("key 长度必须是 16-128 个字符")
    if not (key.isascii() and key.isprintable()):
        raise ValueError("key 只能包含可打印的 ASCII 字符，不能有空白")
    return key


class ApiKeyStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._state: dict[str, str] | None = None

    def _read(self) -> dict[str, str] | None:
        try:
            with open(self._path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        key = data.get("key") if isinstance(data, dict) else None
        if not isinstance(key, str) or not key:
            return None
        source = data.get("source")
        return {
            "key": key,
            "source": source
            if source in (SOURCE_GENERATED, SOURCE_CUSTOM)
            else SOURCE_GENERATED,
        }

    def _write(self, state: dict[str, str]) -> dict[str, str]:
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = f"{self._path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(temporary, self._path)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass
        self._state = state
        return dict(state)

    def load(self) -> dict[str, str]:
        with self._lock:
            if self._state is None:
                self._state = self._read()
            if self._state is None:
                return self._write({"key": _new_key(), "source": SOURCE_GENERATED})
            return dict(self._state)

    def regenerate(self) -> dict[str, str]:
        with self._lock:
            return self._write({"key": _new_key(), "source": SOURCE_GENERATED})

    def set_custom(self, value: object) -> dict[str, str]:
        key = validate_custom_key(value)
        with self._lock:
            return self._write({"key": key, "source": SOURCE_CUSTOM})

    def matches(self, candidate: str) -> bool:
        if not candidate:
            return False
        return secrets.compare_digest(candidate, self.load()["key"])


store = ApiKeyStore(KEY_FILE)
