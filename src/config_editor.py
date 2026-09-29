"""Read, validate and atomically write config.yaml on behalf of the web GUI.

Validation goes through ``load_settings`` on a candidate file so the exact
production rules (including environment overrides) decide what is accepted.
Note that ``yaml.safe_dump`` does not preserve comments; a timestamped backup
of the previous file is kept for that reason.
"""

import copy
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from pydantic_settings import SettingsError

from src.functions import get_env_value
from src.settings import AppSettings, load_settings

MASK = "********"
SERVER_TYPES = ("plex", "jellyfin", "emby")
_SECRET_KEYS = ("token", "password")
MAX_BACKUPS = 5


class ConfigError(Exception):
    """Candidate configuration was rejected; ``errors`` explains why."""

    def __init__(self, errors: list[dict[str, str]]):
        super().__init__("configuration rejected")
        self.errors = errors


def config_path() -> Path:
    return Path(get_env_value(None, "YAML_FILE", "config.yaml"))


def read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def mask_secrets(config: dict[str, Any]) -> dict[str, Any]:
    masked = copy.deepcopy(config)
    for kind in SERVER_TYPES:
        for server in masked.get(kind) or []:
            for key in _SECRET_KEYS:
                if server.get(key):
                    server[key] = MASK
    if masked.get("gui_token"):
        masked["gui_token"] = MASK
    return masked


def merge_secrets(new: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """Replace MASK placeholders in ``new`` with the stored secret values."""
    merged = copy.deepcopy(new)
    for kind in SERVER_TYPES:
        stored = {s.get("name"): s for s in old.get(kind) or [] if isinstance(s, dict)}
        for server in merged.get(kind) or []:
            if not isinstance(server, dict):
                continue
            for key in _SECRET_KEYS:
                if server.get(key) == MASK:
                    previous = stored.get(server.get("name"), {}).get(key)
                    if previous:
                        server[key] = previous
                    else:
                        server.pop(key)
    if merged.get("gui_token") == MASK:
        if old.get("gui_token"):
            merged["gui_token"] = old["gui_token"]
        else:
            merged.pop("gui_token")
    return merged


def env_locked_keys() -> list[str]:
    """Top-level settings (and server tokens) currently supplied by environment."""
    upper = {key.upper() for key in os.environ}
    locked = [
        name
        for name in AppSettings.model_fields
        if f"JPW_{name.upper()}" in upper or name.upper() in upper
    ]
    tokens = os.environ.get("JPW_SERVER_TOKENS") or os.environ.get("SERVER_TOKENS")
    if tokens:
        try:
            locked.extend(f"token:{name}" for name in json.loads(tokens))
        except (ValueError, TypeError):
            pass
    return locked


def _format_errors(error: Exception) -> list[dict[str, str]]:
    if isinstance(error, ValidationError):
        return [
            {
                "loc": ".".join(str(part) for part in detail["loc"]) or "configuration",
                "msg": detail["msg"],
            }
            for detail in error.errors(include_context=False, include_input=False)
        ]
    if isinstance(error, SettingsError):
        return [{"loc": "configuration", "msg": "a configuration source could not be read"}]
    return [{"loc": "configuration", "msg": type(error).__name__}]


def validate_candidate(config: dict[str, Any], directory: Path) -> AppSettings:
    """Load ``config`` through the real settings pipeline without installing it."""
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".config-check-", suffix=".yaml")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
        try:
            return load_settings(yaml_file=tmp, auto_migrate=False)
        except (ValidationError, SettingsError, yaml.YAMLError) as error:
            raise ConfigError(_format_errors(error)) from None
    finally:
        tmp.unlink(missing_ok=True)


def save_config(path: Path, new_config: dict[str, Any]) -> AppSettings:
    """Validate then atomically replace ``path``, keeping a timestamped backup."""
    old = read_config(path)
    config = merge_secrets(new_config, old)
    directory = path.resolve().parent
    settings = validate_candidate(config, directory)

    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        backup.write_bytes(path.read_bytes())
        backup.chmod(0o600)
        for stale in sorted(path.parent.glob(f"{path.name}.bak-*"))[:-MAX_BACKUPS]:
            stale.unlink(missing_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return settings
