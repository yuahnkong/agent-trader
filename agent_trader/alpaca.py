"""Thin Alpaca REST client (paper trading + market data). Order placement is exposed
here but only `execute` in the pipeline calls it, after the risk layer has passed a trade."""

from datetime import date, datetime

import pandas as pd
import requests

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

    def _send(self, method: str, path: str, body: dict | None = None) -> dict | list | None:
        # Writes are not retried by the session (allowed_methods is GET only), so a
        # timeout can't double-submit; client_order_id covers the rest.
        resp = self.session.request(method, config.ALPACA_TRADING_URL + path, json=body, timeout=30)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    # Trading API -----------------------------------------------------------------

    def account(self) -> dict:
        return self._get(config.ALPACA_TRADING_URL, "/v2/account")

    def account_configurations(self) -> dict:
        return self._get(config.ALPACA_TRADING_URL, "/v2/account/configurations")

    def update_account_configurations(self, **changes) -> dict:
        return self._send("PATCH", "/v2/account/configurations", changes)

    def clock(self) -> dict:
        return self._get(config.ALPACA_TRADING_URL, "/v2/clock")

    def positions(self) -> list[dict]:
        return self._get(config.ALPACA_TRADING_URL, "/v2/positions")

    def orders(self, status: str = "all", after: datetime | None = None, limit: int = 500) -> list[dict]:
        params = {"status": status, "limit": limit, "direction": "desc", "nested": "false"}
        if after:
            params["after"] = after.isoformat()
        return self._get(config.ALPACA_TRADING_URL, "/v2/orders", params)

    def order_by_client_id(self, client_order_id: str) -> dict | None:
        try:
            return self._get(
                config.ALPACA_TRADING_URL,
                "/v2/orders:by_client_order_id",
                {"client_order_id": client_order_id},
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def submit_order(
        self,
        symbol: str,
        side: str,
        client_order_id: str,
        notional: float | None = None,
        qty: str | None = None,
    ) -> dict:
        """Market DAY order, by dollar amount (buys) or share quantity (sells). Submitting
        the same client_order_id twice returns the existing order instead of a duplicate."""
        existing = self.order_by_client_id(client_order_id)
        if existing is not None:
            return existing
        body = {
            "symbol": symbol,
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "client_order_id": client_order_id,
        }
        if "/" in symbol:
            body["time_in_force"] = "gtc"  # crypto doesn't take "day"
        if notional is not None:
            body["notional"] = f"{notional:.2f}"
        else:
            body["qty"] = qty
        return self._send("POST", "/v2/orders", body)

    def cancel_all_orders(self) -> None:
        self._send("DELETE", "/v2/orders")

    def fills(self, after: date | None = None) -> list[dict]:
        """All FILL activities, oldest first."""
        params = {"direction": "asc", "page_size": 100}
        if after:
            params["after"] = after.isoformat()
        out: list[dict] = []
        while True:
            page = self._get(config.ALPACA_TRADING_URL, "/v2/account/activities/FILL", params)
            out.extend(page)
            if len(page) < 100:
                return out
            params["page_token"] = page[-1]["id"]

    def portfolio_history(self, period: str = "1A") -> dict:
        return self._get(
            config.ALPACA_TRADING_URL,
            "/v2/account/portfolio/history",
            {"period": period, "timeframe": "1D"},
        )

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

    def crypto_bars(self, symbols: list[str], start: date, end: date | None = None) -> pd.DataFrame:
        """Daily crypto bars (UTC days), same long format as daily_bars."""
        rows = []
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start.isoformat(), "limit": 10000}
        if end:
            params["end"] = end.isoformat()
        while True:
            data = self._get(config.ALPACA_DATA_URL, "/v1beta3/crypto/us/bars", params)
            for sym, bars in (data.get("bars") or {}).items():
                for b in bars:
                    rows.append({"symbol": sym,
                                 "date": datetime.fromisoformat(b["t"].replace("Z", "+00:00")).date(),
                                 "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"], "volume": b["v"]})
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
