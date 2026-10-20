"""Point-in-time annual fundamentals from SEC XBRL company facts.

"Point-in-time" means: for an as-of date D, use only facts whose `filed` date is on or
before D. If a value was later restated, we use the restated number only once the
restating filing exists. This keeps backtests of the screen free of look-ahead bias.

Companies change XBRL tags over the years (SalesRevenueNet -> RevenueFromContract...),
so each concept lists tags in priority order and we take the first one that reports
a given fiscal year.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

ANNUAL_FORMS = ("10-K", "10-K/A", "10-KT")
# A fiscal year is 52/53 weeks; anything well outside that is a stub or transition period.
MIN_YEAR_DAYS, MAX_YEAR_DAYS = 340, 390


@dataclass(frozen=True)
class Concept:
    tags: tuple[str, ...]
    instant: bool  # balance-sheet item (point in time) vs. flow over the year
    unit: str = "USD"


CONCEPTS: dict[str, Concept] = {
    "revenue": Concept(
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ),
        instant=False,
    ),
    "cogs": Concept(
        (
            "CostOfGoodsAndServicesSold",
            "CostOfRevenue",
            "CostOfGoodsSold",
            "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
        ),
        instant=False,
    ),
    "gross_profit_reported": Concept(("GrossProfit",), instant=False),
    "net_income": Concept(("NetIncomeLoss", "ProfitLoss"), instant=False),
    "cfo": Concept(
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        instant=False,
    ),
    "total_assets": Concept(("Assets",), instant=True),
    "current_assets": Concept(("AssetsCurrent",), instant=True),
    "current_liabilities": Concept(("LiabilitiesCurrent",), instant=True),
    "long_term_debt": Concept(
        ("LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"),
        instant=True,
    ),
    "shares_basic": Concept(
        ("WeightedAverageNumberOfSharesOutstandingBasic",), instant=False, unit="shares"
    ),
}


@dataclass(frozen=True)
class Fact:
    value: float
    filed: date
    tag: str
    form: str


@dataclass
class AnnualSnapshot:
    cik: int
    name: str
    as_of: date
    fy_end: date
    prior_fy_end: date | None
    current: dict[str, float | None] = field(default_factory=dict)
    prior: dict[str, float | None] = field(default_factory=dict)
    # Latest filing date among the facts used for the current year, i.e. when this
    # year's numbers (including any restatement) became public.
    known_since: date | None = None
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def age_days(self) -> int:
        return (self.as_of - self.fy_end).days


def _is_annual(entry: dict, instant: bool) -> bool:
    if entry.get("form") not in ANNUAL_FORMS:
        return False
    if instant:
        return "start" not in entry
    if "start" not in entry:
        return False
    days = (date.fromisoformat(entry["end"]) - date.fromisoformat(entry["start"])).days
    return MIN_YEAR_DAYS <= days <= MAX_YEAR_DAYS


def annual_series(facts: dict, concept: Concept, as_of: date) -> dict[date, Fact]:
    """Fiscal-year-end -> the value known as of `as_of`, across the concept's tags."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out: dict[date, Fact] = {}
    for tag in concept.tags:
        entries = gaap.get(tag, {}).get("units", {}).get(concept.unit, [])
        by_end: dict[date, Fact] = {}
        for e in entries:
            filed = date.fromisoformat(e["filed"])
            if filed > as_of or not _is_annual(e, concept.instant):
                continue
            end = date.fromisoformat(e["end"])
            prev = by_end.get(end)
            # Latest filing wins: a later 10-K's comparative column reflects restatements.
            if prev is None or filed > prev.filed:
                by_end[end] = Fact(float(e["val"]), filed, tag, e["form"])
        for end, fact in by_end.items():
            out.setdefault(end, fact)  # earlier tags in the list take priority
    return out


def _match_year(series: dict[date, Fact], fy_end: date, tolerance: int = 10) -> Fact | None:
    """Find the value for a fiscal year end, allowing for 52/53-week calendars where
    balance-sheet and income-statement end dates can differ by a few days."""
    if fy_end in series:
        return series[fy_end]
    near = [d for d in series if abs((d - fy_end).days) <= tolerance]
    return series[min(near, key=lambda d: abs((d - fy_end).days))] if near else None


def annual_snapshot(facts: dict, as_of: date) -> AnnualSnapshot | None:
    """Latest and prior fiscal year as known on `as_of`, or None if no annual data yet."""
    series = {name: annual_series(facts, c, as_of) for name, c in CONCEPTS.items()}

    # Fiscal years are defined by when net income was reported for a full year.
    fy_ends = sorted(series["net_income"])
    if not fy_ends:
        return None
    fy_end = fy_ends[-1]
    prior_candidates = [
        d for d in fy_ends if timedelta(days=330) <= fy_end - d <= timedelta(days=400)
    ]
    prior_fy_end = prior_candidates[-1] if prior_candidates else None

    snap = AnnualSnapshot(
        cik=int(facts.get("cik", 0)),
        name=facts.get("entityName", ""),
        as_of=as_of,
        fy_end=fy_end,
        prior_fy_end=prior_fy_end,
    )
    filed_dates = []
    for name, s in series.items():
        cur = _match_year(s, fy_end)
        snap.current[name] = cur.value if cur else None
        if cur:
            filed_dates.append(cur.filed)
            snap.sources[name] = cur.tag
        prev = _match_year(s, prior_fy_end) if prior_fy_end else None
        snap.prior[name] = prev.value if prev else None
    snap.known_since = max(filed_dates) if filed_dates else None

    for year in (snap.current, snap.prior):
        year["gross_profit"] = _gross_profit(year)
    return snap


def _gross_profit(year: dict[str, float | None]) -> float | None:
    if year.get("revenue") is not None and year.get("cogs") is not None:
        return year["revenue"] - year["cogs"]
    return year.get("gross_profit_reported")
