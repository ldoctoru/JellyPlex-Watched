import json
import threading
from http.client import HTTPConnection
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src import web
from src.main import RunState
from src.settings import AppSettings


BASE = {
    "jellyfin": [
        {"name": "a", "baseurl": "http://a", "token": "t", "sync_to": ["b"]},
        {"name": "b", "baseurl": "http://b", "token": "t", "sync_to": ["a"]},
    ]
}


def make_controller(**overrides):
    settings = AppSettings.model_validate({**BASE, "gui_enabled": True, **overrides})
    return web.Controller(settings, state=RunState())


@pytest.fixture
def serve():
    servers = []

    def start(controller, port=0):
        controller.settings = controller.settings.model_copy(update={"gui_port": port})
        httpd = web.start_web_server(controller)
        servers.append(httpd)
        return httpd.server_address[1]

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def request(port, method, path, headers=None, host=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = dict(headers or {})
    if host:
        hdrs["Host"] = host
    conn.request(method, path, headers=hdrs)
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res.status, body


def test_non_loopback_host_requires_token():
    with pytest.raises(ValidationError):
        AppSettings.model_validate(
            {**BASE, "gui_enabled": True, "gui_host": "0.0.0.0"}
        )
    AppSettings.model_validate(
        {**BASE, "gui_enabled": True, "gui_host": "0.0.0.0", "gui_token": "x"}
    )


def test_status_and_static_assets(serve):
    port = serve(make_controller())
    status, body = request(port, "GET", "/api/status")
    data = json.loads(body)
    assert status == 200
    assert data["dryrun"] is True and data["running"] is False
    assert request(port, "GET", "/")[0] == 200
    assert request(port, "GET", "/app.js")[0] == 200


def test_rejects_non_loopback_host_header_without_token(serve):
    port = serve(make_controller())
    assert request(port, "GET", "/api/status", host="evil.example")[0] == 401
    assert request(port, "GET", "/", host="evil.example")[0] == 403


def test_post_requires_custom_header(serve):
    controller = make_controller()
    port = serve(controller)
    assert request(port, "POST", "/api/run")[0] == 403
    assert not controller.trigger.is_set()
    status, _ = request(port, "POST", "/api/run", {"X-Requested-With": "jpw"})
    assert status == 202
    assert controller.trigger.is_set()


def test_run_conflicts_while_run_in_progress(serve):
    controller = make_controller()
    port = serve(controller)
    with controller.run_lock:
        status, _ = request(port, "POST", "/api/run", {"X-Requested-With": "jpw"})
    assert status == 409


def test_token_required_when_configured(serve):
    port = serve(make_controller(gui_token="s3cret"))
    assert request(port, "GET", "/api/status")[0] == 401
    assert request(port, "GET", "/api/status", {"Authorization": "Bearer nope"})[0] == 401
    assert request(port, "GET", "/api/status", {"Authorization": "Bearer s3cret"})[0] == 200


def test_preview_builds_plan_without_applying(monkeypatch):
    controller = make_controller()
    built = threading.Event()

    def fake_build(settings, average):
        built.set()
        return {}

    monkeypatch.setattr("src.main.build_plan", fake_build)
    apply = []
    monkeypatch.setattr("src.main.apply_plan", lambda plan: apply.append(plan))
    assert controller.start_preview()
    assert built.wait(5)
    for _ in range(100):
        if not controller.previewing:
            break
        threading.Event().wait(0.02)
    assert controller.preview == {"total": 0, "truncated": False, "rows": []}
    assert apply == []
    assert not controller.run_lock.locked()


def test_summarize_plan_flattens_updates():
    from datetime import datetime, timezone

    from src.watched import (
        LibraryData,
        MediaIdentifiers,
        MediaItem,
        WatchedStatus,
    )

    status = WatchedStatus(completed=True, time=0, viewed_date=datetime.now(timezone.utc))
    library = LibraryData(
        title="Movies",
        movies=[MediaItem(identifiers=MediaIdentifiers(title="Heat"), status=status)],
    )
    update = SimpleNamespace(
        target_user="alice", target_library="Movies", library_data=library
    )
    class Dest:
        server_settings = SimpleNamespace(name="jf")

    dest = Dest()
    summary = web.summarize_plan({dest: {"plex": [update]}})
    assert summary["total"] == 1
    assert summary["rows"][0] == {
        "title": "Heat",
        "user": "alice",
        "library": "Movies",
        "source": "plex",
        "destination": "jf",
        "change": "watched",
    }


def test_explain_scope_reports_each_step():
    settings = AppSettings.model_validate({**BASE, "blacklist_users": ["mallory"]})
    ok = settings.explain_scope("alice", "Movies", "a", "b")
    assert ok["verdict"] is True
    assert [s["check"] for s in ok["steps"]] == [
        "Direction enabled",
        "User filters",
        "Library filters",
        "Target user",
        "Target library",
    ]
    blocked = settings.explain_scope("mallory", "Movies", "a", "b")
    assert blocked["verdict"] is False
    assert not next(s for s in blocked["steps"] if s["check"] == "User filters")["ok"]
    unknown = settings.explain_scope("alice", "Movies", "a", "zzz")
    assert unknown["verdict"] is False and unknown["steps"][0]["check"] == "Servers"


def test_explain_scope_matches_should_sync_scope():
    settings = AppSettings.model_validate(
        {**BASE, "whitelist_library_types": ["movie"]}
    )
    for lib_type, target_type in [("movie", "movie"), ("show", "show"), (None, None)]:
        explained = settings.explain_scope(
            "alice", "Movies", "a", "b",
            library_type=lib_type, target_library_type=target_type,
        )
        authorized = settings.should_sync_scope(
            "alice", "Movies", "a", "b",
            library_type=lib_type, target_library_type=target_type,
        )
        assert explained["verdict"] == authorized


def test_explain_endpoint_and_validation(serve):
    port = serve(make_controller())
    hdr = {"X-Requested-With": "jpw"}

    def post(payload):
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/explain", json.dumps(payload), hdr)
        res = conn.getresponse()
        return res.status, json.loads(res.read())

    status, body = post({"user": "alice", "library": "Movies", "from": "a", "to": "b"})
    assert status == 200 and "steps" in body
    assert post({"user": "", "library": "x", "from": "a", "to": "b"})[0] == 400
    assert post({"user": 1, "library": "x", "from": "a", "to": "b"})[0] == 400
