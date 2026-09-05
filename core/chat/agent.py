from __future__ import annotations

import operator
import os
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from core.bootstrap.app_context import AppContext
from core.chat.duckdb_checkpointer import DuckDBSaver
from core.chat.tools import requested_action, run_curated_tool
from core.models.chat import ChatPlan, ChatPlanStep, ChatRoute, ChatTool, ChatToolResult
from core.reason.reasoning_service import (
    compose_chat_answer,
    extract_period_days,
    route_chat_question,
)

DEFAULT_MEMORY_PATH = Path(__file__).resolve().parents[2] / ".mobility_pulse" / "chat_memory.duckdb"
PERIOD_TOOLS = {
    "ALERTS_REPORT",
    "OTA_REPORT",
    "SLA_BREACH_REPORT",
    "SHIFT_READINESS_REPORT",
    "NO_SHOW_REPORT",
    "DELAY_REPORT",
}
TRIP_TOOLS = {"TRIP_LOOKUP", "TRIP_SAFETY"}
SCOPE_FIELDS = ("business_unit", "office", "shift", "vendor")
# These tools answer "which vendor", so filtering them to one vendor turns a ranking into a
# single row that the writer then reports as the best or the worst of the fleet.
VENDOR_RANKING_TOOLS = {ChatTool.ENTITY_COMPARISON, ChatTool.SLA_BREACH_REPORT, ChatTool.BILLING_REPORT}
VENDOR_GROUPED_TOOLS = {
    ChatTool.OTA_REPORT,
    ChatTool.NO_SHOW_REPORT,
    ChatTool.FEEDBACK_REPORT,
    ChatTool.UTILIZATION_REPORT,
}


def ranks_vendors(route: ChatRoute) -> bool:
    return route.tool in VENDOR_RANKING_TOOLS or (
        route.tool in VENDOR_GROUPED_TOOLS and route.group_by == "vendor"
    )


class ChatState(TypedDict, total=False):
    messages: Annotated[list[dict], operator.add]
    route: dict
    result: dict
    route_source: str
    route_detail: str
    plan: dict
    persona: str
    scope: dict


def _inherit_context(route: ChatRoute, previous: dict | None, question: str) -> ChatRoute:
    """Carry filters forward so a follow-up keeps the subject of the last question."""
    if not previous:
        return route
    if str(route.tool) in PERIOD_TOOLS and extract_period_days(question) is None:
        if previous.get("tool") in PERIOD_TOOLS:
            route.period_days = int(previous.get("period_days", route.period_days))
    if str(route.tool) in TRIP_TOOLS and not route.trip_id:
        route.trip_id = previous.get("trip_id")
    route.business_unit = route.business_unit or previous.get("business_unit")
    route.office = route.office or previous.get("office")
    route.shift = route.shift or previous.get("shift")
    route.vendor = route.vendor or previous.get("vendor")
    return route


def scope_phrase(route: ChatRoute) -> str:
    """Name every filter the query applied, so a scope-wide number is never read as vendor-specific."""
    if route.trip_id:
        return f"trip `{route.trip_id}`"
    parts = [f"the latest {route.period_days} day(s)"]
    if route.tool == ChatTool.OTA_REPORT:
        parts.append(f"grouped by {route.group_by}")
    if route.business_unit:
        parts.append(f"business unit {route.business_unit}")
    if route.office:
        parts.append(f"office {route.office}")
    if route.shift:
        parts.append(f"shift {route.shift}")
    parts.append(f"vendor {route.vendor}" if route.vendor else "all vendors")
    return ", ".join(parts)


def describe_route(route: ChatRoute) -> str:
    return f"Read `{route.tool}` for {scope_phrase(route)}."


def build_plan(question: str, route: ChatRoute) -> ChatPlan:
    """Create a bounded tool plan; every step is a registered read-only query."""
    from core.models.persona import Persona, allowed_tools

    routes = [route]
    text = question.lower()
    if route.tool == ChatTool.OPERATIONAL_OVERVIEW or any(
        phrase in text for phrase in ("everything", "all areas", "full picture", "operating story")
    ):
        if route.persona == Persona.FACILITIES_HEAD:
            routes = [
                route.model_copy(update={"tool": ChatTool.OPERATIONAL_OVERVIEW}),
                route.model_copy(update={"tool": ChatTool.ENTITY_COMPARISON, "group_by": "vendor"}),
                route.model_copy(update={"tool": ChatTool.SLA_BREACH_REPORT}),
                route.model_copy(update={"tool": ChatTool.BILLING_REPORT}),
            ]
        elif route.persona == Persona.LINE_MANAGER:
            routes = [route.model_copy(update={"tool": ChatTool.SHIFT_READINESS_REPORT})]
        else:
            routes = [
                route.model_copy(update={"tool": ChatTool.OPERATIONAL_OVERVIEW}),
                route.model_copy(update={"tool": ChatTool.ENTITY_COMPARISON, "group_by": "vendor"}),
                route.model_copy(update={"tool": ChatTool.ALERTS_REPORT}),
                route.model_copy(update={"tool": ChatTool.SLA_BREACH_REPORT}),
                route.model_copy(update={"tool": ChatTool.IMPACTED_TRIPS}),
            ]
    routes = [
        item.model_copy(update={"vendor": None}) if ranks_vendors(item) else item for item in routes
    ]
    if route.persona:
        allowed = allowed_tools(Persona(route.persona))
        routes = [item if item.tool in allowed else item.model_copy(update={"tool": ChatTool.HELP}) for item in routes]
        routes = [item for item in routes if item.tool != ChatTool.HELP] or [route]
    purpose = {
        ChatTool.OPERATIONAL_OVERVIEW: "Establish cross-domain operating KPIs",
        ChatTool.ENTITY_COMPARISON: "Compare performance across the requested dimension",
        ChatTool.ALERTS_REPORT: "Review safety volume and acknowledgement",
        ChatTool.SLA_BREACH_REPORT: "Identify material SLA breaches",
        ChatTool.IMPACTED_TRIPS: "Identify trips for follow-up",
        ChatTool.SHIFT_READINESS_REPORT: "Masked shift readiness roster",
        ChatTool.BILLING_REPORT: "Review billed spend and cost/km",
    }
    return ChatPlan(
        goal=question,
        steps=[
            ChatPlanStep(
                step=index,
                tool=item.tool,
                purpose=purpose.get(item.tool, f"Gather verified {item.tool} evidence"),
                route=item,
            )
            for index, item in enumerate(routes, start=1)
        ],
        requires_approval=bool(route.action_intent),
        proposed_action_intent=route.action_intent,
    )


