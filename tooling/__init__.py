"""Tooling abstractions for local and protocol-based tool execution."""

from .gateway import LocalToolGateway, ToolDefinition, ToolGateway
from .gateway_resolver import GatewaySelection, resolve_gateway
from .mcp_adapter_http import HttpMcpAdapter, HttpMcpConfig
from .mcp_gateway import McpAdapterProtocol, McpToolGateway
from .schema_adapter import build_openai_tool_specs

__all__ = [
    "GatewaySelection",
    "HttpMcpAdapter",
    "HttpMcpConfig",
    "LocalToolGateway",
    "McpAdapterProtocol",
    "McpToolGateway",
    "ToolDefinition",
    "ToolGateway",
    "build_openai_tool_specs",
    "resolve_gateway",
]
