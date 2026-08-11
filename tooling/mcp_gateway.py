"""MCP gateway abstractions.

中文说明：
- 本文件不强依赖某个 MCP SDK，避免你在没有安装 MCP 客户端库时无法运行。
- 这里定义了一个“适配器协议”：只要外部对象能提供 list_tools / call_tool，
  就可以被包装成 McpToolGateway。
- 这样做的目标是：先把业务脚本改造成“网关可替换”，后续再接真实 MCP 传输层。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .gateway import ToolDefinition, ToolGateway


JsonObject = dict[str, Any]


class McpAdapterProtocol(Protocol):
    """MCP 适配器协议。

    约定：
    1. list_tools() 返回工具元数据列表；
    2. call_tool(name, arguments) 返回工具执行结果（JSON 可序列化）。

    你可以用任意实现来满足这个协议：
    - 基于官方 MCP SDK 的实现；
    - 基于你自建网关服务的实现；
    - 测试中的 Fake/Mock 实现。
    """

    def list_tools(self) -> list[Mapping[str, Any]]:
        raise NotImplementedError

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class McpGatewayMeta:
    """记录 MCP 网关的运行时元信息，便于日志与排障。"""

    source: str
    adapter_name: str


class McpToolGateway:
    """把 MCP 适配器包装成 ToolGateway。

    关键设计点：
    - list_tools() 的结果会做一次轻量缓存，避免每轮都远程拉取元数据；
    - call_tool() 统一输出与 LocalToolGateway 一致的字段，保证上层流程无感知。
    """

    def __init__(self, adapter: McpAdapterProtocol, *, source: str = "mcp") -> None:
        self.adapter = adapter
        self.meta = McpGatewayMeta(source=source, adapter_name=type(adapter).__name__)
        self._cached_tools: list[ToolDefinition] | None = None

    def list_tools(self) -> list[ToolDefinition]:
        if self._cached_tools is not None:
            return list(self._cached_tools)

        tools: list[ToolDefinition] = []
        for item in self.adapter.list_tools():
            # 兼容常见 MCP 工具描述字段：name/description/input_schema
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            description = str(item.get("description") or "")
            parameters = dict(item.get("input_schema") or item.get("parameters") or {
                "type": "object",
                "properties": {},
                "required": [],
            })

            # MCP 工具执行由 adapter.call_tool 统一处理，这里 executor 仅用于占位。
            tools.append(
                ToolDefinition(
                    name=name,
                    description=description,
                    parameters=parameters,
                    executor=lambda **_kwargs: {},
                )
            )

        self._cached_tools = tools
        return list(tools)

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> JsonObject:
        try:
            raw = dict(self.adapter.call_tool(name, dict(arguments)))
            # MCP 返回格式并不总是统一，这里做一次归一，避免上层分支泛滥。
            if "ok" in raw:
                result_payload = raw
            else:
                result_payload = {"ok": True, "result": raw}
            return {
                "tool_name": name,
                "ok": bool(result_payload.get("ok", True)),
                "arguments": dict(arguments),
                "result": result_payload.get("result", raw),
                "error": result_payload.get("error"),
                "source": self.meta.source,
            }
        except Exception as exc:
            return {
                "tool_name": name,
                "ok": False,
                "arguments": dict(arguments),
                "error": str(exc),
                "source": self.meta.source,
            }
