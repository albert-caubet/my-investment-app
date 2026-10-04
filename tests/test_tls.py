"""TLS verification: every outbound connection in invest/ goes through the OS verifier.

No network. The Eurostat failure these guard against was Python refusing a
certificate that Windows itself accepted, because Windows installs some trusted
roots only when its own verifier first needs them.
"""

import ast
import json
import ssl
from pathlib import Path

import pytest
import truststore

from invest.data import http
from invest.jobs import notify
from invest.report import deliver

ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_context_verifies_through_the_os_and_is_strict():
    ctx = http.tls_context()
    assert isinstance(ctx, truststore.SSLContext)
    # The fix for a certificate error is never to switch verification off.
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_context_is_built_once():
    assert http.tls_context() is http.tls_context()


def test_a_failed_request_never_prints_an_api_key():
    """FRED takes its key in the URL, and the URL starts every request error, which pages show."""
    error = http.FetchError(
        "https://api.stlouisfed.org/fred/series/search?search_text=cpi&api_key=0123abcd&file_type=json: HTTP Error 400"
    )
    assert "0123abcd" not in str(error)
    assert "search_text=cpi&api_key=***&file_type=json: HTTP Error 400" in str(error)


def test_fetch_bytes_passes_the_context(monkeypatch):
    seen = {}

    def fake_urlopen(request, *, timeout, context):
        seen["context"] = context
        return _Response(b"ok")

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    assert http.fetch_bytes("https://ec.europa.eu/eurostat/example") == b"ok"
    assert seen["context"] is http.tls_context()


def test_telegram_alert_uses_the_context(monkeypatch):
    seen = {}

    def fake_urlopen(request, *, timeout, context):
        seen["context"] = context
        return _Response(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    notify.send_telegram_text("weekly job: 1 problem", {"token": "t", "chat_id": "c"})
    assert seen["context"] is http.tls_context()


def test_telegram_report_uses_the_context(monkeypatch, tmp_path):
    seen = {}

    def fake_urlopen(request, *, timeout, context):
        seen["context"] = context
        return _Response(json.dumps({"ok": True}).encode())

    page = tmp_path / "report.html"
    page.write_text("<p>report</p>", encoding="utf-8")
    monkeypatch.setattr(deliver.urllib.request, "urlopen", fake_urlopen)
    deliver.send_telegram(page, "weekly", {"token": "t", "chat_id": "c"})
    assert seen["context"] is http.tls_context()


def test_email_starttls_uses_the_context(monkeypatch):
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def starttls(self, *, context):
            seen["context"] = context

        def login(self, user, password):
            pass

        def send_message(self, message):
            seen["sent"] = True

    monkeypatch.setattr(deliver.smtplib, "SMTP", FakeSMTP)
    cfg = {"host": "smtp.example.org", "port": 587, "user": "u", "password": "p",
           "from": "a@example.org", "to": "b@example.org"}
    deliver.send_email("weekly", "<p>report</p>", cfg)
    assert seen == {"context": http.tls_context(), "sent": True}


# --- a structural guard, so the next new call site cannot quietly regress --------------


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            yield name, node


@pytest.mark.parametrize("path", sorted((ROOT / "invest").rglob("*.py")), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_connection_in_invest_bypasses_the_os_verifier(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for name, node in _calls(tree):
        where = f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
        if name == "urlopen":
            assert any(k.arg == "context" for k in node.keywords), (
                f"{where}: urlopen without context=tls_context() verifies with Python's own root "
                f"list, which on Windows misses roots the OS installs on demand"
            )
        if name in ("create_default_context", "_create_unverified_context"):
            pytest.fail(f"{where}: builds its own TLS context; use invest.data.http.tls_context()")
