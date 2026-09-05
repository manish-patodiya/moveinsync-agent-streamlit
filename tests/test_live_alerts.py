from core.alerts.policy import allowed_actions, prioritize_alert
from core.models.live_alert import AlertPriority


def test_sev1_panic_is_critical():
    priority, score = prioritize_alert("Sev-1", "PANIC_MOBILE", "NEW")
    assert priority == AlertPriority.CRITICAL
    assert score == 220


def test_panic_without_source_severity_is_still_critical():
    priority, _score = prioritize_alert(None, "PANIC_FIXED_DEVICE", "OPEN")
    assert priority == AlertPriority.CRITICAL


def test_overspeed_maps_to_driver_not_employee():
    actions = allowed_actions("OVER_SPEEDING", has_employee=True)
    assert actions == ["ACKNOWLEDGE", "ESCALATE", "CALL_DRIVER"]


def test_trip_level_panic_hides_employee_call():
    actions = allowed_actions("PANIC_DEVICE", has_employee=False)
    assert "CALL_DRIVER" in actions
    assert "CALL_EMPLOYEE" not in actions


def test_unknown_alert_still_supports_safe_minimum_actions():
    assert allowed_actions("SOMETHING_NEW", False) == ["ACKNOWLEDGE", "ESCALATE"]
