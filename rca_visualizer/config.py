import os
import shlex
from pathlib import Path

DEFAULT_ENV_FILE = Path("/etc/rca-hdmi-visualizer.env")
DEFAULT_SECRETS_FILE = Path("/etc/rca-hdmi-visualizer.secrets")


def parse_env_file(path):
    values = {}
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


def merged_env(paths):
    values = {}
    for path in paths:
        values.update(parse_env_file(path))
    values.update(os.environ)
    return values


def get_bool(values, key, default=False):
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def get_int(values, key, default):
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    return int(raw)


def get_float(values, key, default):
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    return float(raw)


class RuntimeConfig:
    def __init__(self, values):
        self.values = values

    @classmethod
    def load(cls, env_file=DEFAULT_ENV_FILE, secrets_file=DEFAULT_SECRETS_FILE):
        return cls(merged_env([env_file, secrets_file]))

    def str(self, key, default=""):
        return self.values.get(key, default)

    def bool(self, key, default=False):
        return get_bool(self.values, key, default)

    def int(self, key, default):
        return get_int(self.values, key, default)

    def float(self, key, default):
        return get_float(self.values, key, default)
