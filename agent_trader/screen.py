"""Candidate menu: liquid non-financial US stocks, top-quintile gross profitability within
sector, Piotroski F-score >= 7, and the calmer half of 252-day volatility.

The full run touches ~1,500 companies on EDGAR, so it's done weekly and the result is
committed to state/menu.json. Daily runs read that file.
"""

import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from . import config, prices
from .alpaca import AlpacaClient
from .fundamentals import CONCEPTS, annual_snapshot
from .http import JsonCache
from .scoring import accounting_flags, gross_profitability, piotroski, sector_for_sic
from .sec import DAY, FACTS_URL, SUBMISSIONS_URL, SecClient, is_financial_sic

EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}
MENU_PATH = config.STATE_DIR / "menu.json"
_TAGS = {t for c in CONCEPTS.values() for t in c.tags}


def sec_ticker(symbol: str) -> str:
    """Alpaca writes share classes as BRK.B, SEC as BRK-B."""
    return symbol.replace(".", "-")


class SlimSec:
    """Company facts and submissions trimmed to what the screen reads. Full company
    facts run ~5 MB per company; the trimmed copy is a few KB."""

    def __init__(self, sec: SecClient):
        self.sec = sec
        self.cache = JsonCache(config.CACHE_DIR / "slim")

    def _fetch(self, url: str) -> dict:
        self.sec.limiter.wait()
        resp = self.sec.session.get(url, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def facts(self, cik: int) -> dict | None:
        url = FACTS_URL.format(cik=cik)
        hit = self.cache.get(url, None, 7 * DAY)
        if hit is not None:
            return hit or None
        try:
            raw = self._fetch(url)
        except Exception:
            self.cache.put(url, None, {})
            return None
        gaap = raw.get("facts", {}).get("us-gaap", {})
        slim = {
            "cik": raw.get("cik"),
            "entityName": raw.get("entityName"),
            "facts": {"us-gaap": {t: gaap[t] for t in _TAGS if t in gaap}},
        }
        self.cache.put(url, None, slim)
        return slim

    def profile(self, cik: int) -> dict | None:
        """SIC code plus the last two years of filings (form, date, 8-K items)."""
        url = SUBMISSIONS_URL.format(cik=cik)
        hit = self.cache.get(url, None, 2 * DAY)
        if hit is not None:
            return hit or None
        try:
            raw = self._fetch(url)
        except Exception:
            self.cache.put(url, None, {})
            return None
        r = raw.get("filings", {}).get("recent", {})
        cutoff = (date.today() - timedelta(days=730)).isoformat()
        forms = [
            {"form": f, "filed": d, "items": i}
            for f, d, i in zip(r.get("form", []), r.get("filingDate", []), r.get("items", []))
            if d >= cutoff and f in ("8-K", "10-K", "10-Q", "10-K/A", "10-Q/A")
        ]
        slim = {"sic": raw.get("sic"), "sic_description": raw.get("sicDescription"), "forms": forms}
        self.cache.put(url, None, slim)
        return slim


def liquid_universe(alpaca: AlpacaClient, tickers: dict, as_of: date) -> pd.DataFrame:
    assets = [
        a for a in alpaca.assets()
        if a["exchange"] in EXCHANGES and a.get("fractionable") and sec_ticker(a["symbol"]) in tickers
    ]
    symbols = [a["symbol"] for a in assets]
    print(f"  {len(symbols)} fractionable NYSE/NASDAQ/AMEX symbols with an SEC CIK")
    # Completed sessions only. This also keeps the free SIP plan happy: it refuses any
    # explicit end inside the last 15 minutes, and "today" counts.
    bars = alpaca.daily_bars(symbols, start=as_of - timedelta(days=400), end=as_of - timedelta(days=1))
    rows = []
    for sym, g in bars.groupby("symbol"):
        g = g.sort_values("date")
        last60 = g.tail(60)
        dollar_vol = float((last60["close"] * last60["volume"]).median())
        close = float(g["close"].iloc[-1])
        vol = prices.annualized_vol(g.set_index("date")["close"])
        if close < config.MIN_PRICE or dollar_vol < config.MIN_DOLLAR_VOLUME or vol is None:
            continue
        info = tickers[sec_ticker(sym)]
        rows.append(
            {"symbol": sym, "cik": info["cik"], "name": info["name"], "close": close,
             "dollar_volume": dollar_vol, "vol_252d": vol}
        )
    df = pd.DataFrame(rows)
    # One row per company: keep the most traded share class (GOOGL over GOOG).
    df = df.sort_values("dollar_volume", ascending=False).drop_duplicates("cik")
    print(f"  {len(df)} pass price >= ${config.MIN_PRICE:.0f}, "
          f"${config.MIN_DOLLAR_VOLUME / 1e6:.0f}M median dollar volume, 252 days of history")
    return df.reset_index(drop=True)


def run_screen(as_of: date | None = None, workers: int = 6) -> dict:
    as_of = as_of or config.market_today()
    alpaca, sec = AlpacaClient(), SecClient()
    slim = SlimSec(sec)
    print(f"screen as of {as_of}")
    uni = liquid_universe(alpaca, sec.ticker_map(), as_of)

    with ThreadPoolExecutor(workers) as pool:
        profiles = list(pool.map(slim.profile, uni["cik"]))
    uni["sic"] = [p.get("sic") if p else None for p in profiles]
    uni["sector"] = [sector_for_sic(s) for s in uni["sic"]]
    keep = [not is_financial_sic(sic) and sector is not None for sic, sector in zip(uni["sic"], uni["sector"])]
    profiles = [p for p, k in zip(profiles, keep) if k]
    uni = uni[keep].reset_index(drop=True)
    print(f"  {len(uni)} after dropping financials and unmapped SIC codes")

    with ThreadPoolExecutor(workers) as pool:
        all_facts = list(pool.map(slim.facts, uni["cik"]))

    rows = []
    for (_, u), facts, prof in zip(uni.iterrows(), all_facts, profiles):
        snap = annual_snapshot(facts, as_of) if facts else None
        if snap is None or snap.age_days > config.MAX_FUNDAMENTALS_AGE_DAYS:
            continue
        gp = gross_profitability(snap)
        if gp is None:
            continue
        f = piotroski(snap)
        rows.append({
            **u.to_dict(),
            "sic_description": prof.get("sic_description"),
            "fy_end": snap.fy_end.isoformat(),
            "known_since": snap.known_since.isoformat() if snap.known_since else None,
            "gross_profitability": gp,
            "fscore": f.score,
            "fscore_computable": f.computable,
            "fscore_tests": f.tests,
            "revenue": snap.current.get("revenue"),
            "net_income": snap.current.get("net_income"),
            "cfo": snap.current.get("cfo"),
            "total_assets": snap.current.get("total_assets"),
            "flags": accounting_flags(snap, prof.get("forms", [])),
        })
    elig = pd.DataFrame(rows)
    print(f"  {len(elig)} eligible with current annual fundamentals")

    elig["gp_sector_pct"] = elig.groupby("sector")["gross_profitability"].rank(pct=True)
    vol_median = float(elig["vol_252d"].median())
    menu = elig[
        (elig["gp_sector_pct"] > 1 - config.GP_TOP_PCT)
        & (elig["fscore"] >= config.MIN_FSCORE)
        & (elig["vol_252d"] <= vol_median)
    ].sort_values(["gp_sector_pct", "fscore"], ascending=False)
    print(f"  menu: {len(menu)} names (vol cutoff {vol_median:.1%})")

    result = {
        "as_of": as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rules": {
            "min_price": config.MIN_PRICE,
            "min_dollar_volume": config.MIN_DOLLAR_VOLUME,
            "gp_top_pct_within_sector": config.GP_TOP_PCT,
            "min_fscore": config.MIN_FSCORE,
            "vol_cutoff": vol_median,
        },
        "universe_size": int(len(elig)),
        "sector_counts": {k: int(v) for k, v in elig["sector"].value_counts().items()},
        "menu": [
            {k: (round(v, 6) if isinstance(v, float) else v) for k, v in row.items()}
            for row in menu.to_dict("records")
        ],
    }
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    MENU_PATH.write_text(json.dumps(result, indent=1, default=str))
    return result


def load_menu() -> dict | None:
    return json.loads(MENU_PATH.read_text()) if MENU_PATH.exists() else None


def menu_is_stale(menu: dict | None, today: date) -> bool:
    if menu is None:
        return True
    return (today - date.fromisoformat(menu["as_of"])).days >= config.MENU_MAX_AGE_DAYS


def median_vol(menu: dict) -> float:
    return statistics.median(m["vol_252d"] for m in menu["menu"]) if menu["menu"] else 0.2
