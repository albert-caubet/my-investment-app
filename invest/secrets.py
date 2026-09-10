"""Secrets from the environment first, then ``.streamlit/secrets.toml``.

Read directly with ``tomllib`` rather than through ``st.secrets`` so the jobs do
not import Streamlit. Nothing here is ever written to the database or the
report; the freshness table names the *source*, never the key.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from invest.paths import REPO_ROOT


@lru_cache(maxsize=1)
def _file_secrets() -> dict:
    for candidate in (REPO_ROOT / ".streamlit" / "secrets.toml", Path.home() / ".streamlit" / "secrets.toml"):
        if candidate.exists():
            try:
                with candidate.open("rb") as handle:
                    return tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError):
                return {}
    return {}


def get_secret(name: str, default: str | None = None) -> str | None:
    """``name`` from the environment, else from secrets.toml (top level or ``[invest]``)."""
    value = os.environ.get(name)
    if value:
        return value
    secrets = _file_secrets()
    section = secrets.get("invest")
    if isinstance(section, dict) and section.get(name):
        return str(section[name])
    if secrets.get(name):
        return str(secrets[name])
    return default


#: Contact address the SEC requires in the User-Agent header. Without it EDGAR
#: refuses the request, so the EDGAR client raises rather than sending a fake one.
def sec_user_agent() -> str | None:
    return get_secret("SEC_USER_AGENT")


def fred_api_key() -> str | None:
    return get_secret("FRED_API_KEY")
