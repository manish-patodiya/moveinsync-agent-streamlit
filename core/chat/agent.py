from __future__ import annotations

import operator
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from core.bootstrap.app_context import AppContext
from core.chat.duckdb_checkpointer import DuckDBSaver
from core.chat.tools import run_curated_tool
from core.models.chat import ChatRoute, ChatTool, ChatToolResult
from core.reason.reasoning_service import (
    compose_chat_answer,
    extract_period_days,
    route_chat_question,
)

DEFAULT_MEMORY_PATH = Path(__file__).resolve().parents[2] / ".mobility_pulse" / "chat_memory.duckdb"
PERIOD_TOOLS = {"ALERTS_REPORT", "OTA_REPORT", "SLA_BREACH_REPORT"}
TRIP_TOOLS = {"TRIP_LOOKUP", "TRIP_SAFETY"}


class ChatState(TypedDict, total=False):
    messages: Annotated[list[dict], operator.add]
    route: dict
    result: dict
    route_source: str
    route_detail: str


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
    return route


def describe_route(route: ChatRoute) -> str:
    parts = []
    if route.trip_id:
        parts.append(f"trip `{route.trip_id}`")
    if str(route.tool) in PERIOD_TOOLS:
        parts.append(f"the latest {route.period_days} day(s)")
    if route.tool == ChatTool.OTA_REPORT:
        parts.append(f"grouped by {route.group_by}")
    if route.business_unit:
        parts.append(f"business unit {route.business_unit}")
    if route.office:
        parts.append(f"office {route.office}")
    scope = ", ".join(parts) or "the default reporting window"
    return f"Read `{route.tool}` for {scope}."


class OperationsChatAgent:
    """Checkpointed LangGraph router over fixed, parameterized analytics tools."""

    def __init__(self, context: AppContext, memory_path: str | Path = DEFAULT_MEMORY_PATH):
        self.context = context
        self.checkpointer = DuckDBSaver(memory_path)
        graph = StateGraph(ChatState)
        graph.add_node("route", self._route)
        graph.add_node("run_tool", self._run_tool)
        graph.add_node("answer", self._answer)
        graph.add_edge(START, "route")
        graph.add_edge("route", "run_tool")
        graph.add_edge("run_tool", "answer")
        graph.add_edge("answer", END)
        self.graph = graph.compile(checkpointer=self.checkpointer)

    @staticmethod
    def _route(state: ChatState) -> dict:
        messages = state.get("messages", [])
        question = messages[-1]["content"]
        previous = state.get("route")
        route, source, detail = route_chat_question(question, messages[:-1], previous)
        route = _inherit_context(route, previous, question)
        return {
            "route": route.model_dump(mode="json"),
            "route_source": source,
            "route_detail": detail,
        }

    def _run_tool(self, state: ChatState) -> dict:
        route = ChatRoute.model_validate(state["route"])
        result = run_curated_tool(self.context, route)
        result.route_source = state.get("route_source", "rules")
        result.route_detail = state.get("route_detail", "deterministic router")
        result.interpretation = describe_route(route)
        result.grounded_summary = result.answer
        return {"result": result.model_dump(mode="json")}

    @staticmethod
    def _answer(state: ChatState) -> dict:
        messages = state.get("messages", [])
        result = ChatToolResult.model_validate(state["result"])
        answer, source, detail = compose_chat_answer(
            messages[-1]["content"], result, messages[:-1]
        )
        result.answer = answer
        result.answer_source = source
        result.answer_detail = detail
        payload = result.model_dump(mode="json")
        return {
            "result": payload,
            "messages": [{"role": "assistant", "content": answer, "result": payload}],
        }

    def ask(self, question: str, thread_id: str) -> ChatToolResult:
        state = self.graph.invoke(
            {"messages": [{"role": "user", "content": question}]},
            {"configurable": {"thread_id": thread_id}},
        )
        return ChatToolResult.model_validate(state["result"])

    def history(self, thread_id: str) -> list[dict]:
        """Replay a persisted conversation, so context survives an app restart."""
        snapshot = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        return list(snapshot.values.get("messages", []))
