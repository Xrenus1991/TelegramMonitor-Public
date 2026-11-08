"""Offline regression tests; never connect to Telegram, OpenAI or Sherlock sites."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import os
import importlib.util
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, create_engine
from telegram_monitor.cloud import api
from telegram_monitor.cloud.db import md
from telegram_monitor.cloud.comments import (CommentStore, comments,
    normalise_comment, channel_ids_from_env, masked)
from telegram_monitor.cloud.threat_review import possible_direct_threat, telegram_user_id
from telegram_monitor.cloud.sherlock_proxy import NewJob, _same_origin_json

NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize("phrase,expected", [
    ("Я тебя убью", True),
    ("Убью завтра", True),
    ("Мы убьем", True),
    ("I will kill you", True),
    ("я тeбя убью", True),
    ("Он сказал, что убьёт", False),
    ("Я не убью", False),
    ("Никого не убью", False),
    ("В Ереване обычная погода", False),
    ("Сообщили о взрыве", False),
])
def test_manual_review_is_conservative(phrase,expected):
    assert possible_direct_threat(phrase) is expected


def message(text, uid=None):
    return SimpleNamespace(
        message=text, id=41, date=NOW,
        peer_id=SimpleNamespace(channel_id=777),
        from_id=SimpleNamespace(user_id=uid) if uid else SimpleNamespace(channel_id=123),
        sender_id=uid or 123)


def test_sender_is_explicitly_user_only():
    assert telegram_user_id(message("Я убью", 990012)) == 990012
    assert telegram_user_id(message("Я убью")) is None
    assert normalise_comment(message("Обсуждаем погоду", 5), 1, 2)["author_user_id"] is None
    assert normalise_comment(message("Я тебя убью", 5), 1, 2)["author_user_id"] == 5
    assert normalise_comment(message("Я тебя убью"), 1, 2)["author_user_id"] is None


def test_strict_comment_allowlist_and_masking():
    assert channel_ids_from_env("1,2,2") == {1,2}
    for bad in ("-1001234", "all", "1,xyz", "0", ",".join(["1"]*101)):
        with pytest.raises(ValueError):
            channel_ids_from_env(bad)
    masked_text=masked("name@example.com +374 99 123 456 @hello_world")
    assert "name@example.com" not in masked_text
    assert "@hello_world" not in masked_text


def test_comment_store_retains_history_and_enforces_readonly(tmp_path):
    url="sqlite:///"+str(tmp_path/"comments.db")
    engine=create_engine(url)
    md.create_all(engine)
    writer=CommentStore(url,initialize=True)
    benign=normalise_comment(message("Обычный комментарий",55), 1, 5)
    flagged=normalise_comment(message("Я тебя убью",66),1,5)
    flagged["comment_id"]=42;flagged["key"]="1:5:777:42"
    assert writer.save_thread(1,5,[benign,flagged],capped=False)==2
    assert writer.save_thread(1,5,[benign,flagged],capped=False)==0
    viewer=CommentStore(url,read_only=True)
    assert viewer.overview(hours=24)["total_comments"]==2
    thread=viewer.thread(1,5)
    assert thread["count"]==2
    assert thread["items"][0]["telegram_user_id"]==66
    alerts=viewer.alerts_between(NOW-timedelta(hours=1),NOW+timedelta(hours=1))
    assert alerts["count"]==1
    assert alerts["items"][0]["telegram_user_id"]==66
    with pytest.raises(RuntimeError):
        viewer.save_thread(1,5,[],capped=False)
    viewer.dispose();writer.dispose();engine.dispose()


def test_comment_api_auth_and_disabled_defaults(tmp_path,monkeypatch):
    url="sqlite:///"+str(tmp_path/"api.db")
    monkeypatch.setenv("DATABASE_URL",url)
    monkeypatch.setenv("DASHBOARD_USER","admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD","long-test-password-123456789")
    monkeypatch.setenv("CLOUD_COMMENTS_ENABLED","false")
    monkeypatch.setenv("SHERLOCK_OSINT_ENABLED","false")
    api.comment_reader.cache_clear();api.store.cache_clear()
    client=TestClient(api.app)
    assert client.get("/api/comments/overview").status_code==401
    assert client.get("/api/comments/alerts").status_code==401
    assert client.get("/api/osint/sherlock/recent").status_code==401
    auth=("admin","long-test-password-123456789")
    assert client.get("/api/comments/overview",auth=auth).json()["enabled"] is False
    assert client.get("/api/comments/overview?hours=25",auth=auth).status_code==422
    assert client.get("/api/comments/thread?channel_id=-1&post_id=3",auth=auth).status_code==422
    assert client.get("/api/osint/sherlock/recent",auth=auth).status_code==503
    assert "Комментарии под новостями" in client.get("/",auth=auth).text
    monkeypatch.setenv("CLOUD_COMMENTS_ENABLED","true")
    assert client.get("/api/comments/overview",auth=auth).json()["initialized"] is False
    writer=CommentStore(url,initialize=True)
    assert client.get("/api/comments/overview",auth=auth).json()["initialized"] is True
    writer.dispose()
    api.comment_reader.cache_clear();api.store.cache_clear()


def test_sherlock_manual_origin_and_input_validation():
    assert NewJob(username="@public_user").username=="public_user"
    for username in (";rm -rf /", "аbc", "-bad", "abc/def"):
        with pytest.raises(ValueError):
            NewJob(username=username)
    class BadReq:
        headers={"host":"127.0.0.1:18080","origin":"https://outside.example",
                 "x-tm-action":"1","content-type":"application/json"}
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _same_origin_json(BadReq())
    class GoodReq:
        headers={"host":"127.0.0.1:18080","origin":"http://127.0.0.1:18080",
                 "x-tm-action":"1","content-type":"application/json"}
    assert _same_origin_json(GoodReq()) is None


def test_sherlock_bridge_isolated_local_database(tmp_path):
    spec=importlib.util.spec_from_file_location("bridge_test_v861",
        Path(__file__).resolve().parents[1]/"deploy/sherlock/bridge.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch.object(module,"DB",tmp_path/"jobs.db"):
        module.init_db()
        with module.conn() as conn:
            conn.execute("INSERT INTO sherlock_jobs(id,username,state,created_at) VALUES(?,?,?,?)",
                         ("a"*32,"sample","running",module.now()))
        module.init_db()
        with module.conn() as conn:
            assert conn.execute("SELECT state FROM sherlock_jobs").fetchone()["state"]=="failed"

def test_sherlock_empty_history_is_visible_in_dashboard():
    """Keep the tested empty-history feedback in the shipped HTML."""
    html = (Path(__file__).resolve().parents[1] / "telegram_monitor/cloud/dashboard_v8_3.html").read_text(encoding="utf-8")
    assert "История проверок пуста." in html
    assert "if(!items.length)root.append(text('p','Проверок пока нет.','muted'));" in html
    assert "for(const item of items)" in html
