import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from core.chat.duckdb_checkpointer import DuckDBSaver


class _State(TypedDict, total=False):
    messages: Annotated[list[dict], operator.add]
    last: str


def _graph(path):
    builder = StateGraph(_State)
    builder.add_node(
        "reply",
        lambda state: {
            "messages": [{"role": "assistant", "content": f"seen {len(state['messages'])}"}],
            "last": state["messages"][-1]["content"],
        },
    )
    builder.add_edge(START, "reply")
    builder.add_edge("reply", END)
    return builder.compile(checkpointer=DuckDBSaver(path))


def test_conversation_accumulates_across_turns(tmp_path):
    graph = _graph(tmp_path / "memory.duckdb")
    config = {"configurable": {"thread_id": "t1"}}
    graph.invoke({"messages": [{"role": "user", "content": "first"}]}, config)
    state = graph.invoke({"messages": [{"role": "user", "content": "second"}]}, config)
    assert [message["content"] for message in state["messages"]] == [
        "first",
        "seen 1",
        "second",
        "seen 3",
    ]


def test_history_survives_a_new_process(tmp_path):
    path = tmp_path / "memory.duckdb"
    config = {"configurable": {"thread_id": "t1"}}
    _graph(path).invoke({"messages": [{"role": "user", "content": "first"}]}, config)

    reopened = _graph(path)
    assert reopened.get_state(config).values["last"] == "first"
    state = reopened.invoke({"messages": [{"role": "user", "content": "second"}]}, config)
    assert len(state["messages"]) == 4


def test_threads_are_isolated_and_deletable(tmp_path):
    path = tmp_path / "memory.duckdb"
    graph = _graph(path)
    graph.invoke({"messages": [{"role": "user", "content": "a"}]}, {"configurable": {"thread_id": "t1"}})
    graph.invoke({"messages": [{"role": "user", "content": "b"}]}, {"configurable": {"thread_id": "t2"}})
    assert graph.get_state({"configurable": {"thread_id": "t2"}}).values["last"] == "b"

    DuckDBSaver(path).delete_thread("t1")
    assert _graph(path).get_state({"configurable": {"thread_id": "t1"}}).values == {}
