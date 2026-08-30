#!/usr/bin/env python3
"""LangChain 核心能力的离线参考实现。

本文件刻意不调用真实模型，方便在没有 API Key 的环境中观察以下机制：

1. Runnable/LCEL 如何串行、并行和分支组合。
2. Tool 如何把名称、描述、参数 Schema 与可执行函数绑定起来。
3. Agent loop 如何在模型决策与工具结果之间循环。
4. Checkpointer 与 Store 为什么分别对应线程内状态和跨线程记忆。
5. Deep Research 如何拆题、并行搜证、质量检查和统一写作。

项目当前使用 LangChain 0.2.x；网页中的 ``create_agent``、``ToolRuntime`` 和
middleware 属于 LangChain v1 主线。这里优先使用两个版本都能理解的核心协议，
并用小型本地实现补足运行时语义，避免为了教学偷偷发起网络请求。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableBranch, RunnableLambda, RunnableParallel
from langchain_core.tools import BaseTool, StructuredTool, tool
from pydantic import BaseModel, Field


def normalize_request(value: Mapping[str, Any]) -> dict[str, str]:
    """清理输入并建立后续 Runnable 共用的数据契约。"""

    question = " ".join(str(value.get("question", "")).split())
    if not question:
        raise ValueError("question cannot be empty")
    return {"question": question}


def classify_request(value: Mapping[str, str]) -> str:
    """使用确定性规则模拟分类节点，突出 Chain 的固定拓扑。"""

    question = value["question"].lower()
    if "订单" in question or "order" in question:
        return "order"
    if "知识库" in question or "文档" in question:
        return "knowledge"
    return "general"


def build_runnable_chain() -> Runnable[Mapping[str, Any], dict[str, str]]:
    """构造一个包含串行、并行与条件分支的真实 LCEL Runnable。"""

    normalize = RunnableLambda(normalize_request)
    enrich = RunnableParallel(
        question=RunnableLambda(lambda value: value["question"]),
        route=RunnableLambda(classify_request),
    )
    route = RunnableBranch(
        (
            lambda value: value["route"] == "order",
            RunnableLambda(
                lambda value: {
                    **value,
                    "answer": "这是订单问题，应先调用只读订单查询工具。",
                }
            ),
        ),
        (
            lambda value: value["route"] == "knowledge",
            RunnableLambda(
                lambda value: {
                    **value,
                    "answer": "这是知识问题，应先检索有权限的知识库。",
                }
            ),
        ),
        RunnableLambda(
            lambda value: {
                **value,
                "answer": "这是一般问题，可以直接回答或请求补充信息。",
            }
        ),
    )
    return normalize | enrich | route


class OrderQuery(BaseModel):
    """模型可见的订单工具参数 Schema。"""

    order_id: str = Field(min_length=1, description="要查询的订单号")
    detail: str = Field(default="summary", description="summary 或 full")


@tool("lookup_order", args_schema=OrderQuery)
def lookup_order(order_id: str, detail: str = "summary") -> dict[str, str]:
    """查询订单状态；该工具只读，不执行退款或修改操作。"""

    if detail not in {"summary", "full"}:
        raise ValueError("detail must be summary or full")
    order = {"order_id": order_id, "status": "shipped"}
    if detail == "full":
        order["carrier"] = "demo-express"
    return order


@dataclass(frozen=True)
class RuntimeContext:
    """由可信应用注入、不能让模型填写的身份信息。"""

    tenant_id: str
    user_id: str
    roles: frozenset[str] = frozenset()


def build_balance_tool(context: RuntimeContext) -> BaseTool:
    """用闭包演示可信运行时参数与模型参数的隔离。"""

    def get_balance(account_type: str) -> dict[str, Any]:
        """查询当前登录用户的账户余额。"""

        if "balance:read" not in context.roles:
            raise PermissionError("missing balance:read permission")
        return {
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "account_type": account_type,
            "balance": 100,
        }

    return StructuredTool.from_function(
        func=get_balance,
        name="get_balance",
        description="查询当前登录用户的账户余额；用户身份由服务端注入。",
    )


class ToolRegistry:
    """保存模型可见工具契约，并在宿主侧执行真实函数。"""

    def __init__(self, tools: Sequence[BaseTool]) -> None:
        self._tools = {item.name: item for item in tools}
        if len(self._tools) != len(tools):
            raise ValueError("tool names must be unique")

    def schemas(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in self._tools.values():
            schema_model = item.args_schema
            if schema_model is None:
                schema: dict[str, Any] = {"type": "object", "properties": {}}
            elif hasattr(schema_model, "model_json_schema"):
                schema = schema_model.model_json_schema()
            else:
                schema = schema_model.schema()
            result.append(
                {
                    "name": item.name,
                    "description": item.description,
                    "parameters": schema,
                }
            )
        return result

    def execute(self, name: str, arguments: Mapping[str, Any]) -> Any:
        try:
            selected = self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc
        return selected.invoke(dict(arguments))


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentDecision:
    tool_calls: tuple[ToolCall, ...] = ()
    final_answer: str | None = None


class Planner(Protocol):
    """真实模型和离线规则模型都可以遵守的决策接口。"""

    def decide(
        self,
        messages: Sequence[BaseMessage],
        tool_schemas: Sequence[Mapping[str, Any]],
    ) -> AgentDecision: ...


class RuleBasedPlanner:
    """离线替身：模拟模型先发 tool call，再读取 ToolMessage。"""

    ORDER_PATTERN = re.compile(r"(?:订单|order)\s*[-：:]?\s*([A-Za-z0-9-]+)", re.IGNORECASE)

    def decide(
        self,
        messages: Sequence[BaseMessage],
        tool_schemas: Sequence[Mapping[str, Any]],
    ) -> AgentDecision:
        del tool_schemas
        latest = messages[-1]
        if isinstance(latest, ToolMessage):
            payload = json.loads(str(latest.content))
            return AgentDecision(
                final_answer=f"订单 {payload['order_id']} 当前状态是 {payload['status']}。"
            )

        question = str(latest.content)
        match = self.ORDER_PATTERN.search(question)
        if match:
            return AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id=str(uuid.uuid4()),
                        name="lookup_order",
                        arguments={"order_id": match.group(1), "detail": "summary"},
                    ),
                )
            )
        return AgentDecision(final_answer="这个问题不需要调用订单工具。")


@dataclass
class AgentRun:
    answer: str
    messages: list[BaseMessage]
    tool_results: list[dict[str, Any]] = field(default_factory=list)


class AgentLoop:
    """展示模型决策、宿主执行和 ToolMessage 回灌的最小闭环。"""

    def __init__(self, planner: Planner, tools: ToolRegistry, max_rounds: int = 4) -> None:
        if max_rounds <= 0:
            raise ValueError("max_rounds must be positive")
        self._planner = planner
        self._tools = tools
        self._max_rounds = max_rounds

    def run(self, question: str) -> AgentRun:
        messages: list[BaseMessage] = [HumanMessage(content=question)]
        tool_results: list[dict[str, Any]] = []

        for _ in range(self._max_rounds):
            decision = self._planner.decide(messages, self._tools.schemas())
            if decision.final_answer is not None:
                messages.append(AIMessage(content=decision.final_answer))
                return AgentRun(decision.final_answer, messages, tool_results)
            if not decision.tool_calls:
                raise RuntimeError("planner returned neither tool calls nor a final answer")

            ai_tool_calls = [
                {
                    "id": call.call_id,
                    "name": call.name,
                    "args": call.arguments,
                    "type": "tool_call",
                }
                for call in decision.tool_calls
            ]
            messages.append(AIMessage(content="", tool_calls=ai_tool_calls))

            for call in decision.tool_calls:
                result = self._tools.execute(call.name, call.arguments)
                record = {"call_id": call.call_id, "name": call.name, "result": result}
                tool_results.append(record)
                messages.append(
                    ToolMessage(
                        content=json.dumps(result, ensure_ascii=False),
                        tool_call_id=call.call_id,
                    )
                )

        raise RuntimeError("agent exceeded max_rounds")


class InMemoryCheckpointer:
    """按 thread_id 保存线程内消息快照。"""

    def __init__(self) -> None:
        self._states: dict[str, list[dict[str, str]]] = {}
        self._lock = threading.Lock()

    def save(self, thread_id: str, messages: Sequence[Mapping[str, str]]) -> None:
        if not thread_id:
            raise ValueError("thread_id cannot be empty")
        with self._lock:
            self._states[thread_id] = [dict(item) for item in messages]

    def load(self, thread_id: str) -> list[dict[str, str]]:
        with self._lock:
            return [dict(item) for item in self._states.get(thread_id, [])]


class LongTermStore:
    """使用 namespace + key 隔离跨线程长期记忆。"""

    def __init__(self) -> None:
        self._items: dict[tuple[str, ...], dict[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def put(self, namespace: tuple[str, ...], key: str, value: Mapping[str, Any]) -> None:
        if not namespace or not key:
            raise ValueError("namespace and key cannot be empty")
        with self._lock:
            self._items.setdefault(namespace, {})[key] = dict(value)

    def get(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._items.get(namespace, {}).get(key)
            return dict(value) if value is not None else None


@dataclass(frozen=True)
class Evidence:
    topic: str
    claim: str
    source: str
    confidence: float


@dataclass(frozen=True)
class ResearchReport:
    question: str
    topics: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    gaps: tuple[str, ...]
    report: str


class DeepResearchEngine:
    """用 Map-Reduce 形状实现可控的离线 Deep Research 流程。"""

    def __init__(
        self,
        planner: Callable[[str], Sequence[str]],
        researcher: Callable[[str], Sequence[Evidence]],
        min_confidence: float = 0.6,
        max_workers: int = 4,
    ) -> None:
        self._planner = planner
        self._researcher = researcher
        self._min_confidence = min_confidence
        self._max_workers = max_workers

    def run(self, question: str) -> ResearchReport:
        topics = tuple(dict.fromkeys(self._planner(question)))
        if not topics:
            raise ValueError("planner must return at least one topic")

        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(topics))) as pool:
            batches = list(pool.map(self._researcher, topics))

        evidence = tuple(item for batch in batches for item in batch)
        covered = {
            item.topic
            for item in evidence
            if item.confidence >= self._min_confidence and item.source
        }
        gaps = tuple(topic for topic in topics if topic not in covered)
        accepted = tuple(item for item in evidence if item.confidence >= self._min_confidence)
        report = self._write_report(question, accepted, gaps)
        return ResearchReport(question, topics, accepted, gaps, report)

    @staticmethod
    def _write_report(
        question: str,
        evidence: Sequence[Evidence],
        gaps: Sequence[str],
    ) -> str:
        lines = [f"# {question}", ""]
        for item in evidence:
            lines.append(f"- [{item.topic}] {item.claim}（来源：{item.source}）")
        if gaps:
            lines.extend(["", "## 证据缺口", *[f"- {item}" for item in gaps]])
        return "\n".join(lines)


def run_demo() -> dict[str, Any]:
    """运行无需网络的最小演示，返回便于测试和观察的结构。"""

    chain_result = build_runnable_chain().invoke({"question": "查询订单 A100"})
    agent_result = AgentLoop(RuleBasedPlanner(), ToolRegistry([lookup_order])).run("订单 A100 到哪了？")

    evidence_by_topic = {
        "架构": [Evidence("架构", "LangChain Agent 运行在 LangGraph 上", "official-docs", 0.9)],
        "成本": [Evidence("成本", "并行会增加调用预算", "benchmark", 0.8)],
    }
    research = DeepResearchEngine(
        planner=lambda _question: ["架构", "成本"],
        researcher=lambda topic: evidence_by_topic.get(topic, []),
    ).run("如何设计研究型 Agent？")

    return {
        "chain": chain_result,
        "agent_answer": agent_result.answer,
        "tool_results": agent_result.tool_results,
        "research_report": research.report,
    }


if __name__ == "__main__":
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))