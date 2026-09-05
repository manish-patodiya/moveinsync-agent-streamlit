import pandas as pd
import pytest

from core.database.duckdb_connection import create_memory_connection, load_dataframe
from core.database.mobility_360 import DataModelError, assert_trip_grain, build_trip_aggregates_and_360


def _tiny_tables():
    rides = pd.DataFrame({"trip_id": ["A", "B"]})
    employees = pd.DataFrame(
        {
            "trip_id": ["A", "A", "B"],
            "stwid": ["1", "2", "3"],
            "boarding_status": ["Boarded", "Boarded", "Boarded"],
            "is_no_show": [False, False, False],
            "pickup_delay_minutes": [1.0, 2.0, 0.0],
            "drop_delay_minutes": [1.0, 2.0, 0.0],
        }
    )
    billing = pd.DataFrame(
        {
            "trip_id": ["A"],
            "trip_cost": [100.0],
            "total_trip_km": [10.0],
            "vendor": ["Vendor A"],
            "contract": ["X"],
            "slab_name": ["Medium"],
        }
    )
    alerts = pd.DataFrame(
        {
            "trip_id": ["A"],
            "severity": ["Sev-1"],
            "state_text": ["CLOSED"],
            "acknowledge_ts": [pd.Timestamp("2026-05-01")],
            "start_ts": [pd.Timestamp("2026-05-01")],
            "acknowledgement_minutes": [5.0],
            "event_type": ["OVER_SPEEDING"],
        }
    )
    feedback = pd.DataFrame(
        {
            "trip_id": ["B", "B"],
            "route_rating": [5, 0],
            "driver_rating": [4, 5],
            "cab_rating": [5, 5],
            "safety_rating": [5, 5],
            "marshal_rating": [0, 0],
        }
    )
    return rides, employees, billing, alerts, feedback


def test_mobility_360_one_row_per_trip():
    rides, employees, billing, alerts, feedback = _tiny_tables()
    con = create_memory_connection()
    load_dataframe(con, "ride_trips_clean", rides)
    load_dataframe(con, "employee_legs_clean", employees)
    load_dataframe(con, "billing_lines_clean", billing)
    load_dataframe(con, "safety_alerts_clean", alerts)
    load_dataframe(con, "feedback_clean", feedback)
    n_360, n_trips = build_trip_aggregates_and_360(con, "exclude_from_average")
    assert n_360 == n_trips == 2
    avg_route = con.execute(
        "SELECT avg_route_rating FROM mobility_trip_360 WHERE trip_id = 'B'"
    ).fetchone()[0]
    assert avg_route == 5.0


def test_duplicate_spine_trip_ids_are_collapsed():
    from core.ingestion.ride_trip_loader import dedupe_trip_spine

    df = pd.DataFrame(
        {
            "trip_id": ["A", "A", "B"],
            "trip_date": pd.to_datetime(["2026-05-01", "2026-06-01", "2026-05-01"]),
            "source_file": ["may.csv", "june.csv", "may.csv"],
        }
    )
    unique, collapsed = dedupe_trip_spine(df)
    assert collapsed == 1
    assert list(unique["trip_id"]) == ["A", "B"]
    assert str(unique.loc[unique["trip_id"] == "A", "source_file"].iloc[0]) == "june.csv"


def test_invariant_catches_non_aggregated_join():
    rides, employees, billing, alerts, feedback = _tiny_tables()
    con = create_memory_connection()
    load_dataframe(con, "ride_trips_clean", rides)
    load_dataframe(con, "employee_legs_clean", employees)
    load_dataframe(con, "billing_lines_clean", billing)
    load_dataframe(con, "safety_alerts_clean", alerts)
    load_dataframe(con, "feedback_clean", feedback)
    con.execute(
        """
        CREATE TABLE mobility_trip_360 AS
        SELECT r.trip_id
        FROM ride_trips_clean r
        LEFT JOIN employee_legs_clean e USING (trip_id)
        """
    )
    with pytest.raises(DataModelError, match="grain failed"):
        assert_trip_grain(con)
