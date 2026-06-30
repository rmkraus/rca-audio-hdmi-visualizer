from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_ENV_FILE = Path("/etc/rca-hdmi-visualizer.env")
DEFAULT_SECRETS_FILE = Path("/etc/rca-hdmi-visualizer.secrets")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        try:
            parts = shlex.split(value, comments=False, posix=True)
            if len(parts) == 1:
                value = parts[0]
        except ValueError:
            pass
        values[key] = value
    return values


def merged_env(paths: Iterable[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        values.update(parse_env_file(path))
    values.update(os.environ)
    return values


def get_bool(values: dict[str, str], key: str, default: bool = False) -> bool:
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def get_int(values: dict[str, str], key: str, default: int) -> int:
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    return int(raw)


def get_float(values: dict[str, str], key: str, default: float) -> float:
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class RuntimeConfig:
    values: dict[str, str]

    @classmethod
    def load(
        cls,
        env_file: Path = DEFAULT_ENV_FILE,
        secrets_file: Path = DEFAULT_SECRETS_FILE,
    ) -> "RuntimeConfig":
        return cls(merged_env([env_file, secrets_file]))

    def str(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def bool(self, key: str, default: bool = False) -> bool:
        return get_bool(self.values, key, default)

    def int(self, key: str, default: int) -> int:
        return get_int(self.values, key, default)

    def float(self, key: str, default: float) -> float:
        return get_float(self.values, key, default)
