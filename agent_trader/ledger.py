"""Tax lots and realized gains, rebuilt from Alpaca FILL activities (first in, first out).

Alpaca's fill history is the source of truth, so nothing here needs to be stored between
runs. The wash-sale check and the Implicator's short- vs long-term view both read this.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from . import config


@dataclass
class Lot:
    qty: float
    price: float
    opened: date


@dataclass
class Realized:
    symbol: str
    closed: date
    qty: float
    proceeds: float
    cost: float
    opened: date

    @property
    def pnl(self) -> float:
        return self.proceeds - self.cost

    @property
    def long_term(self) -> bool:
        return (self.closed - self.opened).days > 365


@dataclass
class Ledger:
    lots: dict[str, list[Lot]] = field(default_factory=dict)
    realized: list[Realized] = field(default_factory=list)
    buys: dict[str, list[date]] = field(default_factory=dict)

    def last_loss_sale(self, symbol: str) -> date | None:
        losses = [r.closed for r in self.realized if r.symbol == symbol and r.pnl < 0]
        return max(losses) if losses else None

    def last_buy(self, symbol: str) -> date | None:
        return max(self.buys[symbol]) if self.buys.get(symbol) else None


def _fill_date(fill: dict) -> date:
    ts = datetime.fromisoformat(fill["transaction_time"].replace("Z", "+00:00"))
    return ts.astimezone(config.MARKET_TZ).date()


def build_ledger(fills: list[dict]) -> Ledger:
    led = Ledger()
    for f in sorted(fills, key=lambda f: f["transaction_time"]):
        sym, qty, price, day = config.canonical_symbol(f["symbol"]), float(f["qty"]), float(f["price"]), _fill_date(f)
        lots = led.lots.setdefault(sym, [])
        if f["side"] == "buy":
            lots.append(Lot(qty, price, day))
            led.buys.setdefault(sym, []).append(day)
            continue
        remaining = qty
        while remaining > 1e-9 and lots:
            lot = lots[0]
            take = min(lot.qty, remaining)
            led.realized.append(Realized(sym, day, take, take * price, take * lot.price, lot.opened))
            lot.qty -= take
            remaining -= take
            if lot.qty <= 1e-9:
                lots.pop(0)
    led.lots = {s: l for s, l in led.lots.items() if l}
    return led
