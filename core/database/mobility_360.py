from __future__ import annotations

import duckdb

PANIC_EVENTS = ("PANIC_FIXED_DEVICE", "PANIC_DEVICE", "PANIC_MOBILE")


class DataModelError(Exception):
    """Raised when mobility_trip_360 is not one row per trip."""


def assert_trip_grain(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    n_360 = con.execute("SELECT COUNT(*) FROM mobility_trip_360").fetchone()[0]
    n_trips = con.execute("SELECT COUNT(DISTINCT trip_id) FROM ride_trips_clean").fetchone()[0]
    if n_360 != n_trips:
        raise DataModelError(
            f"mobility_trip_360 grain failed: COUNT(*)={n_360} "
            f"!= COUNT(DISTINCT trip_id) from ride_trips_clean={n_trips}. "
            "Child tables must be aggregated to one row per trip_id before joining."
        )
    return n_360, n_trips


def build_trip_aggregates_and_360(
    con: duckdb.DuckDBPyConnection,
    zero_rating_policy: str,
) -> tuple[int, int]:
    exclude_zero = zero_rating_policy == "exclude_from_average"
    rating_avg = (
        "AVG(CASE WHEN {col} > 0 THEN {col} END)" if exclude_zero else "AVG({col})"
    )
    low_pred = (
        "({c} BETWEEN 1 AND 2)" if exclude_zero else "({c} BETWEEN 0 AND 2 AND {c} IS NOT NULL)"
    )
    low_any = " OR ".join(
        low_pred.format(c=c)
        for c in (
            "route_rating",
            "driver_rating",
            "cab_rating",
            "safety_rating",
            "marshal_rating",
        )
    )
    panic_list = ", ".join(f"'{e}'" for e in PANIC_EVENTS)

    con.execute(
        """
        CREATE OR REPLACE TABLE employee_trip_agg AS
        SELECT
            trip_id,
            COUNT(*) AS employee_leg_count,
            COUNT(*) FILTER (WHERE stwid IS NOT NULL AND stwid <> '0') AS valid_employee_count,
            COUNT(*) FILTER (WHERE boarding_status = 'Boarded') AS boarded_employee_count,
            COUNT(*) FILTER (WHERE is_no_show) AS employee_no_show_count,
            AVG(pickup_delay_minutes) AS avg_pickup_delay_minutes,
            AVG(drop_delay_minutes) AS avg_drop_delay_minutes
        FROM employee_legs_clean
        GROUP BY trip_id
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE billing_trip_agg AS
        SELECT
            trip_id,
            SUM(trip_cost) AS billed_cost,
            SUM(total_trip_km) AS billed_km,
            COUNT(*) AS billing_line_count,
            CASE
                WHEN SUM(total_trip_km) > 0 THEN SUM(trip_cost) / SUM(total_trip_km)
                ELSE NULL
            END AS cost_per_billed_km,
            ANY_VALUE(contract) AS contract,
            ANY_VALUE(slab_name) AS slab_name,
            ANY_VALUE(vendor) AS billing_vendor,
            BOOL_OR(trip_cost > 0 AND COALESCE(total_trip_km, 0) = 0) AS has_zero_km_billing_exception
        FROM billing_lines_clean
        GROUP BY trip_id
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE alert_trip_agg AS
        SELECT
            trip_id,
            COUNT(*) AS alert_count,
            COUNT(*) FILTER (WHERE severity = 'Sev-1') AS sev1_alert_count,
            COUNT(*) FILTER (WHERE severity = 'Sev-2') AS sev2_alert_count,
            COUNT(*) FILTER (WHERE state_text IN ('OPEN', 'NEW')) AS open_alert_count,
            COUNT(*) FILTER (WHERE acknowledge_ts IS NULL) AS unacknowledged_alert_count,
            AVG(acknowledgement_minutes) AS avg_acknowledgement_minutes,
            MAX(acknowledgement_minutes) FILTER (WHERE severity = 'Sev-1')
                AS max_sev1_acknowledgement_minutes,
            MAX(acknowledgement_minutes) FILTER (WHERE severity = 'Sev-2')
                AS max_sev2_acknowledgement_minutes,
            MIN(start_ts) FILTER (WHERE state_text IN ('OPEN', 'NEW'))
                AS oldest_open_alert_ts,
            BOOL_OR(event_type IN ({panic_list})) AS has_panic_alert,
            BOOL_OR(event_type = 'OVER_SPEEDING') AS has_overspeed_alert
        FROM safety_alerts_clean
        GROUP BY trip_id
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE feedback_trip_agg AS
        SELECT
            trip_id,
            COUNT(*) AS feedback_count,
            {rating_avg.format(col="route_rating")} AS avg_route_rating,
            {rating_avg.format(col="driver_rating")} AS avg_driver_rating,
            {rating_avg.format(col="cab_rating")} AS avg_cab_rating,
            {rating_avg.format(col="safety_rating")} AS avg_safety_rating,
            {rating_avg.format(col="marshal_rating")} AS avg_marshal_rating,
            COUNT(*) FILTER (WHERE {low_any}) AS low_rating_count
        FROM feedback_clean
        GROUP BY trip_id
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE mobility_trip_360 AS
        SELECT
            r.*,
            COALESCE(e.employee_leg_count, 0) AS employee_leg_count,
            COALESCE(e.valid_employee_count, 0) AS valid_employee_count,
            COALESCE(e.boarded_employee_count, 0) AS boarded_employee_count,
            COALESCE(e.employee_no_show_count, 0) AS employee_no_show_count,
            e.avg_pickup_delay_minutes,
            e.avg_drop_delay_minutes,
            b.billed_cost,
            b.billed_km,
            COALESCE(b.billing_line_count, 0) AS billing_line_count,
            b.cost_per_billed_km,
            b.contract,
            b.slab_name,
            b.billing_vendor,
            COALESCE(b.has_zero_km_billing_exception, FALSE) AS has_zero_km_billing_exception,
            COALESCE(a.alert_count, 0) AS alert_count,
            COALESCE(a.sev1_alert_count, 0) AS sev1_alert_count,
            COALESCE(a.sev2_alert_count, 0) AS sev2_alert_count,
            COALESCE(a.open_alert_count, 0) AS open_alert_count,
            COALESCE(a.unacknowledged_alert_count, 0) AS unacknowledged_alert_count,
            a.avg_acknowledgement_minutes,
            a.max_sev1_acknowledgement_minutes,
            a.max_sev2_acknowledgement_minutes,
            a.oldest_open_alert_ts,
            COALESCE(a.has_panic_alert, FALSE) AS has_panic_alert,
            COALESCE(a.has_overspeed_alert, FALSE) AS has_overspeed_alert,
            COALESCE(f.feedback_count, 0) AS feedback_count,
            f.avg_route_rating,
            f.avg_driver_rating,
            f.avg_cab_rating,
            f.avg_safety_rating,
            f.avg_marshal_rating,
            COALESCE(f.low_rating_count, 0) AS low_rating_count
        FROM ride_trips_clean r
        LEFT JOIN employee_trip_agg e USING (trip_id)
        LEFT JOIN billing_trip_agg b USING (trip_id)
        LEFT JOIN alert_trip_agg a USING (trip_id)
        LEFT JOIN feedback_trip_agg f USING (trip_id)
        """
    )
    return assert_trip_grain(con)
