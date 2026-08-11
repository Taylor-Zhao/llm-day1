"""Tool gateway abstraction.

This module isolates tool discovery and tool invocation so scripts can switch
from local tools to MCP tools without changing orchestration logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ToolDefinition:
    """Local tool metadata and executor."""

    name: str
    description: str
    parameters: JsonObject
    executor: Callable[..., JsonObject]


class ToolGateway(Protocol):
    """Gateway interface for listing and calling tools."""

    def list_tools(self) -> list[ToolDefinition]:
        raise NotImplementedError

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> JsonObject:
        raise NotImplementedError


class LocalToolGateway:
    """Simple in-memory tool gateway for local Python functions."""

    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tool_map = {tool.name: tool for tool in tools}

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tool_map.values())

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> JsonObject:
        tool = self._tool_map.get(name)
        if tool is None:
            return {
                "tool_name": name,
                "ok": False,
                "arguments": dict(arguments),
                "error": "unknown tool",
            }
        try:
            result = tool.executor(**dict(arguments))
            return {
                "tool_name": name,
                "ok": True,
                "arguments": dict(arguments),
                "result": result,
            }
        except Exception as exc:
            return {
                "tool_name": name,
                "ok": False,
                "arguments": dict(arguments),
                "error": str(exc),
            }
