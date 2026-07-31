#!/usr/bin/env python3
"""LLM 工具生态的离线参考实现。

本模块用纯标准库演示 Function Calling、MCP、Skill、A2A、SSE 和 LLM Gateway
的关键控制逻辑。它不启动真实网络服务，也不宣称兼容官方协议的全部版本；生产项目
应优先采用官方 MCP/A2A SDK、成熟网关和经过审计的 JSON Schema 校验器。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


JsonObject = dict[str, Any]
ToolFunction = Callable[..., Any]
ModelProvider = Callable[[JsonObject], JsonObject]


class ToolError(RuntimeError):
    """工具发现、校验、授权或执行失败。"""


def _matches_type(value: Any, expected: str) -> bool:
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    python_type = type_map.get(expected)
    if python_type is None:
        raise ToolError(f"unsupported schema type: {expected}")
    if expected in {"number", "integer"} and isinstance(value, bool):
        return False
    return isinstance(value, python_type)


def validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    """校验教学子集：object、required、properties、type、enum 和额外字段。"""

    if schema.get("type", "object") != "object":
        raise ToolError("tool input schema must be an object")
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    missing = sorted(required - arguments.keys())
    if missing:
        raise ToolError(f"missing required arguments: {', '.join(missing)}")
    if schema.get("additionalProperties") is False:
        unknown = sorted(arguments.keys() - properties.keys())
        if unknown:
            raise ToolError(f"unknown arguments: {', '.join(unknown)}")
    for name, value in arguments.items():
        field_schema = properties.get(name)
        if field_schema is None:
            continue
        expected = field_schema.get("type")
        if expected and not _matches_type(value, expected):
            raise ToolError(f"argument {name!r} must be {expected}")
        allowed = field_schema.get("enum")
        if allowed is not None and value not in allowed:
            raise ToolError(f"argument {name!r} must be one of {allowed}")


@dataclass(frozen=True)
class ToolDefinition:
    """可发现的工具定义；dangerous 工具执行前必须获得显式批准。"""

    name: str
    description: str
    input_schema: JsonObject
    dangerous: bool = False

    def function_schema(self) -> JsonObject:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    tool_name: str
    ok: bool
    value: Any = None
    error: str | None = None


class ToolRuntime:
    """宿主侧工具白名单：模型只决策，运行时负责校验、授权、超时和执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolDefinition, ToolFunction]] = {}

    def register(self, definition: ToolDefinition, function: ToolFunction) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = (definition, function)

    def list_tools(self) -> list[ToolDefinition]:
        return [self._tools[name][0] for name in sorted(self._tools)]

    def execute(
        self,
        *,
        call_id: str,
        name: str,
        arguments: Mapping[str, Any],
        approved: bool = False,
        timeout_seconds: float = 5.0,
    ) -> ToolResult:
        entry = self._tools.get(name)
        if entry is None:
            return ToolResult(call_id, name, False, error="unknown tool")
        definition, function = entry
        pool: ThreadPoolExecutor | None = None
        try:
            validate_arguments(definition.input_schema, arguments)
            if definition.dangerous and not approved:
                raise ToolError("user approval required")
            pool = ThreadPoolExecutor(max_workers=1)
            future = pool.submit(function, **dict(arguments))
            value = future.result(timeout=timeout_seconds)
            pool.shutdown(wait=True)
            return ToolResult(call_id, name, True, value=value)
        except TimeoutError:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
            return ToolResult(call_id, name, False, error="tool execution timed out")
        except Exception as exc:
            if pool is not None:
                pool.shutdown(wait=True)
            return ToolResult(call_id, name, False, error=str(exc))

    def execute_many(
        self,
        calls: Sequence[Mapping[str, Any]],
        *,
        approved_tools: set[str] | None = None,
    ) -> list[ToolResult]:
        """并行执行互不依赖的调用，并按输入顺序返回结果。"""

        approved = approved_tools or set()
        with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
            futures = [
                pool.submit(
                    self.execute,
                    call_id=str(call["id"]),
                    name=str(call["name"]),
                    arguments=dict(call.get("arguments", {})),
                    approved=str(call["name"]) in approved,
                )
                for call in calls
            ]
            return [future.result() for future in futures]


