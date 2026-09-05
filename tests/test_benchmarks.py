from datetime import date

import pandas as pd

from core.database.duckdb_connection import create_memory_connection, load_dataframe
from core.models.workflow_state import AnalysisFilters
from core.sense.benchmarks import ota_scope_benchmark, prior_period_dates


def test_prior_period_is_same_inclusive_length():
    filters = AnalysisFilters(start_date=date(2026, 7, 8), end_date=date(2026, 7, 14))
    assert prior_period_dates(filters) == (date(2026, 7, 1), date(2026, 7, 7))


def test_ota_benchmark_uses_matching_period_and_ranks_peers():
    rows = []
    # Prior period: Vendor A 80% OTA.
    for index in range(10):
        rows.append(
            {
                "trip_date": pd.Timestamp("2026-07-01"),
                "business_unit": "BU",
                "office": "HQ",
                "vendor_id": "A",
                "is_ota_eligible": True,
                "is_on_time": index < 8,
                "alert_count": 0,
                "billed_cost": 0,
            }
        )
    # Current period: A=50%, B=90%, C=70%; other-vendor median=80 and A ranks third.
    for vendor, on_time_count in (("A", 5), ("B", 9), ("C", 7)):
        for index in range(10):
            rows.append(
                {
                    "trip_date": pd.Timestamp("2026-07-08"),
                    "business_unit": "BU",
                    "office": "HQ",
                    "vendor_id": vendor,
                    "is_ota_eligible": True,
                    "is_on_time": index < on_time_count,
                    "alert_count": 0,
                    "billed_cost": 0,
                }
            )
    con = create_memory_connection()
    load_dataframe(con, "mobility_trip_360", pd.DataFrame(rows))
    filters = AnalysisFilters(
        business_unit="BU",
        office="HQ",
        start_date=date(2026, 7, 8),
        end_date=date(2026, 7, 14),
    )
    result = ota_scope_benchmark(
        con, filters, column="vendor_id", value="A", min_trips=10
    )
    assert result["prior_period_ota_pct"] == 80
    assert result["prior_period_delta_pp"] == -30
    assert result["peer_median_ota_pct"] == 80
    assert result["peer_delta_pp"] == -30
    assert result["peer_rank"] == 3
    assert result["peer_count"] == 3


def test_sparse_prior_period_is_not_presented_as_reliable():
    con = create_memory_connection()
    load_dataframe(
        con,
        "mobility_trip_360",
        pd.DataFrame(
            [
                {
                    "trip_date": pd.Timestamp("2026-07-01"),
                    "business_unit": "BU",
                    "office": "HQ",
                    "vendor_id": "A",
                    "is_ota_eligible": True,
                    "is_on_time": True,
                },
                *[
                    {
                        "trip_date": pd.Timestamp("2026-07-08"),
                        "business_unit": "BU",
                        "office": "HQ",
                        "vendor_id": "A",
                        "is_ota_eligible": True,
                        "is_on_time": False,
                    }
                    for _ in range(10)
                ],
            ]
        ),
    )
    filters = AnalysisFilters(
        business_unit="BU",
        office="HQ",
        start_date=date(2026, 7, 8),
        end_date=date(2026, 7, 14),
    )
    result = ota_scope_benchmark(
        con, filters, column="vendor_id", value="A", min_trips=10
    )
    assert result["prior_period_eligible_trips"] == 1
    assert result["prior_period_ota_pct"] is None
    assert result["prior_period_delta_pp"] is None