class OperationsChatAgent:
    """Checkpointed LangGraph router over fixed, parameterized analytics tools."""

    def __init__(self, context: AppContext, memory_path: str | Path | None = None):
        self.context = context
        self._vendors: list[str] | None = None
        configured_path = memory_path or os.getenv("MOBILITY_MEMORY_PATH") or DEFAULT_MEMORY_PATH
        self.checkpointer = DuckDBSaver(configured_path)
        graph = StateGraph(ChatState)
        graph.add_node("route", self._route)
        graph.add_node("run_tool", self._run_tool)
        graph.add_node("answer", self._answer)
        graph.add_edge(START, "route")
        graph.add_edge("route", "run_tool")
        graph.add_edge("run_tool", "answer")
        graph.add_edge("answer", END)
        self.graph = graph.compile(checkpointer=self.checkpointer)

    def _named_vendor(self, question: str) -> str | None:
        """A vendor named in the question outranks the canvas selection."""
        if self._vendors is None:
            self._vendors = [
                str(row[0])
                for row in self.context.conn.execute(
                    "SELECT DISTINCT vendor_id FROM mobility_trip_360 WHERE vendor_id IS NOT NULL"
                ).fetchall()
            ]
        text = question.lower()
        return next((vendor for vendor in self._vendors if vendor.lower() in text), None)

    def _route(self, state: ChatState) -> dict:
        messages = state.get("messages", [])
        question = messages[-1]["content"]
        previous = state.get("route")
        persona = state.get("persona")
        route, source, detail = route_chat_question(question, messages[:-1], previous, persona)
        scope = state.get("scope") or {}
        route.persona = persona or route.persona
        route.manager_role = persona or route.manager_role
        # Filters this question named itself, before inheritance can fill the blanks.
        asked = {field: getattr(route, field) for field in SCOPE_FIELDS}
        route = _inherit_context(route, previous, question)
        # The workspace filters are visible controls, so they outrank an inherited subject:
        # an old vendor must not silently narrow the roster the manager is looking at.
        for field, value in asked.items():
            if value is None and field in scope:
                setattr(route, field, scope[field])
        if scope.get("period_days") and extract_period_days(question) is None:
            route.period_days = int(scope["period_days"])
        route.vendor = self._named_vendor(question) or route.vendor
        if ranks_vendors(route):
            route.vendor = None
        plan = build_plan(question, route)
        return {
            "route": route.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "route_source": source,
            "route_detail": detail,
        }

    def _run_tool(self, state: ChatState) -> dict:
        route = ChatRoute.model_validate(state["route"])
        plan = ChatPlan.model_validate(state["plan"])
        results = [run_curated_tool(self.context, step.route) for step in plan.steps]
        result = results[0]
        if len(results) > 1:
            result.answer = "\n".join(item.answer for item in results)
            result.rows = [
                {"evidence_domain": str(item.tool), **row}
                for item in results
                for row in item.rows
            ]
            result.report_name = f"operations_evidence_{route.period_days}_days.csv"
            result.row_order = "grouped by plan step; each domain retains its documented order"
            result.proposed_action = next(
                (item.proposed_action for item in results if item.proposed_action), None
            )
        result.route_source = state.get("route_source", "rules")
        result.route_detail = state.get("route_detail", "deterministic router")
        result.interpretation = describe_route(route)
        result.grounded_summary = f"{result.answer} Scope: {scope_phrase(route)}."
        result.plan = plan
        result.evidence_summaries = [item.answer for item in results]
        actions = [item.proposed_action for item in results if item.proposed_action]
        if requested := requested_action(route, result.grounded_summary):
            actions.insert(0, requested)
            result.proposed_action = requested
        result.proposed_actions = actions
        return {"result": result.model_dump(mode="json")}

    @staticmethod
    def _answer(state: ChatState) -> dict:
        messages = state.get("messages", [])
        result = ChatToolResult.model_validate(state["result"])
        answer, source, detail = compose_chat_answer(
            messages[-1]["content"], result, state.get("persona")
        )
        result.answer = answer
        result.answer_source = source
        result.answer_detail = detail
        payload = result.model_dump(mode="json")
        return {
            "result": payload,
            "messages": [{"role": "assistant", "content": answer, "result": payload}],
        }

    def ask(
        self,
        question: str,
        thread_id: str,
        *,
        persona: str | None = None,
        scope: dict | None = None,
    ) -> ChatToolResult:
        state = self.graph.invoke(
            {
                "messages": [{"role": "user", "content": question}],
                "persona": persona or "TRANSPORT_MANAGER",
                "scope": scope or {},
            },
            {"configurable": {"thread_id": thread_id}},
        )
        return ChatToolResult.model_validate(state["result"])

    def history(self, thread_id: str) -> list[dict]:
        """Replay a persisted conversation, so context survives an app restart."""
        snapshot = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        return list(snapshot.values.get("messages", []))
