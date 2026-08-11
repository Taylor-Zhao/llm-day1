#!/usr/bin/env python3
"""Day 25-28 LangChain demo: planning, tool calling, retries, audit, and summary.

中文说明：
- 这个脚本是一个最小但完整的 LangChain Agent Demo。
- 核心思路是：先让模型生成结构化计划，再把计划映射成工具调用，
    最后把工具结果回填给模型生成总结。
- 这里没有直接使用复杂的 AgentExecutor，而是用更容易读懂的方式拆成
    `build_plan()`、`execute_step()`、`summarize_once()` 三段。

LangChain 在这里的典型用法：
1. `ChatOpenAI`：连接 OpenAI-compatible 模型服务。
2. `with_structured_output()`：把模型输出约束成 Pydantic 结构。
3. `@tool`：把普通 Python 函数注册成可调用工具。
4. `BaseCallbackHandler`：在链路、LLM、工具执行时做审计记录。
5. `retry_call()`：在 LangChain 外围增加重试、退避和容错。

简单例子：
- 如果用户问“先看有哪些 endpoint，再查 order_id=1001”，`build_plan()` 会
    让模型先产出一个 `Plan`，例如包含 `list_mock_endpoints` 和 `http_get`。
- 如果模型返回空计划或格式不完整，脚本会回退到 `build_default_plan()`。
- 如果工具请求超时，`retry_call()` 会自动重试，而不是直接让 demo 中断。
"""

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from chat_cli import build_client, utc_now_iso
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from tooling import LocalToolGateway, ToolDefinition, resolve_gateway

PROJECT_DIR = Path(__file__).resolve().parent
ALLOWED_HTTP_HOSTS = {"httpbin.org"}
CURRENT_HTTP_TIMEOUT = 15.0

DEFAULT_QUESTION = "请完成接口联调：先识别可用 endpoint，再请求 order_id=1001，最后总结联调结果"

MOCK_ENDPOINTS = [
    {
        "name": "echo_get",
        "method": "GET",
        "url": "https://httpbin.org/get",
        "description": "回显 query 参数，适合联调查询型接口。",
    },
    {
        "name": "echo_post",
        "method": "POST",
        "url": "https://httpbin.org/post",
        "description": "回显 JSON body，适合联调提交型接口。",
    },
    {
        "name": "status_check",
        "method": "GET",
        "url": "https://httpbin.org/status/200",
        "description": "用于联调状态码检查。",
    },
]


class PlanStep(BaseModel):
    # LangChain 的 structured output 最常见做法之一，就是先把输出定义成 Pydantic 模型。
    # 这样模型不再随意输出 Markdown，而是输出可校验的 JSON 结构。
    step: str = Field(..., description="步骤名称")
    tool: str = Field(..., description="list_mock_endpoints, http_get 或 http_post")
    args: Dict[str, Any] = Field(default_factory=dict, description="工具参数")
    why: str = Field(..., description="该步骤目的")


class Plan(BaseModel):
    # Plan 代表一个完整的执行计划；LangChain 在这里负责“生成 plan”，而不是直接执行。
    plan: List[PlanStep] = Field(default_factory=list)


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day25-28 LangChain demo")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="联调目标问题")
    parser.add_argument(
        "--prompt",
        default="prompts/day25_day28_langchain_demo_cn.txt",
        help="LangChain 提示词路径",
    )
    parser.add_argument("--temperature", type=float, default=0.0, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=700, help="输出 token 上限")
    parser.add_argument("--max-steps", type=int, default=6, help="最大执行步骤数")
    parser.add_argument("--max-attempts", type=int, default=3, help="LLM/工具最大重试次数")
    parser.add_argument("--retry-backoff-seconds", type=float, default=0.5, help="重试退避秒数")
    parser.add_argument("--timeout-seconds", type=float, default=15.0, help="HTTP 请求超时")
    parser.add_argument("--model", default="", help="覆盖环境变量中的模型名")
    parser.add_argument(
        "--report",
        default="experiments/day25_day28_langchain_demo.md",
        help="Markdown 报告",
    )
    parser.add_argument(
        "--jsonl",
        default="logs/day25_day28_langchain_demo.jsonl",
        help="执行日志 JSONL",
    )
    parser.add_argument(
        "--audit",
        default="logs/day25_day28_langchain_demo.audit.jsonl",
        help="审计日志 JSONL",
    )
    return parser.parse_args()


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_audit_event(path: Path, trace_id: str, phase: str, event: str, **fields: Any) -> None:
    record = {
        "timestamp_utc": utc_now_iso(),
        "trace_id": trace_id,
        "phase": phase,
        "event": event,
    }
    record.update(fields)
    append_jsonl(path, record)


