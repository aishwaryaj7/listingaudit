"""Tracing with Langfuse: off without keys, and with keys the spans of each run.

Tracing is off in every other test (see conftest.py). Here the traced runs go to an
in-memory exporter instead of a Langfuse server.
"""

import functools

import langfuse
import pytest
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from listingaudit import tracing
from listingaudit.agent.scripted import ScriptedModel
from listingaudit.config import settings


@pytest.fixture
def exporter(monkeypatch):
    """Keys for a Langfuse project, with the spans kept in memory instead of sent."""
    spans = InMemorySpanExporter()
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-lf-test")
    monkeypatch.setattr(settings, "langfuse_secret_key", SecretStr("sk-lf-test"))
    monkeypatch.setattr(settings, "langfuse_base_url", "http://127.0.0.1:9")
    client = functools.partial(
        langfuse.Langfuse, span_exporter=spans, tracer_provider=TracerProvider()
    )
    monkeypatch.setattr(langfuse, "Langfuse", client)
    yield spans
    tracing.langfuse().shutdown()
    tracing.langfuse.cache_clear()


def one_step_graph(model: ScriptedModel):
    graph = StateGraph(MessagesState)
    graph.add_node("ask", lambda state: {"messages": [model.invoke(state["messages"])]})
    graph.add_edge(START, "ask")
    graph.add_edge("ask", END)
    return graph.compile()


def test_without_keys_nothing_is_traced():
    usage = UsageMetadataCallbackHandler()
    assert tracing.tracer() is None
    assert tracing.trace_url("0" * 32) is None
    assert tracing.run_config(usage, None, name="audit") == {
        "callbacks": [usage],
        "metadata": {"langfuse_trace_name": "audit", "langfuse_tags": []},
    }
    tracing.flush()


def test_a_traced_run_records_its_steps_under_one_trace(exporter):
    handler = tracing.tracer()
    config = tracing.run_config(
        UsageMetadataCallbackHandler(),
        handler,
        name="review",
        session="42",
        user="Test Reviewer",
        tags=["eval"],
    )
    one_step_graph(ScriptedModel(script=[AIMessage("Hello")])).invoke(
        {"messages": [HumanMessage("Hi")]}, config
    )
    tracing.flush()

    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {"LangGraph", "ask", "ScriptedModel"}
    [trace] = {format(span.context.trace_id, "032x") for span in spans}
    assert tracing.trace_id(handler) == trace
    root = next(span for span in spans if span.name == "LangGraph").attributes
    assert root["langfuse.trace.name"] == "review"
    assert root["session.id"] == "42"
    assert root["user.id"] == "Test Reviewer"
    assert list(root["langfuse.trace.tags"]) == ["eval"]
    assert root["langfuse.environment"] == "local"


def test_each_run_gets_a_trace_of_its_own(exporter):
    graph = one_step_graph(ScriptedModel(script=[AIMessage("One"), AIMessage("Two")]))
    ids = []
    for _ in range(2):
        handler = tracing.tracer()
        graph.invoke({"messages": [HumanMessage("Hi")]}, tracing.run_config(handler, name="audit"))
        ids.append(tracing.trace_id(handler))
    assert ids[0] != ids[1]


def test_a_trace_without_a_link_still_has_its_id(exporter):
    assert tracing.trace_url("0" * 32) is None  # no Langfuse server to ask for the project
