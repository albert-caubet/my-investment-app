"""Operations: backups, the scheduler arithmetic, notifications and credential sources."""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

import database
from invest.data.cache import Store
from invest.jobs import backup, notify

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import scheduler  # noqa: E402


def test_backup_copies_database_and_zips_reports_and_prunes(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("INVEST_DATA_DIR", str(data))
    monkeypatch.delenv("INVEST_DB", raising=False)
    monkeypatch.delenv("INVEST_REPORTS_DIR", raising=False)
    with Store(data / "market.duckdb") as store:
        store.upsert_series("X", {"2026-01-01": 1.0}, source="test")
    (data / "reports").mkdir()
    (data / "reports" / "latest.md").write_text("# report", encoding="utf-8")
    dest = tmp_path / "backups"
    first = backup.backup(dest=dest, keep=2, now=datetime(2026, 9, 10, 3, 0, 0))
    assert first["database"].exists() and first["reports"].exists()
    with Store(first["database"], read_only=True) as copy:
        assert copy.read_series("X").iloc[0] == 1.0
    backup.backup(dest=dest, keep=2, now=datetime(2026, 9, 11, 3, 0, 0))
    third = backup.backup(dest=dest, keep=2, now=datetime(2026, 9, 12, 3, 0, 0))
    assert len(list(dest.glob("market_*.duckdb"))) == 2
    assert len(third["removed"]) == 2  # one database copy and one zip pruned


def test_backup_with_nothing_to_back_up(tmp_path, monkeypatch):
    monkeypatch.setenv("INVEST_DATA_DIR", str(tmp_path / "empty"))
    monkeypatch.delenv("INVEST_DB", raising=False)
    monkeypatch.delenv("INVEST_REPORTS_DIR", raising=False)
    result = backup.backup(dest=tmp_path / "b")
    assert result["database"] is None and result["reports"] is None


def test_scheduler_next_run():
    sat_8 = scheduler.parse_schedule("sat 08:00")
    assert sat_8 == (5, 8, 0)
    # Thursday 2026-09-10 10:00 -> Saturday 2026-09-12 08:00
    assert scheduler.next_run(sat_8, datetime(2026, 9, 10, 10, 0)) == datetime(2026, 9, 12, 8, 0)
    # Saturday 09:00, already past -> next Saturday
    assert scheduler.next_run(sat_8, datetime(2026, 9, 12, 9, 0)) == datetime(2026, 9, 19, 8, 0)
    daily = scheduler.parse_schedule("daily 03:00")
    assert scheduler.next_run(daily, datetime(2026, 9, 10, 2, 0)) == datetime(2026, 9, 10, 3, 0)
    assert scheduler.next_run(daily, datetime(2026, 9, 10, 4, 0)) == datetime(2026, 9, 11, 3, 0)


def test_notify_without_channels_says_so(monkeypatch):
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_HOST", "REPORT_EMAIL_TO"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("invest.secrets._file_secrets", lambda: {})
    assert notify.notify("subject", "text") == ["no alert channel configured"]


def test_notify_reports_a_failed_channel(monkeypatch):
    monkeypatch.setattr("invest.secrets._file_secrets", lambda: {})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.delenv("SMTP_HOST", raising=False)

    def boom(text, cfg):
        raise RuntimeError("offline")

    monkeypatch.setattr(notify, "send_telegram_text", boom)
    statuses = notify.notify("s", "t")
    assert statuses == ["telegram alert FAILED: offline"]


def test_firebase_credentials_from_environment_json(monkeypatch):
    payload = {"type": "service_account", "project_id": "p", "private_key": "k", "client_email": "e@example.com"}
    monkeypatch.setenv(database.CREDENTIALS_JSON_ENV, json.dumps(payload))
    assert database.credential_source() == payload
    monkeypatch.delenv(database.CREDENTIALS_JSON_ENV)
    source = database.credential_source()
    assert isinstance(source, (str, dict))  # the file path, or a secrets section when one exists