def ensure_allowed_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("only https urls are allowed")
    if parsed.netloc not in ALLOWED_HTTP_HOSTS:
        raise ValueError(f"host not allowed: {parsed.netloc}")


def trim_text(text: str, max_len: int = 1200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...<truncated>"


@tool("list_mock_endpoints")
def list_mock_endpoints() -> str:
    """List available mock endpoints for integration testing."""
    # `@tool` 会把这个普通函数暴露给 LangChain，模型可以通过 tool call 调用它。
    # 例子：模型如果判断“先看看有哪些可用接口”，就会选择这个工具。
    return json.dumps({"endpoints": MOCK_ENDPOINTS}, ensure_ascii=False)


@tool("http_get")
def http_get(url: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Perform a safe HTTPS GET request to an allowlisted host."""
    # 工具函数仍然是普通 Python 代码，只是被 LangChain 统一包装成 tool。
    # 这样做的好处是：模型只负责“决定调用什么”，真正的执行逻辑仍然由我们控制。
    ensure_allowed_url(url)
    with httpx.Client(timeout=CURRENT_HTTP_TIMEOUT) as client:
        resp = client.get(url, params=params or {})
        content_type = resp.headers.get("content-type", "")
        try:
            body_obj = resp.json()
            body_text = json.dumps(body_obj, ensure_ascii=False)
        except Exception:
            body_text = resp.text
        payload = {
            "url": str(resp.url),
            "status_code": resp.status_code,
            "content_type": content_type,
            "body": trim_text(body_text),
        }
        return json.dumps(payload, ensure_ascii=False)


@tool("http_post")
def http_post(url: str, json_body: Optional[Dict[str, Any]] = None) -> str:
    """Perform a safe HTTPS POST request to an allowlisted host."""
    # 这里的 allowlist 和超时控制属于“工具安全边界”，避免模型乱发请求。
    ensure_allowed_url(url)
    with httpx.Client(timeout=CURRENT_HTTP_TIMEOUT) as client:
        resp = client.post(url, json=json_body or {})
        content_type = resp.headers.get("content-type", "")
        try:
            body_obj = resp.json()
            body_text = json.dumps(body_obj, ensure_ascii=False)
        except Exception:
            body_text = resp.text
        payload = {
            "url": str(resp.url),
            "status_code": resp.status_code,
            "content_type": content_type,
            "body": trim_text(body_text),
        }
        return json.dumps(payload, ensure_ascii=False)


TOOLS = [list_mock_endpoints, http_get, http_post]


def _gateway_list_mock_endpoints() -> dict[str, Any]:
    return {"endpoints": MOCK_ENDPOINTS}


def _gateway_http_get(url: str, params: Optional[Dict[str, Any]] = None) -> dict[str, Any]:
    ensure_allowed_url(url)
    with httpx.Client(timeout=CURRENT_HTTP_TIMEOUT) as client:
        resp = client.get(url, params=params or {})
        content_type = resp.headers.get("content-type", "")
        try:
            body_obj = resp.json()
            body_text = json.dumps(body_obj, ensure_ascii=False)
        except Exception:
            body_text = resp.text
        return {
            "url": str(resp.url),
            "status_code": resp.status_code,
            "content_type": content_type,
            "body": trim_text(body_text),
        }


def _gateway_http_post(url: str, json_body: Optional[Dict[str, Any]] = None) -> dict[str, Any]:
    ensure_allowed_url(url)
    with httpx.Client(timeout=CURRENT_HTTP_TIMEOUT) as client:
        resp = client.post(url, json=json_body or {})
        content_type = resp.headers.get("content-type", "")
        try:
            body_obj = resp.json()
            body_text = json.dumps(body_obj, ensure_ascii=False)
        except Exception:
            body_text = resp.text
        return {
            "url": str(resp.url),
            "status_code": resp.status_code,
            "content_type": content_type,
            "body": trim_text(body_text),
        }


def build_tool_gateway() -> LocalToolGateway:
    """Build local gateway; later this can be swapped with an MCP gateway."""

    return LocalToolGateway(
        [
            ToolDefinition(
                name="list_mock_endpoints",
                description="列出可联调的 mock endpoint。",
                parameters={"type": "object", "properties": {}, "required": []},
                executor=lambda: _gateway_list_mock_endpoints(),
            ),
            ToolDefinition(
                name="http_get",
                description="执行白名单 HTTPS GET 请求。",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["url"],
                },
                executor=_gateway_http_get,
            ),
            ToolDefinition(
                name="http_post",
                description="执行白名单 HTTPS POST 请求。",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "json_body": {"type": "object"},
                    },
                    "required": ["url"],
                },
                executor=_gateway_http_post,
            ),
        ]
    )


class AuditCallbackHandler(BaseCallbackHandler):
    # LangChain 的 callback 机制适合做“可观测性”层。
    # 这里我们把 chain / llm / tool 的关键事件写入 audit 日志，便于回放和排障。
    def __init__(self, audit_path: Path, trace_id: str):
        self.audit_path = audit_path
        self.trace_id = trace_id

    def _summarize_value(self, value: Any) -> dict[str, Any]:
        # 不同 callback 的 payload 形态可能不同，这里统一压缩成可写日志的摘要。
        summary: dict[str, Any] = {"value_type": type(value).__name__}
        if isinstance(value, dict):
            summary["keys"] = list(value.keys())
        elif isinstance(value, list):
            summary["length"] = len(value)
        else:
            summary["preview"] = trim_text(str(value), 300)
        return summary

    def on_chain_start(self, serialized: dict, inputs: dict, **kwargs: Any) -> None:
        append_audit_event(
            self.audit_path,
            self.trace_id,
            "chain",
            "chain_start",
            name=(serialized or {}).get("name") if isinstance(serialized, dict) else None,
            **self._summarize_value(inputs),
        )

    def on_chain_end(self, outputs: dict, **kwargs: Any) -> None:
        if hasattr(outputs, "model_dump"):
            payload = outputs.model_dump()
            append_audit_event(
                self.audit_path,
                self.trace_id,
                "chain",
                "chain_end",
                output_type=type(outputs).__name__,
                keys=list(payload.keys()),
            )
            return
        append_audit_event(self.audit_path, self.trace_id, "chain", "chain_end", **self._summarize_value(outputs))

    def on_llm_start(self, serialized: dict, prompts: List[str], **kwargs: Any) -> None:
        name = serialized.get("name") if isinstance(serialized, dict) else None
        append_audit_event(self.audit_path, self.trace_id, "llm", "llm_start", name=name, prompt_count=len(prompts))

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        append_audit_event(self.audit_path, self.trace_id, "llm", "llm_end", llm_output=getattr(response, "llm_output", None))

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        append_audit_event(self.audit_path, self.trace_id, "llm", "llm_error", error=str(error))

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        name = serialized.get("name") if isinstance(serialized, dict) else None
        append_audit_event(self.audit_path, self.trace_id, "tool", "tool_start", name=name, input=trim_text(str(input_str), 300))

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        append_audit_event(self.audit_path, self.trace_id, "tool", "tool_end", output=trim_text(output, 300))

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        append_audit_event(self.audit_path, self.trace_id, "tool", "tool_error", error=str(error))


def build_llm(args: argparse.Namespace) -> ChatOpenAI:
    # LangChain 的核心入口之一：把模型服务包装成 `ChatOpenAI`。
    # 这里使用的是 OpenAI-compatible 接口，所以既可以接官方 OpenAI，也可以接本地网关。
    # 例如本地 Ollama：OPENAI_BASE_URL=http://127.0.0.1:11434/v1, OPENAI_API_KEY=ollama
    api_key, base_url, _ = build_client()
    return ChatOpenAI(
        model=args.model,
        api_key=api_key,
        base_url=base_url,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )


def normalize_step_args(step: PlanStep) -> dict[str, Any]:
    # 这是一个“工具参数修正层”：模型有时会忘记给 url，或者把参数写得不完整。
    # 我们在执行前补齐参数，让 demo 更稳。
    tool_name = step.tool.strip()
    args = dict(step.args or {})
    if tool_name == "http_get":
        if "url" not in args:
            args = {"url": "https://httpbin.org/get", "params": args}
        elif "params" not in args:
            args = {"url": args.get("url"), "params": {k: v for k, v in args.items() if k != "url"}}
    if tool_name == "http_post":
        if "url" not in args:
            args = {"url": "https://httpbin.org/post", "json_body": args}
        elif "json_body" not in args:
            args = {"url": args.get("url"), "json_body": {k: v for k, v in args.items() if k != "url"}}
    return args


def build_default_plan(question: str) -> Plan:
    # 如果 structured output 返回空计划，就用这个确定性的默认计划兜底。
    # 这样即使小模型规划质量不稳定，demo 也能继续跑。
    return Plan(
        plan=[
            PlanStep(
                step="识别可用 endpoint",
                tool="list_mock_endpoints",
                args={},
                why="首先需要确认有哪些可联调的 endpoint。",
            ),
            PlanStep(
                step="请求 order_id=1001",
                tool="http_get",
                args={"order_id": 1001},
                why="使用 order_id=1001 读取查询接口结果。",
            ),
            PlanStep(
                step="总结联调结果",
                tool="http_post",
                args={},
                why="调用 POST 工具收束最终联调结论。",
            ),
        ]
    )


def is_retryable_error(error: Exception) -> bool:
    lowered = str(error).lower()
    markers = ["timed out", "timeout", "connection error", "connection reset", "502", "503", "504"]
    return any(marker in lowered for marker in markers)


def retry_call(operation_name: str, trace_id: str, audit_path: Path, max_attempts: int, backoff_seconds: float, fn: Any) -> Any:
    # LangChain 本身不强制替你做“业务级重试”，所以这里显式包一层。
    # 适用场景：LLM 调用超时、HTTP 请求超时、5xx 暂时性错误。
    # 示例：第 1 次调用 http_get 超时，则等待 0.5s、1.0s 再重试。
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        start = time.perf_counter()
        append_audit_event(audit_path, trace_id, operation_name, "attempt_started", attempt=attempt, max_attempts=max_attempts)
        try:
            value = fn()
            latency_ms = int((time.perf_counter() - start) * 1000)
            append_audit_event(audit_path, trace_id, operation_name, "attempt_succeeded", attempt=attempt, latency_ms=latency_ms)
            return value
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            last_error = exc
            append_audit_event(audit_path, trace_id, operation_name, "attempt_failed", attempt=attempt, latency_ms=latency_ms, error=str(exc))
            if attempt < max_attempts and is_retryable_error(exc):
                append_audit_event(audit_path, trace_id, operation_name, "retry_scheduled", attempt=attempt, next_attempt=attempt + 1, backoff_seconds=backoff_seconds * attempt)
                time.sleep(backoff_seconds * attempt)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError(operation_name)


def build_plan(llm: ChatOpenAI, prompt_path: Path, question: str, trace_id: str, audit_path: Path, max_attempts: int, backoff_seconds: float) -> tuple[Plan, str]:
    # 这里使用 `with_structured_output(Plan)` 是 LangChain 很典型的“结构化输出”用法。
    # 它的目标是让模型返回符合 Plan schema 的内容，而不是自由发挥的自然语言。
    # 示例：
    # - 输入："请先识别 endpoint，再请求订单"
    # - 输出：{"plan": [{"step": "识别可用 endpoint", ...}, ...]}
    system_prompt = prompt_path.read_text(encoding="utf-8").strip()
    planner = llm.with_structured_output(Plan)

    def _invoke() -> Plan:
        return planner.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"请根据用户目标生成计划：{question}"),
            ],
            config={"callbacks": [AuditCallbackHandler(audit_path, trace_id)]},
        )

    try:
        plan = retry_call("plan_generation", trace_id, audit_path, max_attempts, backoff_seconds, _invoke)
        if not plan.plan:
            append_audit_event(audit_path, trace_id, "analysis", "plan_empty_fallback_used", reason="empty plan from model")
            return build_default_plan(question), "fallback"
        return plan, "langchain"
    except Exception as exc:
        append_audit_event(audit_path, trace_id, "analysis", "plan_fallback_used", error=str(exc))
        return build_default_plan(question), "fallback"


def execute_step(
    gateway: LocalToolGateway,
    step: PlanStep,
    trace_id: str,
    audit_path: Path,
    max_attempts: int,
    backoff_seconds: float,
) -> dict:
    # LangChain 的 Agent 思想在这里体现得很清楚：
    # 模型决定“调用哪个工具”，脚本决定“如何安全执行工具”。
    args = normalize_step_args(step)

    def _invoke() -> dict[str, Any]:
        return gateway.call_tool(step.tool, args)

    try:
        gateway_source = type(gateway).__name__
        append_audit_event(audit_path, trace_id, "tool", "tool_call_started", tool_name=step.tool, source=gateway_source)
        result_payload = retry_call(f"tool:{step.tool}", trace_id, audit_path, max_attempts, backoff_seconds, _invoke)
        append_audit_event(
            audit_path,
            trace_id,
            "tool",
            "tool_call_completed",
            tool_name=step.tool,
            ok=bool(result_payload.get("ok")),
            source=gateway_source,
        )
        return result_payload
    except Exception as exc:
        append_audit_event(
            audit_path,
            trace_id,
            "tool",
            "tool_call_failed",
            tool_name=step.tool,
            error=str(exc),
            source=type(gateway).__name__,
        )
        return {"tool_name": step.tool, "ok": False, "arguments": args, "error": str(exc)}


def summarize_once(llm: ChatOpenAI, question: str, plan: Plan, execution_results: List[dict], trace_id: str, audit_path: Path, max_attempts: int, backoff_seconds: float) -> tuple[str, dict, str]:
    # 第二次调用 LLM：不是为了规划，而是为了“总结最终结论”。
    # 这个阶段的输入通常包括：原始问题、执行计划、工具结果。
    # 例子：
    #   用户问题 + plan + tool results -> 最终中文总结
    system_prompt = "你是接口联调总结助手。请基于执行计划和工具结果，输出中文结论、关键请求、关键响应、下一步建议。"
    user_text = (
        f"用户目标：{question}\n\n"
        f"执行计划(JSON)：\n{json.dumps(plan.model_dump(), ensure_ascii=False)}\n\n"
        f"执行结果(JSON)：\n{json.dumps(execution_results, ensure_ascii=False)}"
    )

    def _invoke() -> AIMessage:
        return llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_text),
            ],
            config={"callbacks": [AuditCallbackHandler(audit_path, trace_id)]},
        )

    try:
        response = retry_call("summary_generation", trace_id, audit_path, max_attempts, backoff_seconds, _invoke)
        return (response.content or "").strip(), {"input_tokens": None, "output_tokens": None, "total_tokens": None}, "llm"
    except Exception as exc:
        fallback = build_fallback_summary(question, plan, execution_results, str(exc))
        append_audit_event(audit_path, trace_id, "summary", "summary_fallback_used", error=str(exc))
        return fallback, {"input_tokens": None, "output_tokens": None, "total_tokens": None}, "fallback"


def build_fallback_summary(question: str, plan: Plan, execution_results: List[dict], error_message: str) -> str:
    # 兜底总结：当 LLM 总结失败时，仍然保证产出可读报告。
    # 这类兜底逻辑在演示或生产中都很常见，属于稳定性设计的一部分。
    lines = [
        "### 结论",
        "",
        "总结阶段调用模型失败，以下为基于执行结果生成的本地兜底总结。",
        "",
        "### 用户目标",
        "",
        f"- {question}",
        "",
        "### 步骤结果",
        "",
    ]
    for idx, step in enumerate(plan.plan, start=1):
        result = execution_results[idx - 1] if idx - 1 < len(execution_results) else {}
        lines.append(f"- 第{idx}步 {step.tool}：{'成功' if result.get('ok') else '失败'}")
        if not result.get("ok") and result.get("error"):
            lines.append(f"  - 错误：{result.get('error')}")
    lines.extend([
        "",
        "### 下一步建议",
        "",
        "- 对失败步骤启用重试或切换到稳定网络后重跑。",
        f"- 本次总结兜底原因：{error_message}",
    ])
    return "\n".join(lines)


def write_report(path: Path, args: argparse.Namespace, trace_id: str, plan: Plan, plan_source: str, execution_results: List[dict], summary: str, summary_source: str) -> None:
    # 报告里不仅写结果，也写 plan_source / summary_source，方便你区分：
    # - LangChain 结构化计划是否成功
    # - 是否启用了默认计划兜底
    # - 最终总结是模型生成还是 fallback 生成
    path.parent.mkdir(parents=True, exist_ok=True)
    retry_total = sum(max(0, int(item.get("attempts") or 1) - 1) for item in execution_results)
    lines = [
        "# Day 25-28 LangChain Agent Demo",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- Trace ID：{trace_id}",
        f"- 模型：{args.model}",
        f"- 问题：{args.question}",
        f"- 计划来源：{plan_source}",
        f"- 总结来源：{summary_source}",
        f"- 单步最大重试：{args.max_attempts}",
        f"- 总重试次数：{retry_total}",
        "",
        "## Day 25 - LangChain 计划生成",
        "",
        "```json",
        json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Day 26 - 失败重试与超时处理",
        "",
        "- 工具调用和 LLM 调用都经过 `retry_call()` 包装。",
        "- 超时、连接错误、5xx 失败会触发退避重试。",
        "",
        "## Day 27 - 审计日志",
        "",
        "- LangChain callback 会记录 chain / llm / tool 事件。",
        "- `logs/day25_day28_langchain_demo.audit.jsonl` 记录每次尝试的时间戳、阶段和错误。",
        "",
        "## Day 28 - Agent Demo",
        "",
        "- 这个脚本可以直接作为演示入口，展示计划、工具调用、审计和总结。",
        "",
        "## Tool Results",
        "",
        "| Step | Tool | Ok | Why |",
        "|---:|---|---:|---|",
    ]
    for idx, step in enumerate(plan.plan, start=1):
        result = execution_results[idx - 1] if idx - 1 < len(execution_results) else {}
        lines.append(f"| {idx} | {step.tool} | {int(bool(result.get('ok')))} | {step.why[:120]} |")
    lines.extend([
        "",
        "## Summary",
        "",
        summary,
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    global CURRENT_HTTP_TIMEOUT

    # 整体运行顺序：
    # 1. build_llm() 创建 LangChain 模型对象
    # 2. build_plan() 生成结构化计划
    # 3. execute_step() 逐步调用工具
    # 4. summarize_once() 生成最终总结
    # 5. write_report() 输出可读报告 + 审计日志
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()
    prompt_path = resolve_project_path(args.prompt)
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)
    audit_path = resolve_project_path(args.audit)

    CURRENT_HTTP_TIMEOUT = max(1.0, float(args.timeout_seconds))
    model_name = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
    args.model = model_name
    api_key, base_url, _ = build_client()
    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )

    trace_id = uuid.uuid4().hex[:12]

    # 第三步接入：使用“可替换工具网关”。
    # 你后续把 LocalToolGateway 换成 McpToolGateway 时，
    # execute_step / summarize / report 这些业务流程代码不需要改。
    local_gateway = build_tool_gateway()
    gateway_selection = resolve_gateway(local_gateway)
    gateway = gateway_selection.gateway

    append_audit_event(audit_path, trace_id, "run", "run_started", question=args.question, model=model_name, timeout_seconds=CURRENT_HTTP_TIMEOUT)
    append_audit_event(
        audit_path,
        trace_id,
        "gateway",
        "gateway_selected",
        mode=gateway_selection.mode,
        source=gateway_selection.source,
        message=gateway_selection.message,
        gateway_class=type(gateway).__name__,
        tool_names=[tool.name for tool in gateway.list_tools()],
    )

    plan, plan_source = build_plan(llm, prompt_path, args.question, trace_id, audit_path, args.max_attempts, args.retry_backoff_seconds)
    append_jsonl(jsonl_path, {"timestamp_utc": utc_now_iso(), "trace_id": trace_id, "phase": "analysis", "plan": plan.model_dump()})

    execution_results: List[dict] = []
    for idx, step in enumerate(plan.plan[: args.max_steps], start=1):
        result = execute_step(gateway, step, trace_id, audit_path, args.max_attempts, args.retry_backoff_seconds)
        result["attempts"] = int(result.get("attempts") or 1)
        execution_results.append(result)
        append_jsonl(jsonl_path, {"timestamp_utc": utc_now_iso(), "trace_id": trace_id, "phase": "tool_execution", "step_index": idx, "step": step.model_dump(), "result": result})

    summary_text, summary_usage, summary_source = summarize_once(llm, args.question, plan, execution_results, trace_id, audit_path, args.max_attempts, args.retry_backoff_seconds)
    append_jsonl(jsonl_path, {"timestamp_utc": utc_now_iso(), "trace_id": trace_id, "phase": "summary", "summary_source": summary_source, "summary": summary_text, "usage": summary_usage})
    append_audit_event(audit_path, trace_id, "run", "run_completed", plan_source=plan_source, summary_source=summary_source)

    write_report(report_path, args, trace_id, plan, plan_source, execution_results, summary_text, summary_source)

    print("Done. Day25-28 LangChain agent demo generated.")
    print(f"Trace  => {trace_id}")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")
    print(f"Audit  => {args.audit}")
    print(f"Gateway => {gateway_selection.mode} ({gateway_selection.source})")


if __name__ == "__main__":
    main()