class McpServer:
    """JSON-RPC 2.0 形状的内存 MCP Server 教学骨架。"""

    def __init__(self, name: str, tools: ToolRuntime) -> None:
        self.name = name
        self.tools = tools
        self.resources: dict[str, str] = {}
        self.prompts: dict[str, str] = {}

    def add_resource(self, uri: str, text: str) -> None:
        self.resources[uri] = text

    def add_prompt(self, name: str, template: str) -> None:
        self.prompts[name] = template

    def handle(self, request: Mapping[str, Any]) -> JsonObject:
        request_id = request.get("id")
        if request.get("jsonrpc") != "2.0":
            return self._error(request_id, -32600, "invalid JSON-RPC version")
        method = request.get("method")
        params = dict(request.get("params") or {})
        try:
            if method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.input_schema,
                        }
                        for tool in self.tools.list_tools()
                    ]
                }
            elif method == "tools/call":
                call_result = self.tools.execute(
                    call_id=str(request_id),
                    name=str(params.get("name", "")),
                    arguments=dict(params.get("arguments") or {}),
                    approved=bool(params.get("approved", False)),
                )
                result = asdict(call_result)
            elif method == "resources/list":
                result = {"resources": [{"uri": uri} for uri in sorted(self.resources)]}
            elif method == "resources/read":
                uri = str(params.get("uri", ""))
                if uri not in self.resources:
                    raise ToolError("resource not found")
                result = {"uri": uri, "text": self.resources[uri]}
            elif method == "prompts/list":
                result = {"prompts": [{"name": name} for name in sorted(self.prompts)]}
            elif method == "prompts/get":
                name = str(params.get("name", ""))
                if name not in self.prompts:
                    raise ToolError("prompt not found")
                result = {"name": name, "template": self.prompts[name]}
            else:
                return self._error(request_id, -32601, "method not found")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            return self._error(request_id, -32000, str(exc))

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> JsonObject:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }


class McpClient:
    """一条 Client-to-Server 连接，并将 MCP Tool 转为模型函数 schema。"""

    def __init__(self, server: McpServer) -> None:
        self.server = server
        self._next_id = 1

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> JsonObject:
        request_id = self._next_id
        self._next_id += 1
        response = self.server.handle(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})}
        )
        if "error" in response:
            raise ToolError(response["error"]["message"])
        return dict(response["result"])

    def discover_function_schemas(self) -> list[JsonObject]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["inputSchema"],
                },
            }
            for tool in self.request("tools/list")["tools"]
        ]


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    directory: Path


