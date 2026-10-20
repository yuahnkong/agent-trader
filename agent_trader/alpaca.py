"""Thin Alpaca REST client (paper trading + market data). Read-only for now; order
placement will live behind the risk layer, not here."""

from datetime import date, datetime

import pandas as pd

from . import config
from .http import make_session


class AlpacaClient:
    def __init__(self, key: str | None = None, secret: str | None = None):
        if key is None or secret is None:
            key, secret = config.alpaca_credentials()
        self.session = make_session({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def _get(self, base: str, path: str, params: dict | None = None) -> dict | list:
        resp = self.session.get(base + path, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # Trading API -----------------------------------------------------------------

    def account(self) -> dict:
        return self._get(config.ALPACA_TRADING_URL, "/v2/account")

    def account_configurations(self) -> dict:
        return self._get(config.ALPACA_TRADING_URL, "/v2/account/configurations")

    def clock(self) -> dict:
        return self._get(config.ALPACA_TRADING_URL, "/v2/clock")

    def positions(self) -> list[dict]:
        return self._get(config.ALPACA_TRADING_URL, "/v2/positions")

    def assets(self) -> list[dict]:
        """Active, tradable US equities."""
        rows = self._get(
            config.ALPACA_TRADING_URL,
            "/v2/assets",
            {"status": "active", "asset_class": "us_equity"},
        )
        return [a for a in rows if a.get("tradable")]

    # Market data -----------------------------------------------------------------

    def daily_bars(self, symbols: list[str], start: date, end: date | None = None) -> pd.DataFrame:
        """Split- and dividend-adjusted daily bars, long format: symbol, date, o/h/l/c/v."""
        rows = []
        # The bars endpoint accepts many symbols per call; chunk to keep URLs sane.
        for i in range(0, len(symbols), 100):
            chunk = symbols[i : i + 100]
            params = {
                "symbols": ",".join(chunk),
                "timeframe": "1Day",
                "start": start.isoformat(),
                "adjustment": "all",
                "feed": config.ALPACA_DATA_FEED,
                "limit": 10000,
            }
            if end:
                params["end"] = end.isoformat()
            while True:
                data = self._get(config.ALPACA_DATA_URL, "/v2/stocks/bars", params)
                for sym, bars in (data.get("bars") or {}).items():
                    for b in bars:
                        rows.append(
                            {
                                "symbol": sym,
                                "date": datetime.fromisoformat(b["t"].replace("Z", "+00:00")).date(),
                                "open": b["o"],
                                "high": b["h"],
                                "low": b["l"],
                                "close": b["c"],
                                "volume": b["v"],
                            }
                        )
                token = data.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
        cols = ["symbol", "date", "open", "high", "low", "close", "volume"]
        return pd.DataFrame(rows, columns=cols).sort_values(["symbol", "date"], ignore_index=True)

    def news(self, symbols: list[str], start: datetime, limit: int = 200) -> list[dict]:
        """Headlines (Benzinga via Alpaca) for the given symbols since `start`, newest first."""
        params = {
            "symbols": ",".join(symbols),
            "start": start.isoformat(),
            "limit": 50,
            "sort": "desc",
        }
        out: list[dict] = []
        while len(out) < limit:
            data = self._get(config.ALPACA_DATA_URL, "/v1beta1/news", params)
            for n in data.get("news", []):
                out.append(
                    {
                        "id": n["id"],
                        "created_at": n["created_at"],
                        "headline": n["headline"],
                        "summary": n.get("summary", ""),
                        "symbols": n.get("symbols", []),
                        "source": n.get("source", ""),
                        "url": n.get("url", ""),
                    }
                )
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return out[:limit]
