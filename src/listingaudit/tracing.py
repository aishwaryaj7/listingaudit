"""Tracing with Langfuse: the steps, prompts, tool calls, tokens and time of each run.

Runs are traced when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set in .env (see
README); without them nothing is recorded or sent. Traces go to LANGFUSE_BASE_URL,
Langfuse Cloud in the EU by default, or a self-hosted Langfuse server.
"""

from functools import cache
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from listingaudit.config import settings


@cache
def langfuse() -> Any | None:
    """The Langfuse client, or None when tracing is off."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        base_url=settings.langfuse_base_url,
        environment=settings.langfuse_environment,
    )


def tracer() -> BaseCallbackHandler | None:
    """A callback handler that traces one run, or None when tracing is off.

    A handler keeps the id of the last trace it wrote, so each run gets its own.
    """
    if langfuse() is None:
        return None
    from langfuse.langchain import CallbackHandler

    return CallbackHandler(public_key=settings.langfuse_public_key)


def run_config(
    *callbacks: BaseCallbackHandler | None,
    name: str,
    session: str | None = None,
    user: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """A LangGraph run config with the callbacks, and the names Langfuse files the trace under."""
    metadata: dict[str, Any] = {"langfuse_trace_name": name, "langfuse_tags": tags or []}
    if session:
        metadata["langfuse_session_id"] = session
    if user:
        metadata["langfuse_user_id"] = user
    return {"callbacks": [c for c in callbacks if c is not None], "metadata": metadata}


def trace_id(handler: BaseCallbackHandler | None) -> str | None:
    return getattr(handler, "last_trace_id", None)


def trace_url(trace_id: str) -> str | None:
    """A link to the trace in Langfuse, or None when Langfuse can't be asked for one."""
    if (client := langfuse()) is None:
        return None
    try:
        return client.get_trace_url(trace_id=trace_id)
    except Exception:  # the link is a convenience: the trace id finds the trace too
        return None


def flush() -> None:
    """Send the traces still waiting, before the process ends."""
    if (client := langfuse()) is not None:
        client.flush()
