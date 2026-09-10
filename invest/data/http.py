"""Tiny HTTP helper shared by every source module.

``urllib`` only, like ``market_data.py``: one fewer dependency, and every request
goes through one function that sets a User-Agent, a timeout and a bounded retry.
Failures raise :class:`FetchError`; nothing here ever returns a default.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request

DEFAULT_USER_AGENT = "my-investment-app/1.0 (personal research tool)"
DEFAULT_TIMEOUT = 60


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
            with urllib.request.urlopen(request, timeout=timeout) as response:
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
