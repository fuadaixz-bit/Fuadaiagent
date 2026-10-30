import base64
import importlib.util
import json
import os
import sys
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("TG_TOKEN", "test-token")
os.environ.setdefault("OWNER_ID", "123")
os.environ.setdefault("ONCE_LOCK_DIR", "/tmp/fuadbot-test-once")
Path(os.environ["ONCE_LOCK_DIR"]).mkdir(parents=True, exist_ok=True)

import pytest

APP_PATH = Path(__file__).with_name("app.py")
spec = importlib.util.spec_from_file_location("fuadbot_app_test", APP_PATH)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
# Avoid startup network calls and the production data file.
app_requests = __import__("requests")
_original_post = app_requests.post
app_requests.post = lambda *a, **kw: types.SimpleNamespace(status_code=200, json=lambda: {"ok": True})
os.environ["DATA"] = str(Path(__file__).with_name("test_data.json"))
spec.loader.exec_module(app)
app_requests.post = _original_post
app.DATA = str(Path(__file__).with_name("test_data.json"))
app.STATE = {"chats": {}, "mem": {}, "cfg": {}, "rems": [], "daily": {}, "todos": {}, "notes": [], "streaks": {}, "moods": {}, "usage": {}, "spends": [], "links": [], "persona": "default", "dates": [], "studyplan": {}, "wikis": [], "dreams": {}, "debate": {}, "ratelimit": {}, "last_action": [], "cmdcount": {}, "alert_day": "", "sumday": ""}
app.gh_backup_soon = lambda: None

@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    app.DATA = str(tmp_path / "data.json")
    monkeypatch.setenv("ONCE_LOCK_DIR", str(tmp_path / "once"))
    Path(os.environ["ONCE_LOCK_DIR"]).mkdir(parents=True, exist_ok=True)
    app.STATE = {"chats": {}, "mem": {}, "cfg": {}, "rems": [], "daily": {}, "todos": {}, "notes": [], "streaks": {}, "moods": {}, "usage": {}, "spends": [], "links": [], "persona": "default", "dates": [], "studyplan": {}, "wikis": [], "dreams": {}, "debate": {}, "ratelimit": {}, "last_action": [], "cmdcount": {}, "alert_day": "", "sumday": ""}
    app.gh_backup_soon = lambda: None
    app.SEEN.clear()
    yield

@pytest.mark.parametrize("delay,seconds", [("10m", 600), ("2h", 7200), ("3d", 259200)])
def test_parse_relative_time(delay, seconds, monkeypatch):
    monkeypatch.setattr(app.time, "time", lambda: 1000.0)
    assert app.parse_time_word(delay) == 1000.0 + seconds

@pytest.mark.parametrize("delay", ["daily", "0m", "-3h", "nanm", "24:00", "12:60", "nonsense"])
def test_parse_invalid_and_daily(delay):
    assert app.parse_time_word(delay) is None

def test_parse_hhmm_future_and_past(monkeypatch):
    now = datetime(2026, 10, 29, 10, 24, tzinfo=app.TZ)
    monkeypatch.setattr(app, "now_local", lambda: now)
    assert datetime.fromtimestamp(app.parse_time_word("11:30"), app.TZ) == now.replace(hour=11, minute=30)
    assert datetime.fromtimestamp(app.parse_time_word("10:00"), app.TZ) == (now + timedelta(days=1)).replace(hour=10, minute=0)

def test_once_dedupes_across_calls():
    assert app._once("unit-test-key") is True
    assert app._once("unit-test-key") is False

def test_daily_job_fires_once(monkeypatch):
    now = datetime(2026, 10, 29, 7, 0, tzinfo=app.TZ)
    monkeypatch.setattr(app, "now_local", lambda: now)
    monkeypatch.setattr(app.time, "time", lambda: now.timestamp())
    sent = []
    monkeypatch.setattr(app, "send_long", lambda *args, **kwargs: sent.append(args))
    monkeypatch.setattr(app, "save", lambda: None)
    monkeypatch.setattr(app, "extras_tick", lambda: None)
    monkeypatch.setattr(app, "v8_tick", lambda: None)
    monkeypatch.setattr(app, "human_tick", lambda: None)
    monkeypatch.setattr(app.time, "sleep", lambda _: (_ for _ in ()).throw(StopIteration()))
    app.STATE["daily"] = {"123": {"jobs": [{"hour": 7, "min": 0, "text": "daily ping"}]}}
    with pytest.raises(StopIteration): app.reminder_loop()
    assert sent == [(123, "daily ping")]
    # A second loop at the same minute must respect the persisted last stamp and lock.
    monkeypatch.setattr(app.time, "sleep", lambda _: (_ for _ in ()).throw(StopIteration()))
    with pytest.raises(StopIteration): app.reminder_loop()
    assert sent == [(123, "daily ping")]

def test_rem_list_delete_numbering():
    app.STATE["rems"] = [
        {"chat": "123", "at": 2000000000, "text": "first"},
        {"chat": "123", "at": 2000000100, "text": "second"},
    ]
    assert "1. first" in app.do_cmd("123", "/remind list")
    assert "2. second" in app.do_cmd("123", "/remind list")
    app.do_cmd("123", "/remind del 1")
    assert len(app.STATE["rems"]) == 1
    assert app.STATE["rems"][0]["text"] == "second"

def test_save_atomic_json_and_backup():
    app.STATE["probe"] = {"ok": True}
    app.save()
    assert json.loads(Path(app.DATA).read_text())["probe"] == {"ok": True}
    app.STATE["probe"] = {"ok": False}
    app.save()
    assert json.loads(Path(app.DATA + ".bak").read_text())["probe"] == {"ok": True}

def test_webhook_duplicate_update_enqueued_once(monkeypatch):
    called = []
    monkeypatch.setattr(app, "enqueue", lambda *args: called.append(args))
    client = app.app.test_client()
    payload = {"update_id": 998877, "message": {"from": {"id": 123}, "chat": {"type": "private"}, "text": "hello", "message_id": 55}}
    # API threads call handle_update; patch the target so test is deterministic.
    monkeypatch.setattr(app.threading, "Thread", lambda *a, **kw: types.SimpleNamespace(start=lambda: app.handle_update(*kw.get("args", ()))) if kw.get("target") == app.handle_update else type("T", (), {"start": lambda self: None})())
    assert client.post("/webhook", json=payload).status_code == 200
    assert client.post("/webhook", json=payload).status_code == 200
    assert len(called) <= 1

def test_tg_does_not_treat_http_error_as_success(monkeypatch):
    responses = iter([types.SimpleNamespace(status_code=500), types.SimpleNamespace(status_code=200)])
    monkeypatch.setattr(app.requests, "post", lambda *a, **kw: next(responses))
    assert app.tg("sendMessage", chat_id=123, text="x") is None

def test_journal_keyword_detection():
    app.STATE["journal"] = {}
    saved = []
    app.save = lambda: saved.append(True)
    assert app.journal_add(123, "aj diary te likhlam") is None
    assert "123" in app.STATE["journal"]
    assert saved
