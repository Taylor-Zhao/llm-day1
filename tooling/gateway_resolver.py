"""Resolve runtime tool gateway with MCP fallback.

中文说明：
- 这个文件对应你说的“第三步”：MCP 可用时走 MCP，不可用自动降级本地工具。
- 降级策略由 TOOL_GATEWAY_MODE 控制：
  1) local: 强制本地
  2) mcp: 强制 MCP（失败则报错）
  3) auto: 优先 MCP，失败自动回退 local（默认）
- 为了不绑定某个 MCP SDK，这里通过“适配器模块”动态加载：
  环境变量 MCP_GATEWAY_ADAPTER_MODULE 指向一个可 import 的 Python 模块，
  且模块需要提供 create_adapter() 函数。
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any

from .gateway import ToolGateway
from .mcp_gateway import McpAdapterProtocol, McpToolGateway


@dataclass(frozen=True)
class GatewaySelection:
    """网关选择结果。

    字段说明：
    - mode: 最终生效模式（local/mcp）；
    - source: 选择来源（local/manual/mcp/auto-fallback）；
    - message: 给日志或报告看的可读解释。
    """

    gateway: ToolGateway
    mode: str
    source: str
    message: str


def _load_mcp_adapter(module_name: str) -> McpAdapterProtocol:
    """动态加载 MCP 适配器模块。

    适配器模块约定：
    - 提供 create_adapter() -> McpAdapterProtocol
    """

    module = importlib.import_module(module_name)
    factory = getattr(module, "create_adapter", None)
    if factory is None or not callable(factory):
        raise RuntimeError(
            f"MCP adapter module '{module_name}' must expose callable create_adapter()"
        )
    adapter = factory()
    # 这里不做严格 isinstance 校验，采用鸭子类型，降低耦合。
    if not hasattr(adapter, "list_tools") or not hasattr(adapter, "call_tool"):
        raise RuntimeError(
            f"MCP adapter from '{module_name}' must provide list_tools() and call_tool()"
        )
    return adapter


def resolve_gateway(local_gateway: ToolGateway) -> GatewaySelection:
    """根据环境变量解析最终网关。

    环境变量：
    - TOOL_GATEWAY_MODE: local | mcp | auto（默认 auto）
    - MCP_GATEWAY_ADAPTER_MODULE: MCP 适配器模块名

    典型用法：
    1) 本地开发（默认降级）：
       TOOL_GATEWAY_MODE=auto
       # 不配 adapter 也能跑，自动回退 local

    2) 强制 MCP：
       TOOL_GATEWAY_MODE=mcp
       MCP_GATEWAY_ADAPTER_MODULE=my_project.mcp_adapter

    3) 强制本地：
       TOOL_GATEWAY_MODE=local
    """

    mode = (os.getenv("TOOL_GATEWAY_MODE") or "auto").strip().lower()
    adapter_module = (os.getenv("MCP_GATEWAY_ADAPTER_MODULE") or "").strip()

    if mode not in {"local", "mcp", "auto"}:
        raise RuntimeError("TOOL_GATEWAY_MODE must be one of: local, mcp, auto")

    if mode == "local":
        return GatewaySelection(
            gateway=local_gateway,
            mode="local",
            source="manual",
            message="TOOL_GATEWAY_MODE=local，强制使用本地网关。",
        )

    if not adapter_module:
        if mode == "mcp":
            raise RuntimeError(
                "TOOL_GATEWAY_MODE=mcp but MCP_GATEWAY_ADAPTER_MODULE is empty"
            )
        return GatewaySelection(
            gateway=local_gateway,
            mode="local",
            source="auto-fallback",
            message="未配置 MCP 适配器，auto 模式自动回退本地网关。",
        )

    try:
        adapter = _load_mcp_adapter(adapter_module)
        mcp_gateway = McpToolGateway(adapter)

        # 在解析阶段做一次轻量探测，避免“适配器可加载但 MCP 端点不可用”时
        # 误判为已切到 MCP。这样 auto 模式能更早回退，用户体验更稳定。
        _ = mcp_gateway.list_tools()

        return GatewaySelection(
            gateway=mcp_gateway,
            mode="mcp",
            source="mcp",
            message=f"已加载 MCP 适配器：{adapter_module}",
        )
    except Exception as exc:
        if mode == "mcp":
            raise
        return GatewaySelection(
            gateway=local_gateway,
            mode="local",
            source="auto-fallback",
            message=f"MCP 适配器加载失败，已回退本地网关：{exc}",
        )