class SkillCatalog:
    """三段式 Skill 加载：扫描元数据、按名加载正文、按需读取资源。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._metadata: dict[str, SkillMetadata] = {}

    def scan(self) -> list[SkillMetadata]:
        discovered: dict[str, SkillMetadata] = {}
        for skill_file in sorted(self.root.glob("*/SKILL.md")):
            metadata, _body = self._parse(skill_file)
            if metadata.name in discovered:
                raise ValueError(f"duplicate skill: {metadata.name}")
            discovered[metadata.name] = metadata
        self._metadata = discovered
        return [discovered[name] for name in sorted(discovered)]

    def load_instructions(self, name: str) -> str:
        metadata = self._get(name)
        _metadata, body = self._parse(metadata.directory / "SKILL.md")
        return body

    def read_asset(self, name: str, relative_path: str) -> str:
        metadata = self._get(name)
        target = (metadata.directory / relative_path).resolve()
        if metadata.directory not in target.parents or not target.is_file():
            raise ValueError("skill asset path is outside the skill directory")
        return target.read_text(encoding="utf-8")

    def _get(self, name: str) -> SkillMetadata:
        if not self._metadata:
            self.scan()
        metadata = self._metadata.get(name)
        if metadata is None:
            raise KeyError(f"unknown skill: {name}")
        return metadata

    @staticmethod
    def _parse(path: Path) -> tuple[SkillMetadata, str]:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError(f"invalid skill frontmatter: {path}")
        header, body = text[4:].split("\n---\n", 1)
        fields: dict[str, str] = {}
        for line in header.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                fields[key.strip()] = value.strip().strip('"\'')
        if not fields.get("name") or not fields.get("description"):
            raise ValueError(f"skill requires name and description: {path}")
        return (
            SkillMetadata(fields["name"], fields["description"], path.parent.resolve()),
            body.strip(),
        )


@dataclass(frozen=True)
class AgentSkill:
    skill_id: str
    description: str
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentCard:
    name: str
    endpoint: str
    skills: tuple[AgentSkill, ...]
    streaming: bool = False
    push_notifications: bool = False


class TaskStatus(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class A2ATask:
    task_id: str
    agent_name: str
    skill_id: str
    message: str
    status: TaskStatus = TaskStatus.SUBMITTED
    artifacts: list[JsonObject] = field(default_factory=list)
    error: str | None = None


class AgentDirectory:
    """Agent Card 注册与按 Skill ID 发现。"""

    def __init__(self) -> None:
        self.cards: dict[str, AgentCard] = {}

    def register(self, card: AgentCard) -> None:
        if card.name in self.cards:
            raise ValueError(f"agent already registered: {card.name}")
        self.cards[card.name] = card

    def find_by_skill(self, skill_id: str) -> list[AgentCard]:
        return [
            card
            for card in self.cards.values()
            if any(skill.skill_id == skill_id for skill in card.skills)
        ]


class A2ATaskStore:
    """带明确状态迁移的内存 Task Store。"""

    def __init__(self, directory: AgentDirectory) -> None:
        self.directory = directory
        self.tasks: dict[str, A2ATask] = {}
        self.lock = threading.RLock()

    def submit(self, agent_name: str, skill_id: str, message: str) -> A2ATask:
        card = self.directory.cards.get(agent_name)
        if card is None or not any(skill.skill_id == skill_id for skill in card.skills):
            raise ValueError("agent does not advertise the requested skill")
        task = A2ATask(uuid.uuid4().hex, agent_name, skill_id, message)
        with self.lock:
            self.tasks[task.task_id] = task
        return task

    def start(self, task_id: str) -> A2ATask:
        return self._transition(task_id, TaskStatus.SUBMITTED, TaskStatus.WORKING)

    def complete(self, task_id: str, artifacts: Sequence[Mapping[str, Any]]) -> A2ATask:
        task = self._transition(task_id, TaskStatus.WORKING, TaskStatus.COMPLETED)
        task.artifacts = [dict(artifact) for artifact in artifacts]
        return task

    def fail(self, task_id: str, error: str) -> A2ATask:
        task = self._transition(task_id, TaskStatus.WORKING, TaskStatus.FAILED)
        task.error = error
        return task

    def _transition(self, task_id: str, expected: TaskStatus, target: TaskStatus) -> A2ATask:
        with self.lock:
            task = self.tasks[task_id]
            if task.status is not expected:
                raise ValueError(f"invalid task transition: {task.status} -> {target}")
            task.status = target
            return task


@dataclass(frozen=True)
class ServerEvent:
    event_id: int
    event: str
    data: JsonObject

    def encode(self) -> str:
        return (
            f"id: {self.event_id}\n"
            f"event: {self.event}\n"
            f"data: {json.dumps(self.data, ensure_ascii=False)}\n\n"
        )


class SseEventBuffer:
    """带 Last-Event-ID 重放语义的小型 SSE 事件缓冲。"""

    def __init__(self, max_events: int = 100) -> None:
        self.max_events = max_events
        self.events: list[ServerEvent] = []

    def publish(self, event: str, data: Mapping[str, Any]) -> ServerEvent:
        item = ServerEvent((self.events[-1].event_id if self.events else 0) + 1, event, dict(data))
        self.events.append(item)
        self.events = self.events[-self.max_events :]
        return item

    def replay_after(self, last_event_id: int) -> list[ServerEvent]:
        return [event for event in self.events if event.event_id > last_event_id]


def choose_transport(*, local: bool, bidirectional: bool, realtime_media: bool) -> str:
    """按通信需求选择本地 MCP、远程文本或实时媒体传输。"""

    if realtime_media:
        return "webrtc"
    if local:
        return "stdio"
    if bidirectional:
        return "websocket"
    return "streamable_http"


@dataclass(frozen=True)
class GatewayRoute:
    public_model: str
    providers: tuple[str, ...]
    cost_per_1k_tokens: float


@dataclass
class GatewayUsage:
    requests: int = 0
    tokens: int = 0
    cost: float = 0.0
    failures: int = 0


class LlmGateway:
    """统一路由、虚拟 Key 配额、故障转移和用量审计的离线网关骨架。"""

    def __init__(self, routes: Sequence[GatewayRoute], providers: Mapping[str, ModelProvider]) -> None:
        self.routes = {route.public_model: route for route in routes}
        self.providers = dict(providers)
        self.token_quotas: dict[str, int] = {}
        self.usage: dict[str, GatewayUsage] = {}

    def set_token_quota(self, virtual_key: str, tokens: int) -> None:
        if tokens <= 0:
            raise ValueError("quota must be positive")
        self.token_quotas[virtual_key] = tokens

    def complete(self, virtual_key: str, request: Mapping[str, Any]) -> JsonObject:
        model = str(request.get("model", ""))
        route = self.routes.get(model)
        if route is None:
            raise ValueError("model is not allowed by gateway")
        state = self.usage.setdefault(virtual_key, GatewayUsage())
        estimated_tokens = int(request.get("estimated_tokens", 0))
        quota = self.token_quotas.get(virtual_key)
        if quota is not None and state.tokens + estimated_tokens > quota:
            raise PermissionError("token quota exceeded")

        errors: list[str] = []
        for provider_name in route.providers:
            provider = self.providers.get(provider_name)
            if provider is None:
                errors.append(f"unknown provider: {provider_name}")
                continue
            try:
                response = dict(provider(dict(request)))
                actual_tokens = int(response.get("total_tokens", estimated_tokens))
                state.requests += 1
                state.tokens += actual_tokens
                state.cost += actual_tokens / 1000 * route.cost_per_1k_tokens
                response["gateway_provider"] = provider_name
                return response
            except Exception as exc:
                state.failures += 1
                errors.append(f"{provider_name}: {exc}")
        raise RuntimeError("all providers failed: " + "; ".join(errors))


def run_demo() -> None:
    runtime = ToolRuntime()
    runtime.register(
        ToolDefinition(
            "add",
            "计算两数之和",
            {
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
                "additionalProperties": False,
            },
        ),
        lambda a, b: a + b,
    )
    server = McpServer("calculator", runtime)
    client = McpClient(server)
    print("Discovered tools:", [item["function"]["name"] for item in client.discover_function_schemas()])
    print("Call result:", client.request("tools/call", {"name": "add", "arguments": {"a": 20, "b": 22}}))


if __name__ == "__main__":
    run_demo()