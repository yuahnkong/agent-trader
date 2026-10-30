"""Screen arithmetic: sectors, gross profitability, Piotroski F-score, accounting red flags.

Agents never compute ratios; everything they see about a company's numbers comes from here.
"""

from dataclasses import dataclass

from .fundamentals import AnnualSnapshot

# Broad sectors from SIC codes, roughly GICS-shaped. SIC is what EDGAR gives us for free;
# it's coarse (it files Apple under computer hardware, Alphabet under services) but it's
# point-in-time and consistent, which matters more for a within-sector rank.
# Ranges are checked in order, so narrower ranges come before the ones that contain them.
SECTOR_RANGES: list[tuple[int, int, str]] = [
    # Exceptions first: SIC groups whose two-digit parent points at the wrong sector.
    (3630, 3639, "Consumer Discretionary"),  # household appliances (A.O. Smith, Whirlpool)
    (3651, 3652, "Consumer Discretionary"),  # consumer audio/video
    (4920, 4925, "Energy"),  # gas production, transmission (Cheniere files as 4924)
    (4950, 4959, "Industrials"),  # waste management, not a utility
    (5311, 5331, "Consumer Staples"),  # department and variety stores (Walmart, Costco)
    (5912, 5912, "Consumer Staples"),  # drug stores
    (2830, 2836, "Health Care"),
    (3841, 3851, "Health Care"),
    (8000, 8099, "Health Care"),
    (8731, 8731, "Health Care"),
    (1300, 1399, "Energy"),
    (2900, 2999, "Energy"),
    (4900, 4999, "Utilities"),
    (4800, 4899, "Communication"),
    (2700, 2799, "Communication"),
    (7810, 7819, "Communication"),
    (3570, 3579, "Technology"),
    (3600, 3699, "Technology"),
    (3820, 3829, "Technology"),
    (7370, 7379, "Technology"),
    (2000, 2199, "Consumer Staples"),
    (2840, 2844, "Consumer Staples"),
    (5140, 5149, "Consumer Staples"),
    (5400, 5499, "Consumer Staples"),
    (100, 999, "Consumer Staples"),  # agriculture
    (1000, 1499, "Materials"),
    (2600, 2699, "Materials"),
    (2800, 2899, "Materials"),
    (3300, 3399, "Materials"),
    (1500, 1799, "Industrials"),
    (3400, 3569, "Industrials"),
    (3580, 3599, "Industrials"),
    (3700, 3799, "Industrials"),
    (3800, 3899, "Industrials"),
    (4000, 4799, "Industrials"),
    (8700, 8799, "Industrials"),
    (7300, 7399, "Industrials"),  # business services other than software
    (2200, 2599, "Consumer Discretionary"),
    (3000, 3299, "Consumer Discretionary"),
    (3900, 3999, "Consumer Discretionary"),
    (5000, 5999, "Consumer Discretionary"),
    (7000, 7299, "Consumer Discretionary"),
    (7400, 7999, "Consumer Discretionary"),
    (8100, 8999, "Industrials"),
]


def sector_for_sic(sic: str | int | None) -> str | None:
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return None
    for lo, hi, name in SECTOR_RANGES:
        if lo <= code <= hi:
            return name
    return None


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def gross_profitability(snap: AnnualSnapshot) -> float | None:
    """(Revenue - COGS) / Total Assets for the latest fiscal year (Novy-Marx 2013)."""
    return _div(snap.current.get("gross_profit"), snap.current.get("total_assets"))


@dataclass
class FScore:
    score: int
    computable: int  # how many of the 9 tests had the data to run
    tests: dict[str, bool | None]


def piotroski(snap: AnnualSnapshot) -> FScore:
    """Piotroski (2000) F-score from two fiscal years. A test whose inputs are missing
    scores 0 and is reported as None, so missing data never inflates the score.

    Simplifications, documented so the agents don't over-read the number:
    ROA uses year-end rather than beginning-of-year assets, and a company with no
    long-term debt tag in either year is treated as debt-free.
    """
    c, p = snap.current, snap.prior
    t: dict[str, bool | None] = {}

    roa = _div(c.get("net_income"), c.get("total_assets"))
    roa_prior = _div(p.get("net_income"), p.get("total_assets"))
    t["roa_positive"] = None if roa is None else roa > 0
    t["cfo_positive"] = None if c.get("cfo") is None else c["cfo"] > 0
    t["roa_improved"] = None if roa is None or roa_prior is None else roa > roa_prior
    t["cfo_exceeds_net_income"] = (
        None if c.get("cfo") is None or c.get("net_income") is None else c["cfo"] > c["net_income"]
    )

    def leverage(y):
        if y.get("total_assets") in (None, 0):
            return None
        return (y.get("long_term_debt") or 0.0) / y["total_assets"]

    lev, lev_prior = leverage(c), leverage(p)
    t["leverage_not_up"] = None if lev is None or lev_prior is None else lev <= lev_prior

    cr = _div(c.get("current_assets"), c.get("current_liabilities"))
    cr_prior = _div(p.get("current_assets"), p.get("current_liabilities"))
    t["current_ratio_improved"] = None if cr is None or cr_prior is None else cr > cr_prior

    sh, sh_prior = c.get("shares_basic"), p.get("shares_basic")
    t["no_dilution"] = None if sh is None or sh_prior is None else sh <= sh_prior * 1.005

    gm = _div(c.get("gross_profit"), c.get("revenue"))
    gm_prior = _div(p.get("gross_profit"), p.get("revenue"))
    t["gross_margin_improved"] = None if gm is None or gm_prior is None else gm > gm_prior

    at = _div(c.get("revenue"), c.get("total_assets"))
    at_prior = _div(p.get("revenue"), p.get("total_assets"))
    t["asset_turnover_improved"] = None if at is None or at_prior is None else at > at_prior

    return FScore(
        score=sum(1 for v in t.values() if v),
        computable=sum(1 for v in t.values() if v is not None),
        tests=t,
    )


def accounting_flags(snap: AnnualSnapshot, recent_forms: list[dict]) -> list[str]:
    """Red flags for the Fundamentals Analyst. `recent_forms` is the last ~2 years of
    filings as {form, filed, items}."""
    flags = []
    c = snap.current
    ta = c.get("total_assets")
    if c.get("net_income") is not None and c.get("cfo") is not None and ta:
        accruals = (c["net_income"] - c["cfo"]) / ta
        if accruals > 0.10:
            flags.append(f"accruals {accruals:.0%} of assets: earnings well above operating cash flow")
    if any(f["form"] in ("10-K/A", "10-Q/A") for f in recent_forms):
        flags.append("amended 10-K/10-Q filed in the last two years (possible restatement)")
    for f in recent_forms:
        items = f.get("items") or ""
        if f["form"] == "8-K" and "4.02" in items:
            flags.append(f"8-K item 4.02 (prior financials no longer reliable) filed {f['filed']}")
        if f["form"] == "8-K" and "4.01" in items:
            flags.append(f"8-K item 4.01 (auditor change) filed {f['filed']}")
    if snap.current.get("revenue") is None and snap.current.get("gross_profit") is None:
        flags.append("no revenue tag found; gross profitability unreliable")
    return flags
