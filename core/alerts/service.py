from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from core.bootstrap.app_context import AppContext
from core.alerts.policy import allowed_actions, prioritize_alert
from core.models.live_alert import ContactDetails, LiveAlert

SYNTHETIC_SCENARIOS = [
    ("PANIC_MOBILE", "Sev-1", True),
    ("OVER_SPEEDING", "Sev-2", False),
    ("EMPLOYEE_GEOFENCE_VIOLATION", "Sev-2", True),
    ("DEVICE_NOT_REACHABLE", "Sev-2", False),
    ("VEHICLE_STOPPAGE", "Sev-3", False),
]


class AlertService:
    def __init__(self, context: AppContext):
        self.context = context

    @staticmethod
    def _build(row: tuple, *, synthetic: bool = False) -> LiveAlert:
        (
            event_id,
            trip_id,
            business_unit,
            office,
            vendor,
            event_type,
            severity,
            state,
            source,
            start_time,
            acknowledged_at,
            employee_ref,
            trip_direction,
            delay_minutes,
            driver_nc,
            cab_nc,
        ) = row
        has_employee = bool(employee_ref and str(employee_ref) != "0")
        priority, score = prioritize_alert(severity, event_type, state)
        return LiveAlert(
            event_id=str(event_id),
            trip_id=str(trip_id),
            business_unit=business_unit,
            office=office,
            vendor=vendor,
            event_type=event_type,
            severity=severity or "UNKNOWN",
            state=state,
            source=source,
            start_time=start_time,
            acknowledged_at=acknowledged_at,
            priority=priority,
            priority_score=score,
            allowed_actions=allowed_actions(event_type, has_employee),
            has_employee_contact=has_employee,
            synthetic=synthetic,
            trip_direction=trip_direction,
            delay_minutes=delay_minutes,
            driver_non_compliant=bool(driver_nc),
            cab_non_compliant=bool(cab_nc),
        )

    def current_unacknowledged(self, limit: int = 25) -> list[LiveAlert]:
        rows = self.context.conn.execute(
            """
            SELECT a.event_id, a.trip_id, a.business_unit, m.office, m.vendor_id,
                   a.event_type, COALESCE(a.severity, 'UNKNOWN'), a.state_text,
                   a.source, a.start_ts, a.acknowledge_ts, a.stwid,
                   m.trip_direction, m.calculated_delay_minutes,
                   m.is_driver_nc, m.is_cab_nc
            FROM safety_alerts_clean a
            LEFT JOIN mobility_trip_360 m USING (trip_id)
            WHERE a.acknowledge_ts IS NULL
            ORDER BY
              CASE a.severity WHEN 'Sev-1' THEN 1 WHEN 'Sev-2' THEN 2
                              WHEN 'Sev-3' THEN 3 ELSE 4 END,
              a.start_ts DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        return sorted((self._build(row) for row in rows), key=lambda alert: -alert.priority_score)

    def synthetic_feed(self) -> list[LiveAlert]:
        trips = self.context.conn.execute(
            """
            SELECT trip_id, business_unit, office, vendor_id, trip_direction,
                   calculated_delay_minutes, is_driver_nc, is_cab_nc
            FROM mobility_trip_360
            WHERE trip_date = (SELECT MAX(trip_date) FROM mobility_trip_360)
              AND vendor_id IS NOT NULL
            ORDER BY trip_id
            LIMIT 5
            """
        ).fetchall()
        now = datetime.now(timezone.utc)
        alerts: list[LiveAlert] = []
        for index, (trip, scenario) in enumerate(zip(trips, SYNTHETIC_SCENARIOS)):
            trip_id, bu, office, vendor, direction, delay, driver_nc, cab_nc = trip
            event_type, severity, employee_linked = scenario
            row = (
                f"demo-alert-{index + 1}",
                trip_id,
                bu,
                office,
                vendor,
                event_type,
                severity,
                "NEW",
                "DEMO_FEED",
                now - timedelta(minutes=index + 1),
                None,
                f"demo-employee-{index + 1}" if employee_linked else "0",
                direction,
                delay,
                driver_nc,
                cab_nc,
            )
            alerts.append(self._build(row, synthetic=True))
        return alerts

    @staticmethod
    def dummy_contact(alert: LiveAlert, role: str) -> ContactDetails:
        seed = f"{alert.event_id}:{role}".encode()
        last_four = int(hashlib.sha1(seed).hexdigest()[:8], 16) % 10_000
        return ContactDetails(
            role=role.upper(),
            display_name=f"Demo {role.title()} · Trip {alert.trip_id[-4:]}",
            masked_phone=f"+91 98•• ••{last_four:04d}",
        )
