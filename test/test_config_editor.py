import json
from http.client import HTTPConnection

import pytest
import yaml

from src import config_editor, web
from src.main import RunState
from src.settings import AppSettings

BASE = {
    "jellyfin": [
        {"name": "a", "baseurl": "http://a", "token": "secret-a", "sync_to": ["b"]},
        {"name": "b", "baseurl": "http://b", "token": "secret-b", "sync_to": ["a"]},
    ],
    "user_mappings": [
        {
            "canonical": "alice",
            "aliases": [
                {"server": "a", "username": "al"},
                {"server": "b", "username": "alice"},
            ],
        }
    ],
}


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    for key in list(__import__("os").environ):
        if key.upper().startswith("JPW_") or key.upper() in {"ENV_FILE", "YAML_FILE"}:
            monkeypatch.delenv(key, raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("# my comment\n" + yaml.safe_dump(BASE), encoding="utf-8")
    monkeypatch.setenv("YAML_FILE", str(path))
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "missing.env"))
    return path


def test_mask_and_merge_round_trip(cfg_file):
    stored = config_editor.read_config(cfg_file)
    masked = config_editor.mask_secrets(stored)
    assert masked["jellyfin"][0]["token"] == config_editor.MASK
    assert stored["jellyfin"][0]["token"] == "secret-a"
    assert config_editor.merge_secrets(masked, stored) == stored


def test_save_keeps_secrets_writes_backup_and_preserves_rules(cfg_file):
    new = config_editor.mask_secrets(config_editor.read_config(cfg_file))
    new["dryrun"] = False
    config_editor.save_config(cfg_file, new)
    saved = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert saved["dryrun"] is False
    assert saved["jellyfin"][0]["token"] == "secret-a"
    assert saved["user_mappings"] == BASE["user_mappings"]
    backups = list(cfg_file.parent.glob("config.yaml.bak-*"))
    assert len(backups) == 1 and "# my comment" in backups[0].read_text()
    assert (cfg_file.stat().st_mode & 0o777) == 0o600
    assert not list(cfg_file.parent.glob(".config-*"))


def test_invalid_config_is_rejected_and_file_untouched(cfg_file):
    before = cfg_file.read_text(encoding="utf-8")
    new = config_editor.mask_secrets(config_editor.read_config(cfg_file))
    new["jellyfin"][0]["sync_to"] = ["nope"]
    with pytest.raises(config_editor.ConfigError) as exc:
        config_editor.save_config(cfg_file, new)
    assert exc.value.errors and all("msg" in e for e in exc.value.errors)
    assert "secret" not in json.dumps(exc.value.errors)
    assert cfg_file.read_text(encoding="utf-8") == before
    assert not list(cfg_file.parent.glob("config.yaml.bak-*"))


def test_env_locked_keys(monkeypatch):
    monkeypatch.setenv("JPW_DRYRUN", "false")
    monkeypatch.setenv("JPW_SERVER_TOKENS", '{"a": "x"}')
    locked = config_editor.env_locked_keys()
    assert "dryrun" in locked and "token:a" in locked


def test_backups_are_pruned(cfg_file):
    for i in range(8):
        (cfg_file.parent / f"config.yaml.bak-2020010{i}-000000").write_text("x")
    config_editor.save_config(cfg_file, config_editor.read_config(cfg_file))
    assert len(list(cfg_file.parent.glob("config.yaml.bak-*"))) == config_editor.MAX_BACKUPS


def test_http_get_masks_and_put_saves(cfg_file):
    settings = AppSettings.model_validate({**BASE, "gui_enabled": True, "gui_port": 1})
    controller = web.Controller(settings, state=RunState())
    settings = settings.model_copy(update={"gui_port": 0})
    controller.settings = settings
    httpd = web.start_web_server(controller)
    port = httpd.server_address[1]
    try:
        def call(method, path, body=None):
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            headers = {"X-Requested-With": "jpw", "Content-Type": "application/json"}
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            res = conn.getresponse()
            data = json.loads(res.read())
            conn.close()
            return res.status, data

        status, data = call("GET", "/api/config")
        assert status == 200
        assert "secret-a" not in json.dumps(data)
        assert data["config"]["jellyfin"][0]["token"] == data["mask"]

        data["config"]["sleep_duration"] = 60
        status, _ = call("PUT", "/api/config", data["config"])
        assert status == 200 and controller.reload_pending
        assert yaml.safe_load(cfg_file.read_text())["sleep_duration"] == 60

        data["config"]["jellyfin"][0]["sync_to"] = ["missing"]
        status, body = call("PUT", "/api/config", data["config"])
        assert status == 422 and body["errors"]

        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("PUT", "/api/config", "{}", {})
        assert conn.getresponse().status == 403
    finally:
        httpd.shutdown()
        httpd.server_close()
