from datetime import date

from agent_trader.fundamentals import annual_snapshot


def flow(start, end, val, filed, form="10-K"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def inst(end, val, filed, form="10-K"):
    return {"end": end, "val": val, "filed": filed, "form": form}


def facts(**tags):
    return {
        "cik": 1,
        "entityName": "Test Co",
        "facts": {"us-gaap": {t: {"units": {"USD": rows}} for t, rows in tags.items()}},
    }


FY23 = ("2023-01-01", "2023-12-31")
FY24 = ("2024-01-01", "2024-12-31")


def base():
    return facts(
        NetIncomeLoss=[
            flow(*FY23, 10, "2024-02-15"),
            flow(*FY23, 10, "2025-02-15"),  # comparative column in next 10-K
            flow(*FY24, 12, "2025-02-15"),
            flow("2024-01-01", "2024-03-31", 3, "2024-05-01", form="10-Q"),
        ],
        SalesRevenueNet=[flow(*FY23, 100, "2024-02-15")],
        RevenueFromContractWithCustomerExcludingAssessedTax=[flow(*FY24, 120, "2025-02-15")],
        CostOfRevenue=[flow(*FY23, 60, "2024-02-15"), flow(*FY24, 70, "2025-02-15")],
        Assets=[
            inst("2023-12-31", 500, "2024-02-15"),
            inst("2023-12-31", 480, "2025-02-15"),  # restated in the FY24 10-K
            inst("2024-12-31", 550, "2025-02-15"),
        ],
    )


def test_uses_only_filings_known_on_as_of_date():
    snap = annual_snapshot(base(), date(2025, 1, 31))
    assert snap.fy_end == date(2023, 12, 31)
    assert snap.prior_fy_end is None
    assert snap.current["total_assets"] == 500  # restatement not public yet


def test_restatement_applies_once_filed():
    snap = annual_snapshot(base(), date(2025, 3, 1))
    assert snap.fy_end == date(2024, 12, 31)
    assert snap.prior_fy_end == date(2023, 12, 31)
    assert snap.prior["total_assets"] == 480
    assert snap.known_since == date(2025, 2, 15)


def test_tag_fallback_across_years_and_gross_profit():
    snap = annual_snapshot(base(), date(2025, 3, 1))
    assert snap.current["revenue"] == 120
    assert snap.prior["revenue"] == 100
    assert snap.current["gross_profit"] == 50
    assert snap.prior["gross_profit"] == 40


def test_quarterly_facts_ignored():
    snap = annual_snapshot(base(), date(2024, 6, 1))
    assert snap.fy_end == date(2023, 12, 31)
    assert snap.current["net_income"] == 10


def test_no_annual_data_returns_none():
    f = facts(NetIncomeLoss=[flow("2024-01-01", "2024-03-31", 3, "2024-05-01", form="10-Q")])
    assert annual_snapshot(f, date(2025, 1, 1)) is None


def test_52_53_week_year_end_mismatch_tolerated():
    f = facts(
        NetIncomeLoss=[flow("2023-10-01", "2024-09-28", 5, "2024-11-01")],
        Assets=[inst("2024-09-30", 99, "2024-11-01")],
    )
    snap = annual_snapshot(f, date(2025, 1, 1))
    assert snap.current["total_assets"] == 99


def test_reported_gross_profit_used_when_cogs_missing():
    f = facts(
        NetIncomeLoss=[flow(*FY24, 1, "2025-02-15")],
        Revenues=[flow(*FY24, 10, "2025-02-15")],
        GrossProfit=[flow(*FY24, 4, "2025-02-15")],
    )
    snap = annual_snapshot(f, date(2025, 3, 1))
    assert snap.current["gross_profit"] == 4
