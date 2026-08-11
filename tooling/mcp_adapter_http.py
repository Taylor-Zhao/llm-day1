"""HTTP MCP adapter skeleton.

中文说明：
- 这是“真实 HTTP MCP 服务”的客户端骨架适配器。
- 它满足当前网关解析器约定：create_adapter() + list_tools() + call_tool()。
- 你可以把它直接替换 `tooling.mcp_adapter_example`，用于对接实际远端服务。

注意：
- MCP 在不同实现里，传输协议可能是 stdio / websocket / sse / http。
- 这里使用的是“REST 风格 HTTP 骨架”，用于快速落地与演示。
- 如果你的 MCP 服务是 JSON-RPC 或 SSE，只需要改这一个文件，不影响上层脚本。

环境变量：
1) MCP_HTTP_BASE_URL              必填，例如: http://127.0.0.1:8080
2) MCP_HTTP_API_KEY               可选，若有鉴权会放到 Authorization 头
3) MCP_HTTP_TIMEOUT_SECONDS       可选，默认 15
4) MCP_HTTP_TOOLS_PATH            可选，默认 /tools
5) MCP_HTTP_CALL_PATH             可选，默认 /tools/call

推荐搭配：
- TOOL_GATEWAY_MODE=auto
- MCP_GATEWAY_ADAPTER_MODULE=tooling.mcp_adapter_http
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import httpx


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class HttpMcpConfig:
    """HTTP MCP 客户端配置。"""

    base_url: str
    api_key: str
    timeout_seconds: float
    tools_path: str
    call_path: str


class HttpMcpAdapter:
    """基于 HTTP 的 MCP 适配器骨架。

    协议约定（可按你的服务改）：
    1) GET  {base_url}{tools_path}
       - 返回形式 A: {"tools": [...]}  # 推荐
       - 返回形式 B: [...]             # 兼容

    2) POST {base_url}{call_path}
       - 请求体: {"name": "tool_name", "arguments": {...}}
       - 返回形式 A: {"ok": true, "result": {...}}
       - 返回形式 B: {...}  # 将被视为 result 原始对象

    这样设计的好处：
    - 与当前 `McpToolGateway` 的归一化逻辑天然匹配；
    - 即使返回结构稍有差异，也能在这一层收敛。
    """

    def __init__(self, config: HttpMcpConfig) -> None:
        if not config.base_url.strip():
            raise RuntimeError("MCP_HTTP_BASE_URL is required")
        self.config = config

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        suffix = "/" + path.lstrip("/")
        return base + suffix

    def list_tools(self) -> list[Mapping[str, Any]]:
        """拉取 MCP 工具目录。

        返回值要求：列表中的每个元素应至少包含 name，建议包含 description/input_schema。
        """

        url = self._url(self.config.tools_path)
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.get(url, headers=self._headers())
            response.raise_for_status()
            payload = response.json()

        if isinstance(payload, dict):
            tools = payload.get("tools")
            if not isinstance(tools, list):
                raise RuntimeError("tools endpoint must return {'tools': [...]} or list")
            return tools

        if isinstance(payload, list):
            return payload

        raise RuntimeError("tools endpoint returned unexpected payload type")

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """调用 MCP 工具。

        - 失败时抛异常，让上层统一回退/审计；
        - 成功时返回 JSON 可序列化对象。
        """

        url = self._url(self.config.call_path)
        body: JsonObject = {"name": name, "arguments": dict(arguments)}
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.post(url, headers=self._headers(), json=body)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict):
            raise RuntimeError("call endpoint must return a JSON object")
        return payload


def _read_float_env(name: str, default_value: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default_value
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def create_adapter() -> HttpMcpAdapter:
    """网关解析器入口：创建 HTTP MCP 适配器。

    常见配置示例：
    export TOOL_GATEWAY_MODE=auto
    export MCP_GATEWAY_ADAPTER_MODULE=tooling.mcp_adapter_http
    export MCP_HTTP_BASE_URL=http://127.0.0.1:8080
    export MCP_HTTP_API_KEY=your-token
    """

    config = HttpMcpConfig(
        base_url=(os.getenv("MCP_HTTP_BASE_URL") or "").strip(),
        api_key=(os.getenv("MCP_HTTP_API_KEY") or "").strip(),
        timeout_seconds=_read_float_env("MCP_HTTP_TIMEOUT_SECONDS", 15.0),
        tools_path=(os.getenv("MCP_HTTP_TOOLS_PATH") or "/tools").strip(),
        call_path=(os.getenv("MCP_HTTP_CALL_PATH") or "/tools/call").strip(),
    )
    return HttpMcpAdapter(config)
