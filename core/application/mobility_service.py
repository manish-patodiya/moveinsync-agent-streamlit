from __future__ import annotations

from datetime import timedelta

from core.act.action_policy import create_persona_actions
from core.act.action_store import ActionStore
from core.alerts.service import AlertService
from core.bootstrap.app_context import AppContext, bootstrap
from core.chat.agent import OperationsChatAgent
from core.models.action import Action, ActionStatus, AuditEvent, ManagerRole
from core.models.persona import PERSONA_PERIOD_DAYS, Persona, PersonaScope
from core.models.workflow_state import AnalysisFilters
from core.reason.llm_provider import describe_llm
from core.reason.reasoning_service import draft_alert_escalation, reason_about_issue, to_persona_decision
from core.sense.leadership_brief import leadership_brief_html, leadership_brief_markdown
from core.sense.persona_insights import build_persona_pulse
from core.workflow.graph import run_graph


class MobilityService:
    def __init__(self, context: AppContext):
        self.context = context
        self.alerts = AlertService(context)
        self.chat = OperationsChatAgent(context)
        self.action_store = ActionStore(self.chat.checkpointer)
        self._persona_cache: dict[str, dict] = {}

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
        shifts = [
            row[0]
            for row in self.context.conn.execute(
                "SELECT DISTINCT shift_type FROM mobility_trip_360 WHERE shift_type IS NOT NULL ORDER BY 1"
            ).fetchall()
        ]
        return {
            "business_units": business_units,
            "offices": offices,
            "shifts": shifts,
            "min_date": bounds[0],
            "max_date": bounds[1],
        }

    def default_scope(self, persona: Persona) -> PersonaScope:
        options = self.filter_options()
        end = options["max_date"]
        days = PERSONA_PERIOD_DAYS[persona]
        office = None
        shift = None
        if persona == Persona.LINE_MANAGER:
            busy = self.context.conn.execute(
                """
                SELECT office, shift_type
                FROM employee_legs_clean
                WHERE trip_date = ? AND office IS NOT NULL AND shift_type IS NOT NULL
                  AND stwid IS NOT NULL AND stwid <> '0'
                GROUP BY 1, 2
                ORDER BY COUNT(*) DESC
                LIMIT 1
                """,
                [end],
            ).fetchone()
            if busy:
                office, shift = busy
            elif options["offices"] and options["shifts"]:
                office, shift = options["offices"][0], options["shifts"][0]
        return PersonaScope(
            persona=persona,
            business_unit=self.context.settings["app"]["default_business_unit"],
            office=office,
            shift=shift,
            start_date=max(options["min_date"], end - timedelta(days=days - 1)),
            end_date=end,
        )

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

    def ask_operations(
        self,
        question: str,
        thread_id: str,
        *,
        persona: str | None = None,
        scope: dict | None = None,
    ):
        result = self.chat.ask(question, thread_id, persona=persona, scope=scope)
        actions = result.proposed_actions or (
            [result.proposed_action] if result.proposed_action else []
        )
        self.save_new_actions(actions)
        return result

    def conversation_history(self, thread_id: str) -> list[dict]:
        return self.chat.history(thread_id)

    def forget_conversation(self, thread_id: str) -> None:
        self.chat.checkpointer.delete_thread(thread_id)

    def _cache_key(self, scope: PersonaScope) -> str:
        return "|".join(
            [
                str(scope.persona),
                str(scope.business_unit),
                str(scope.office),
                str(scope.shift),
                str(scope.start_date),
                str(scope.end_date),
            ]
        )

    def persona_workspace(self, scope: PersonaScope, *, use_llm: bool = True) -> dict:
        key = self._cache_key(scope)
        if key in self._persona_cache:
            return self._persona_cache[key]
        pulse = build_persona_pulse(
            self.context.conn, scope, self.context.sla, self.context.thresholds
        )
        caveats = list(pulse.caveats) + list(self.context.data_health.errors)
        reasoning = {}
        decisions = []
        enabled = use_llm and bool(self.context.settings["llm"]["enabled"])
        for issue in pulse.issues[: int(self.context.settings["app"]["max_issues_for_reasoning"])]:
            result = reason_about_issue(
                issue, caveats, llm_enabled=enabled, persona=str(scope.persona)
            )
            reasoning[issue.issue_id] = result
            decisions.append(to_persona_decision(issue, result, str(scope.persona)))
        outputs = {issue_id: result.output for issue_id, result in reasoning.items()}
        actions = create_persona_actions(
            scope.persona, pulse.issues, outputs, self.context.settings["automation"]
        )
        self.save_new_actions(actions)
        brief = leadership_brief_markdown(scope, pulse, decisions)
        payload = {
            "pulse": pulse,
            "reasoning": reasoning,
            "decisions": decisions,
            "actions": actions,
            "brief_markdown": brief,
            "brief_html": leadership_brief_html(brief),
        }
        self._persona_cache[key] = payload
        return payload

    def vendor_drill(self, scope: PersonaScope, vendor: str) -> dict:
        from core.sense.persona_insights import _filters_for, vendor_drill

        return vendor_drill(self.context.conn, _filters_for(scope), vendor)

    def rider_trip_context(self, trip_id: str) -> dict:
        from core.sense.persona_insights import rider_trip_context

        return rider_trip_context(self.context.conn, trip_id)

    def propose_vendor_follow_up(self, scope: PersonaScope, vendor: str, rationale: str) -> list[Action]:
        from core.models.evidence import Evidence
        from core.models.issue import CandidateIssue, IssueType, Severity
        from core.reason.reasoning_service import template_reasoning

        issue_type = (
            IssueType.BILLING_ANOMALY
            if scope.persona == Persona.FACILITIES_HEAD
            else IssueType.VENDOR_OTA_BREACH
        )
        allowed = (
            ["REVIEW_VENDOR_PERFORMANCE", "RECORD_SLA_RECOVERY", "SHARE_MOBILITY_BRIEF"]
            if scope.persona == Persona.FACILITIES_HEAD
            else ["DRAFT_VENDOR_ESCALATION_EMAIL", "REQUEST_CORRECTIVE_PLAN", "MONITOR_KPI"]
        )
        issue = CandidateIssue(
            issue_id=f"canvas-{scope.persona}-{vendor}-{scope.start_date}-{scope.end_date}",
            issue_type=issue_type,
            severity=Severity.HIGH,
            title=f"{vendor} · selected for follow-up",
            business_scope={"vendor": vendor, "office": scope.office, "shift": scope.shift},
            current_metric="vendor_focus",
            current_value=vendor,
            affected_trip_count=0,
            evidence=[Evidence(label="Vendor", value=vendor), Evidence(label="Canvas rationale", value=rationale)],
            data_confidence="HIGH",
            allowed_action_types=allowed,
        )
        actions = create_persona_actions(
            scope.persona,
            [issue],
            {issue.issue_id: template_reasoning(issue)},
            self.context.settings["automation"],
        )
        self.save_new_actions(actions)
        return actions

    def propose_rider_follow_up(self, scope: PersonaScope, rider: str, trip_id: str) -> list[Action]:
        from core.models.evidence import Evidence
        from core.models.issue import CandidateIssue, IssueType, Severity
        from core.reason.reasoning_service import template_reasoning

        issue = CandidateIssue(
            issue_id=f"canvas-rider-{rider}-{trip_id}-{scope.end_date}",
            issue_type=IssueType.SHIFT_READINESS,
            severity=Severity.HIGH,
            title=f"Follow up {rider} on trip {trip_id}",
            business_scope={"office": scope.office, "shift": scope.shift, "vendor": None},
            current_metric="masked_rider",
            current_value=rider,
            affected_trip_count=1,
            evidence=[
                Evidence(label="Rider", value=rider, source="employee_legs_clean"),
                Evidence(label="Trip", value=trip_id, source="mobility_trip_360"),
            ],
            data_confidence="HIGH",
            allowed_action_types=["REQUEST_RIDER_FOLLOW_UP", "ASSIGN_SHIFT_FOLLOW_UP"],
        )
        actions = create_persona_actions(
            scope.persona,
            [issue],
            {issue.issue_id: template_reasoning(issue)},
            {"auto_create_manager_alerts": False, "auto_create_email_drafts": False},
        )
        self.save_new_actions(actions)
        return actions

    def save_new_actions(self, actions: list[Action]) -> None:
        known = {item.action_id for item in self.action_store.list()}
        self.save_actions([action for action in actions if action.action_id not in known])

    def transition_action(
        self,
        action: Action,
        requested: ActionStatus,
        *,
        actor_role: ManagerRole = ManagerRole.TRANSPORT_MANAGER,
        note: str | None = None,
    ) -> tuple[Action, AuditEvent]:
        previous = action.status
        allowed = {
            ActionStatus.PROPOSED: {ActionStatus.APPROVED, ActionStatus.REJECTED, ActionStatus.REVIEWED},
            ActionStatus.APPROVED: {
                ActionStatus.SIMULATED_SENT,
                ActionStatus.SIMULATED_COMPLETED,
                ActionStatus.REJECTED,
            },
            ActionStatus.REVIEWED: {ActionStatus.APPROVED, ActionStatus.REJECTED},
            ActionStatus.REJECTED: set(),
            ActionStatus.SIMULATED_SENT: set(),
            ActionStatus.SIMULATED_COMPLETED: set(),
        }
        if requested not in allowed[previous]:
            raise ValueError(f"Cannot move action from {previous} to {requested}")
        if requested == ActionStatus.SIMULATED_SENT and (
            not action.requires_human_approval
            or action.email_subject is None
            or previous != ActionStatus.APPROVED
        ):
            raise ValueError("Email actions must be approved before simulated send")
        if requested == ActionStatus.SIMULATED_COMPLETED and (
            previous != ActionStatus.APPROVED
        ):
            raise ValueError("Actions must be approved before simulated execution")
        updated = action.model_copy(update={"status": requested})
        if (
            requested == ActionStatus.APPROVED
            and action.requires_human_approval
            and action.email_subject is not None
            and self.context.settings["automation"]["auto_mark_approved_actions_simulated_sent"]
        ):
            updated = updated.model_copy(update={"status": ActionStatus.SIMULATED_SENT})
        event = AuditEvent(
            action_id=action.action_id,
            previous_status=previous,
            new_status=updated.status,
            actor_role=actor_role,
            source=action.source,
            note=note,
        )
        self.action_store.save(updated)
        self.action_store.record(event)
        return updated, event

    def save_actions(self, actions: list[Action]) -> None:
        self.action_store.save_all(actions)

    def saved_actions(self) -> list[Action]:
        return self.action_store.list()

    def action_audit(self) -> list[AuditEvent]:
        return self.action_store.audit()
