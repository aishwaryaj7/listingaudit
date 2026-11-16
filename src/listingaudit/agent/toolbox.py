"""The agent's connection to the toolbox, the project's MCP server.

The agent starts the server as a subprocess and talks MCP to it over stdio, like any
other client. Code calls the tools it runs itself, and the rulebook search also goes to
the language model as a LangChain tool, so the model can ask for searches.
"""

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession

SERVER = {
    "transport": "stdio",
    "command": sys.executable,
    "args": ["-m", "listingaudit.toolbox.server"],
    # The server is part of the project, so it gets the agent's whole environment,
    # not only the few variables an MCP client passes on by default
    "env": dict(os.environ),
}


class ToolError(RuntimeError):
    """A tool answered with an error, such as an unknown listing."""


@dataclass
class Toolbox:
    session: ClientSession
    tools: dict[str, BaseTool]

    @classmethod
    async def connect(cls, session: ClientSession) -> "Toolbox":
        tools = await load_mcp_tools(session)
        return cls(session, {tool.name: tool for tool in tools})

    async def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        """Call a tool and return its structured result."""
        result = await self.session.call_tool(name, arguments)
        if result.isError:
            raise ToolError(f"{name}: {result.content[0].text}")
        return result.structuredContent

    @property
    def search(self) -> BaseTool:
        return self.tools["search_rulebook"]


@asynccontextmanager
async def open_toolbox() -> AsyncIterator[Toolbox]:
    """Start the toolbox server, and stop it when the block ends."""
    client = MultiServerMCPClient({"listingaudit": SERVER})
    async with client.session("listingaudit") as session:
        yield await Toolbox.connect(session)
