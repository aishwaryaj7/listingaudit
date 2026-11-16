"""A stand-in language model that answers from a script, to run the agent for free.

The tests and the notebook drive the graph with it, without paid calls. It works like
LangChain's own fake chat models, but also answers requests for structured output.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import Field


class ScriptedModel(BaseChatModel):
    """Gives each request the next answer of its script, and keeps the requests.

    An answer is a message, an object such as a KindChoice, or a function that makes
    one from the request's messages.
    """

    script: list[Any]
    requests: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def answer(self, messages: list[BaseMessage]) -> Any:
        if not self.script:
            raise RuntimeError("The script has no answers left.")
        self.requests.append(messages)
        answer = self.script.pop(0)
        return answer(messages) if callable(answer) else answer

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.answer(messages))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:
        return RunnableLambda(self.answer)
