"""SEC EDGAR client: ticker->CIK map, XBRL company facts, and filing history."""

from dataclasses import dataclass
from datetime import date

from . import config
from .http import JsonCache, RateLimiter, make_session

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{doc}"

DAY = 86400


def is_financial_sic(sic: str | int | None) -> bool:
    """SIC 6000-6799 is finance, insurance and real estate. Gross profitability is
    meaningless there (no COGS), so the design excludes the whole division."""
    try:
        return 6000 <= int(sic) <= 6799
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Filing:
    cik: int
    form: str
    filed: date
    accession: str
    report_date: str
    items: str
    url: str


class SecClient:
    def __init__(self, cache: JsonCache | None = None):
        self.session = make_session(
            {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
        )
        self.limiter = RateLimiter(config.SEC_MAX_REQUESTS_PER_SEC)
        self.cache = cache or JsonCache(config.CACHE_DIR)

    def _get(self, url: str, max_age: float) -> dict:
        cached = self.cache.get(url, None, max_age)
        if cached is not None:
            return cached
        self.limiter.wait()
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        self.cache.put(url, None, data)
        return data

    def ticker_map(self) -> dict[str, dict]:
        """Ticker -> {cik, name, exchange}. Tickers use SEC's form (BRK-B, not BRK.B)."""
        raw = self._get(TICKERS_URL, max_age=7 * DAY)
        idx = {f: i for i, f in enumerate(raw["fields"])}
        out = {}
        for row in raw["data"]:
            out[row[idx["ticker"]]] = {
                "cik": row[idx["cik"]],
                "name": row[idx["name"]],
                "exchange": row[idx["exchange"]],
            }
        return out

    def company_facts(self, cik: int, max_age: float = DAY) -> dict:
        return self._get(FACTS_URL.format(cik=cik), max_age)

    def submissions(self, cik: int, max_age: float = DAY) -> dict:
        return self._get(SUBMISSIONS_URL.format(cik=cik), max_age)

    def recent_filings(
        self,
        cik: int,
        since: date,
        forms: tuple[str, ...] = ("8-K", "10-K", "10-Q", "10-K/A", "10-Q/A"),
        max_age: float = 3600,
    ) -> list[Filing]:
        recent = self.submissions(cik, max_age=max_age)["filings"]["recent"]
        out = []
        for i, form in enumerate(recent["form"]):
            filed = date.fromisoformat(recent["filingDate"][i])
            if form not in forms or filed < since:
                continue
            accn = recent["accessionNumber"][i]
            out.append(
                Filing(
                    cik=cik,
                    form=form,
                    filed=filed,
                    accession=accn,
                    report_date=recent["reportDate"][i],
                    items=recent["items"][i],
                    url=ARCHIVE_URL.format(
                        cik=cik, accn=accn.replace("-", ""), doc=recent["primaryDocument"][i]
                    ),
                )
            )
        return sorted(out, key=lambda f: f.filed, reverse=True)
