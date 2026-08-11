"""Schema adapters for model-specific tool definitions."""

from __future__ import annotations

from typing import Any

from .gateway import ToolGateway


def build_openai_tool_specs(gateway: ToolGateway) -> list[dict[str, Any]]:
    """Convert gateway tool metadata to OpenAI function calling schema."""

    specs: list[dict[str, Any]] = []
    for tool in gateway.list_tools():
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    return specs
