import pytest

from src import healthcheck, web
from src.main import RunState
from src.settings import AppSettings

BASE = {
    "jellyfin": [
        {"name": "a", "baseurl": "http://a", "token": "t", "sync_to": ["b"]},
        {"name": "b", "baseurl": "http://b", "token": "t", "sync_to": ["a"]},
    ]
}


def test_disabled_gui_is_healthy_without_probing():
    assert healthcheck.check(AppSettings.model_validate(BASE)) is True


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "http://127.0.0.1:9000/healthz"),
        ("0.0.0.0", "http://127.0.0.1:9000/healthz"),
        ("::", "http://127.0.0.1:9000/healthz"),
        ("::1", "http://[::1]:9000/healthz"),
    ],
)
def test_probe_url_targets_loopback(host, expected):
    settings = AppSettings.model_validate(
        {**BASE, "gui_enabled": True, "gui_host": host, "gui_port": 9000, "gui_token": "t"}
    )
    assert healthcheck.probe_url(settings) == expected


def test_enabled_gui_is_unhealthy_when_nothing_listens():
    settings = AppSettings.model_validate({**BASE, "gui_enabled": True, "gui_port": 1})
    assert healthcheck.check(settings) is False


def test_enabled_gui_is_healthy_when_server_answers():
    settings = AppSettings.model_validate({**BASE, "gui_enabled": True, "gui_port": 1})
    controller = web.Controller(settings, state=RunState())
    controller.settings = settings.model_copy(update={"gui_port": 0})
    httpd = web.start_web_server(controller)
    try:
        port = httpd.server_address[1]
        live = settings.model_copy(update={"gui_port": port})
        assert healthcheck.check(live) is True
    finally:
        httpd.shutdown()
        httpd.server_close()
