"""Example MCP adapter for local development.

中文说明：
- 这是一个“可直接运行”的 MCP 适配器示例，不依赖外部 MCP SDK。
- 目的不是替代真实 MCP 服务，而是让你先打通第三步链路：
  TOOL_GATEWAY_MODE + MCP_GATEWAY_ADAPTER_MODULE -> resolve_gateway -> McpToolGateway。
- 你后续接真实 MCP 时，只需保留 create_adapter() 入口和两个方法签名：
  list_tools()、call_tool()。

快速使用：
1) export TOOL_GATEWAY_MODE=auto
2) export MCP_GATEWAY_ADAPTER_MODULE=tooling.mcp_adapter_example
3) 运行 Day22/Day25/Day25-28 脚本，日志会显示已选中 MCP。
"""

from __future__ import annotations

import json
from typing import Any, Mapping


class ExampleMcpAdapter:
    """本地假实现：模拟 MCP 工具目录与工具调用。

    设计说明：
    - list_tools() 返回 MCP 常见字段：name/description/input_schema；
    - call_tool() 模拟工具执行结果，返回 JSON 兼容对象；
    - 字段尽量贴近真实协议，方便后续无缝替换。
    """

    def __init__(self) -> None:
        self._tools = [
            {
                "name": "list_mock_endpoints",
                "description": "列出可联调的 mock endpoint。",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "http_get",
                "description": "模拟 GET 请求，返回回显结果。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "http_post",
                "description": "模拟 POST 请求，返回回显结果。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "json_body": {"type": "object"},
                    },
                    "required": ["url"],
                },
            },
        ]

    def list_tools(self) -> list[Mapping[str, Any]]:
        """返回 MCP 工具元数据。

        这里直接返回静态配置，真实场景通常来自远端 MCP server。
        """

        return list(self._tools)

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """模拟 MCP 工具调用。

        返回约定：
        - 成功: {"ok": True, "result": {...}}
        - 失败: {"ok": False, "error": "..."}

        McpToolGateway 会把该结果进一步归一化成统一结构。
        """

        args = dict(arguments)

        if name == "list_mock_endpoints":
            return {
                "ok": True,
                "result": {
                    "endpoints": [
                        {
                            "name": "echo_get",
                            "method": "GET",
                            "url": "https://httpbin.org/get",
                            "description": "回显 query 参数",
                        },
                        {
                            "name": "echo_post",
                            "method": "POST",
                            "url": "https://httpbin.org/post",
                            "description": "回显 JSON body",
                        },
                    ]
                },
            }

        if name == "http_get":
            url = str(args.get("url") or "").strip()
            if not url:
                return {"ok": False, "error": "missing required field: url"}
            return {
                "ok": True,
                "result": {
                    "url": url,
                    "status_code": 200,
                    "content_type": "application/json",
                    "body": json.dumps(
                        {
                            "source": "example_mcp",
                            "method": "GET",
                            "params": args.get("params") or {},
                        },
                        ensure_ascii=False,
                    ),
                },
            }

        if name == "http_post":
            url = str(args.get("url") or "").strip()
            if not url:
                return {"ok": False, "error": "missing required field: url"}
            return {
                "ok": True,
                "result": {
                    "url": url,
                    "status_code": 200,
                    "content_type": "application/json",
                    "body": json.dumps(
                        {
                            "source": "example_mcp",
                            "method": "POST",
                            "json_body": args.get("json_body") or {},
                        },
                        ensure_ascii=False,
                    ),
                },
            }

        return {"ok": False, "error": f"unknown tool: {name}"}


def create_adapter() -> ExampleMcpAdapter:
    """网关解析器约定入口。

    resolve_gateway() 会动态 import 该模块并调用 create_adapter()。
    """

    return ExampleMcpAdapter()
