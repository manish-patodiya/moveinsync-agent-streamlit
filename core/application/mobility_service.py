from __future__ import annotations

from datetime import date, timedelta

from core.alerts.service import AlertService
from core.bootstrap.app_context import AppContext, bootstrap
from core.chat.agent import OperationsChatAgent
from core.models.action import Action, ActionStatus, AuditEvent
from core.models.workflow_state import AnalysisFilters
from core.reason.llm_provider import describe_llm
from core.reason.reasoning_service import draft_alert_escalation
from core.workflow.graph import run_graph


class MobilityService:
    def __init__(self, context: AppContext):
        self.context = context
        self.alerts = AlertService(context)
        self.chat = OperationsChatAgent(context)

    @classmethod
    def create(cls) -> "MobilityService":
        return cls(bootstrap())

    def filter_options(self) -> dict:
        bounds = self.context.conn.execute(
            "SELECT MIN(trip_date), MAX(trip_date) FROM mobility_trip_360"
        ).fetchone()
        business_units = [
            row[0]
            for row in self.context.conn.execute(
                "SELECT DISTINCT business_unit FROM mobility_trip_360 WHERE business_unit IS NOT NULL ORDER BY 1"
            ).fetchall()
        ]
        offices = [
            row[0]
            for row in self.context.conn.execute(
                "SELECT DISTINCT office FROM mobility_trip_360 WHERE office IS NOT NULL ORDER BY 1"
            ).fetchall()
        ]
        return {
            "business_units": business_units,
            "offices": offices,
            "min_date": bounds[0],
            "max_date": bounds[1],
        }

    def default_filters(self) -> AnalysisFilters:
        options = self.filter_options()
        end = options["max_date"]
        days = int(self.context.settings["app"]["default_period_days"])
        return AnalysisFilters(
            business_unit=self.context.settings["app"]["default_business_unit"],
            office=None,
            start_date=max(options["min_date"], end - timedelta(days=days - 1)),
            end_date=end,
        )

    def run(self, filters: AnalysisFilters, progress=None) -> dict:
        return run_graph(self.context, filters, progress)

    def llm_status(self) -> dict:
        config = describe_llm()
        enabled = bool(self.context.settings["llm"]["enabled"])
        return {
            **config,
            "enabled": enabled,
            "active": enabled and bool(config["available"]),
        }

    def data_health(self) -> dict:
        return self.context.data_health.to_dict()

    def live_alerts(self, limit: int = 25):
        return self.alerts.current_unacknowledged(limit)

    def synthetic_alerts(self):
        return self.alerts.synthetic_feed()

    def dummy_contact(self, alert, role: str):
        return self.alerts.dummy_contact(alert, role)

    def alert_escalation_draft(self, alert):
        return draft_alert_escalation(alert)

    def ask_operations(self, question: str, thread_id: str):
        return self.chat.ask(question, thread_id)

    def conversation_history(self, thread_id: str) -> list[dict]:
        return self.chat.history(thread_id)

    def forget_conversation(self, thread_id: str) -> None:
        self.chat.checkpointer.delete_thread(thread_id)

    def transition_action(
        self,
        action: Action,
        requested: ActionStatus,
    ) -> tuple[Action, AuditEvent]:
        previous = action.status
        allowed = {
            ActionStatus.PROPOSED: {ActionStatus.APPROVED, ActionStatus.REJECTED, ActionStatus.REVIEWED},
            ActionStatus.APPROVED: {ActionStatus.SIMULATED_SENT, ActionStatus.REJECTED},
            ActionStatus.REVIEWED: {ActionStatus.APPROVED, ActionStatus.REJECTED},
            ActionStatus.REJECTED: set(),
            ActionStatus.SIMULATED_SENT: set(),
        }
        if requested not in allowed[previous]:
            raise ValueError(f"Cannot move action from {previous} to {requested}")
        if requested == ActionStatus.SIMULATED_SENT and (
            not action.requires_human_approval
            or action.email_subject is None
            or previous != ActionStatus.APPROVED
        ):
            raise ValueError("Email actions must be approved before simulated send")
        updated = action.model_copy(update={"status": requested})
        if (
            requested == ActionStatus.APPROVED
            and action.requires_human_approval
            and action.email_subject is not None
            and self.context.settings["automation"]["auto_mark_approved_actions_simulated_sent"]
        ):
            updated = updated.model_copy(update={"status": ActionStatus.SIMULATED_SENT})
        return updated, AuditEvent(
            action_id=action.action_id,
            previous_status=previous,
            new_status=updated.status,
        )
