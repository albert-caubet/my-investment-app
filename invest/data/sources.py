"""From a catalog entry to a frame of observations: the one dispatcher.

``Fetcher.fetch(spec)`` knows every source in the catalog and returns
``DataFrame(obs_date, value)`` or raises :class:`SourceError`. Files that serve
several series (the Shiller workbook, the EBP CSV, the COT zips, the Yahoo
batch) are downloaded once per run and kept on the instance.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from invest.data import bis, ecb, eurostat, files, fred, oecd, yahoo
from invest.data.files import DownloadResult
from invest.macro.catalog import SeriesSpec
from invest.positioning import cot


class SourceError(RuntimeError):
    """A series could not be fetched. The message is what the freshness table prints."""


def _column(frame: pd.DataFrame, column: str, what: str) -> pd.DataFrame:
    if column not in frame.columns:
        raise SourceError(f"{what} has no column {column!r}; has {list(frame.columns)[:12]}")
    out = frame[["obs_date", column]].rename(columns={column: "value"})
    return out.dropna().reset_index(drop=True)


class Fetcher:
    def __init__(self, *, fred_api_key: str | None = None, cot_years: list[int] | None = None):
        self.fred_api_key = fred_api_key
        self.cot_years = cot_years
        self.downloads: list[DownloadResult] = []
        self.yahoo_frames: dict[str, pd.DataFrame] = {}
        self._cache: dict[str, pd.DataFrame] = {}
        self._errors: dict[str, str] = {}

    # -- shared downloads ---------------------------------------------------

    def _cached(self, name: str, loader):
        """Load a shared file once; a failure is remembered so it is reported once per series."""
        if name in self._cache:
            return self._cache[name]
        if name in self._errors:
            raise SourceError(self._errors[name])
        try:
            frame = loader()
        except Exception as exc:  # any source failure is a SourceError to the caller
            self._errors[name] = f"{name}: {exc}"
            raise SourceError(self._errors[name]) from exc
        self._cache[name] = frame
        return frame

    def _shiller(self) -> pd.DataFrame:
        def load():
            result = files.download(files.shiller_download_url(), "ie_data.xls")
            self.downloads.append(result)
            return files.read_shiller_xls(result.path)

        return self._cached("shiller", load)

    def _ebp(self) -> pd.DataFrame:
        return self._cached("ebp", lambda: files.parse_ebp_csv(files.fetch_text(files.EBP_URL)))

    def _philly(self) -> pd.DataFrame:
        return self._cached(
            "philly",
            lambda: files.parse_philly_csv(files.fetch_text(files.PHILLY_URL, headers=files.BROWSER_HEADERS)),
        )

    def _nyfed(self) -> pd.DataFrame:
        def load():
            result = files.download(files.NYFED_RECPROB_URL, "nyfed_allmonth.xls")
            self.downloads.append(result)
            return files.read_nyfed_recprob_xls(result.path)

        return self._cached("nyfed_recprob", load)

    def _hlw(self) -> pd.DataFrame:
        def load():
            result = files.download(files.HLW_URL, "hlw_estimates.xlsx")
            self.downloads.append(result)
            return files.read_hlw_xlsx(result.path)

        return self._cached("hlw", load)

    def _cot(self) -> pd.DataFrame:
        def load():
            years = self.cot_years
            if years is None:
                this_year = date.today().year
                years = list(range(this_year - 9, this_year + 1))
            return cot.fetch_years(years)

        return self._cached("cot", load)

    def prefetch_yahoo(self, specs: list[SeriesSpec]) -> None:
        """One batched download for every Yahoo symbol in the catalog."""
        symbols = sorted({s.key for s in specs if s.source == "yahoo" and s.key})
        if not symbols:
            return
        try:
            self.yahoo_frames.update(yahoo.fetch_history(symbols, period="max"))
        except Exception as exc:
            self._errors["yahoo"] = f"yahoo batch download failed: {exc}"

    # -- dispatch ------------------------------------------------------------

    def fetch(self, spec: SeriesSpec) -> pd.DataFrame:
        source = spec.source
        try:
            if source == "fred":
                return fred.fetch_series(spec.key, api_key=self.fred_api_key)
            if source == "ecb":
                return ecb.fetch_series(spec.key, start=spec.start or "1990-01")
            if source == "eurostat":
                return eurostat.fetch_series(spec.key, since=spec.start)
            if source == "oecd":
                return oecd.fetch_series(spec.key, start=spec.start or "1960-01")
            if source == "bis":
                return bis.fetch_series(spec.key, start=spec.start or "1960-01-01")
            if source == "yahoo":
                if spec.key not in self.yahoo_frames:
                    if "yahoo" in self._errors:
                        raise SourceError(self._errors["yahoo"])
                    got = yahoo.fetch_history([spec.key], period="max")
                    if spec.key not in got:
                        raise SourceError(f"yahoo returned no data for {spec.key!r}")
                    self.yahoo_frames.update(got)
                frame = self.yahoo_frames[spec.key]
                return pd.DataFrame({"obs_date": frame.index.date, "value": frame["close"].to_numpy()})
            if source == "shiller":
                return _column(self._shiller(), spec.key, "Shiller data")
            if source == "ebp":
                return _column(self._ebp(), spec.key, "EBP CSV")
            if source == "philly":
                return _column(self._philly(), spec.key, "Philly Fed CSV")
            if source == "nyfed_recprob":
                return _column(self._nyfed(), spec.key, "NY Fed recession probabilities")
            if source == "hlw":
                return _column(self._hlw(), spec.key, "HLW estimates")
            if source == "cot":
                return cot.net_speculative(self._cot(), spec.key)
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"{spec.id} ({source} {spec.key}): {exc}") from exc
        raise SourceError(f"{spec.id}: source {source!r} is not fetched by the dispatcher")
