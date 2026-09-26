"""Tiny HTTP helper shared by every source module.

``urllib`` only, like ``market_data.py``: every request goes through one function
that sets a User-Agent, a timeout, a bounded retry and the TLS context below.
Failures raise :class:`FetchError`; nothing here ever returns a default.
"""

from __future__ import annotations

import gzip
import json
import ssl
import time
import urllib.error
import urllib.request
from functools import lru_cache

import truststore

DEFAULT_USER_AGENT = "my-investment-app/1.0 (personal research tool)"
DEFAULT_TIMEOUT = 60


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    """Verify certificates the way the operating system does.

    Windows ships with part of its trusted root list and installs the rest on
    demand, the first time its own verifier needs one. Python's ``ssl`` only reads
    the store as it stands, so a source whose root nothing on the machine had
    needed yet failed from here while working in any browser. Every Eurostat
    series did, until something installed GlobalSign Root R46.

    truststore verifies through the OS itself (CryptoAPI on Windows,
    Security.framework on macOS, the system store on Linux), so the on-demand
    install happens and Python agrees with the browser. Verification is not
    relaxed in any way: certificates are required and hostnames checked. The
    answer to a certificate error is never to turn verification off.
    """
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class FetchError(RuntimeError):
    """A request failed after its retries. The message names the URL and cause."""


def fetch_bytes(
    url: str,
    *,
    headers: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 2,
    backoff: float = 1.5,
) -> bytes:
    merged = {"User-Agent": DEFAULT_USER_AGENT, "Accept-Encoding": "gzip"}
    merged.update(headers or {})
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(request, timeout=timeout, context=tls_context()) as response:
                data = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            last = exc
            # 4xx will not fix itself by retrying; 429 and 5xx might.
            if exc.code < 500 and exc.code != 429:
                break
        except Exception as exc:  # URLError, timeout, incomplete read
            last = exc
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    raise FetchError(f"{url}: {last}") from last


def fetch_text(url: str, *, encoding: str = "utf-8", **kwargs) -> str:
    return fetch_bytes(url, **kwargs).decode(encoding, "replace")


def fetch_json(url: str, **kwargs):
    text = fetch_text(url, **kwargs)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url}: response is not JSON ({exc})") from exc
