#!/usr/bin/env python3
"""Day 26-28: retry, audit trail, and demo-ready orchestration."""

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from chat_cli import build_client, chat_once, load_system_prompt, utc_now_iso

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


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day26-28 agent demo")
    parser.add_argument(
        "--question",
        default=DEFAULT_QUESTION,
        help="编排目标问题",
    )
    parser.add_argument(
        "--planner-prompt",
        default="prompts/day26_day28_agent_demo_cn.txt",
        help="任务规划提示词路径",
    )
    parser.add_argument("--temperature", type=float, default=0.0, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=700, help="输出 token 上限")
    parser.add_argument("--max-steps", type=int, default=6, help="最多执行步骤")
    parser.add_argument("--timeout-seconds", type=float, default=15.0, help="HTTP 请求超时")
    parser.add_argument("--max-tool-attempts", type=int, default=3, help="单个工具最大重试次数")
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=0.5,
        help="工具/LLM 重试退避秒数",
    )
    parser.add_argument("--model", default="", help="可选：覆盖环境变量中的模型名")
    parser.add_argument(
        "--report",
        default="experiments/day26_day28_agent_demo.md",
        help="Markdown 报告",
    )
    parser.add_argument(
        "--jsonl",
        default="logs/day26_day28_agent_demo.jsonl",
        help="编排步骤 JSONL",
    )
    parser.add_argument(
        "--audit",
        default="logs/day26_day28_agent_demo.audit.jsonl",
        help="审计日志 JSONL",
    )
    return parser.parse_args()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
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


def list_mock_endpoints() -> dict[str, Any]:
    return {"endpoints": MOCK_ENDPOINTS}


def http_get(url: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    ensure_allowed_url(url)
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
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
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.2)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("http_get failed")


def http_post(url: str, json_body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    ensure_allowed_url(url)
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
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
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.2)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("http_post failed")


TOOL_IMPLS: dict[str, Callable[..., dict[str, Any]]] = {
    "list_mock_endpoints": list_mock_endpoints,
    "http_get": http_get,
    "http_post": http_post,
}


def extract_json_payload(text: str) -> str:
    payload = text.strip()
    if payload.startswith("```"):
        lines = payload.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        payload = "\n".join(lines).strip()
        if payload.lower().startswith("json"):
            payload = payload[4:].strip()
    return payload


def build_default_plan(question: str) -> list[dict[str, Any]]:
    return [
        {
            "step": "识别可用 endpoint",
            "tool": "list_mock_endpoints",
            "args": {},
            "why": "首先需要确认有哪些可联调的 endpoint。",
        },
        {
            "step": "请求 order_id=1001",
            "tool": "http_get",
            "args": {"order_id": 1001},
            "why": "使用 order_id=1001 读取查询接口结果。",
        },
        {
            "step": "总结联调结果",
            "tool": "http_post",
            "args": {},
            "why": "调用 POST 工具收束最终联调结论。",
        },
    ]


def is_retryable_error_text(error_text: str) -> bool:
    lowered = error_text.lower()
    retry_markers = [
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection error",
        "connection reset",
        "502",
        "503",
        "504",
    ]
    return any(marker in lowered for marker in retry_markers)


def is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return is_retryable_error_text(str(exc))


def retry_llm_call(
    *,
    operation_name: str,
    phase: str,
    trace_id: str,
    audit_path: Path,
    max_attempts: int,
    backoff_seconds: float,
    callable_fn: Callable[[], Any],
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        start = time.perf_counter()
        append_audit_event(
            audit_path,
            trace_id,
            phase,
            f"{operation_name}_attempt_started",
            attempt=attempt,
            max_attempts=max_attempts,
        )
        try:
            value = callable_fn()
            latency_ms = int((time.perf_counter() - start) * 1000)
            append_audit_event(
                audit_path,
                trace_id,
                phase,
                f"{operation_name}_attempt_succeeded",
                attempt=attempt,
                latency_ms=latency_ms,
            )
            return value
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            last_error = exc
            append_audit_event(
                audit_path,
                trace_id,
                phase,
                f"{operation_name}_attempt_failed",
                attempt=attempt,
                latency_ms=latency_ms,
                error=str(exc),
            )
            if attempt < max_attempts and is_retryable_exception(exc):
                append_audit_event(
                    audit_path,
                    trace_id,
                    phase,
                    f"{operation_name}_retry_scheduled",
                    attempt=attempt,
                    next_attempt=attempt + 1,
                    backoff_seconds=backoff_seconds * attempt,
                    reason=str(exc),
                )
                time.sleep(backoff_seconds * attempt)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{operation_name} failed")


def build_plan_once(
    *,
    client: httpx.Client,
    api_key: str,
    base_url: str,
    model: str,
    planner_prompt: str,
    question: str,
    temperature: float,
    max_tokens: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    user_text = (
        "请根据用户目标生成任务编排计划（只输出 JSON）：\n"
        f"用户目标：{question}\n"
        "可用工具：list_mock_endpoints, http_get, http_post"
    )
    assistant_text, usage = chat_once(
        client=client,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system_prompt=planner_prompt,
        user_text=user_text,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    payload = extract_json_payload(assistant_text)
    try:
        data = json.loads(payload)
    except Exception as exc:
        raise RuntimeError(f"planner output is not valid JSON: {exc}")

    plan = data.get("plan") or []
    if not isinstance(plan, list) or not plan:
        raise RuntimeError("planner returned empty plan")
    return plan, usage, assistant_text


def build_plan(
    *,
    client: httpx.Client,
    api_key: str,
    base_url: str,
    model: str,
    planner_prompt: str,
    question: str,
    temperature: float,
    max_tokens: int,
    trace_id: str,
    audit_path: Path,
    max_attempts: int,
    backoff_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, str]:
    try:
        plan, usage, raw_plan = retry_llm_call(
            operation_name="plan_generation",
            phase="analysis",
            trace_id=trace_id,
            audit_path=audit_path,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            callable_fn=lambda: build_plan_once(
                client=client,
                api_key=api_key,
                base_url=base_url,
                model=model,
                planner_prompt=planner_prompt,
                question=question,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
        return plan, usage, raw_plan, "llm"
    except Exception as exc:
        fallback_plan = build_default_plan(question)
        raw_plan = json.dumps({"plan": fallback_plan}, ensure_ascii=False, indent=2)
        append_audit_event(
            audit_path,
            trace_id,
            "analysis",
            "plan_fallback_used",
            error=str(exc),
            fallback_step_count=len(fallback_plan),
        )
        return fallback_plan, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, raw_plan, "fallback"


def normalize_step_args(step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    tool_name = str(step.get("tool") or "").strip()
    args = step.get("args") or {}
    if not isinstance(args, dict):
        args = {}

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

    return tool_name, args


def execute_step_once(step: dict[str, Any]) -> dict[str, Any]:
    tool_name, args = normalize_step_args(step)
    impl = TOOL_IMPLS.get(tool_name)
    if impl is None:
        return {
            "tool_name": tool_name,
            "ok": False,
            "arguments": args,
            "error": "unknown tool",
        }
    try:
        result = impl(**args)
        return {
            "tool_name": tool_name,
            "ok": True,
            "arguments": args,
            "result": result,
        }
    except Exception as exc:
        return {
            "tool_name": tool_name,
            "ok": False,
            "arguments": args,
            "error": str(exc),
        }


def execute_step_with_retry(
    *,
    step: dict[str, Any],
    step_index: int,
    trace_id: str,
    audit_path: Path,
    max_attempts: int,
    backoff_seconds: float,
) -> dict[str, Any]:
    tool_name, args = normalize_step_args(step)
    last_result: Optional[dict[str, Any]] = None

    for attempt in range(1, max_attempts + 1):
        start = time.perf_counter()
        append_audit_event(
            audit_path,
            trace_id,
            "tool_execution",
            "tool_attempt_started",
            step_index=step_index,
            attempt=attempt,
            max_attempts=max_attempts,
            tool_name=tool_name,
            arguments=args,
        )
        result = execute_step_once(step)
        latency_ms = int((time.perf_counter() - start) * 1000)
        result = dict(result)
        result["attempts"] = attempt
        result["latency_ms"] = latency_ms
        last_result = result

        append_audit_event(
            audit_path,
            trace_id,
            "tool_execution",
            "tool_attempt_finished",
            step_index=step_index,
            attempt=attempt,
            tool_name=tool_name,
            ok=bool(result.get("ok")),
            latency_ms=latency_ms,
            error=result.get("error"),
        )

        if result.get("ok"):
            return result

        error_text = str(result.get("error") or "")
        if attempt < max_attempts and tool_name in {"http_get", "http_post"} and is_retryable_error_text(error_text):
            append_audit_event(
                audit_path,
                trace_id,
                "tool_execution",
                "tool_retry_scheduled",
                step_index=step_index,
                attempt=attempt,
                next_attempt=attempt + 1,
                backoff_seconds=backoff_seconds * attempt,
                tool_name=tool_name,
                reason=error_text,
            )
            time.sleep(backoff_seconds * attempt)
            continue

        return result

    if last_result is not None:
        return last_result
    return {
        "tool_name": tool_name,
        "ok": False,
        "arguments": args,
        "error": "step execution failed",
        "attempts": max_attempts,
        "latency_ms": 0,
    }


def summarize_results_once(
    *,
    client: httpx.Client,
    api_key: str,
    base_url: str,
    model: str,
    question: str,
    plan: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    system_prompt = "你是接口联调总结助手。请基于执行计划和工具结果，输出中文结论、关键请求、关键响应、下一步建议。"
    user_text = (
        f"用户目标：{question}\n\n"
        "执行计划(JSON)：\n"
        f"{json.dumps(plan, ensure_ascii=False)}\n\n"
        "执行结果(JSON)：\n"
        f"{json.dumps(execution_results, ensure_ascii=False)}"
    )
    summary_text, usage = chat_once(
        client=client,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system_prompt=system_prompt,
        user_text=user_text,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return summary_text.strip(), usage


def summarize_results(
    *,
    client: httpx.Client,
    api_key: str,
    base_url: str,
    model: str,
    question: str,
    plan: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    trace_id: str,
    audit_path: Path,
    max_attempts: int,
    backoff_seconds: float,
) -> tuple[str, dict[str, Any], str]:
    try:
        summary_text, usage = retry_llm_call(
            operation_name="summary_generation",
            phase="summary",
            trace_id=trace_id,
            audit_path=audit_path,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            callable_fn=lambda: summarize_results_once(
                client=client,
                api_key=api_key,
                base_url=base_url,
                model=model,
                question=question,
                plan=plan,
                execution_results=execution_results,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
        return summary_text, usage, "llm"
    except Exception as exc:
        summary_text = build_fallback_summary(
            question=question,
            plan=plan,
            execution_results=execution_results,
            error_message=str(exc),
        )
        append_audit_event(
            audit_path,
            trace_id,
            "summary",
            "summary_fallback_used",
            error=str(exc),
        )
        return summary_text, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "fallback"


def build_fallback_summary(
    *,
    question: str,
    plan: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    error_message: str,
) -> str:
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

    for idx, step in enumerate(plan, start=1):
        result = execution_results[idx - 1] if idx - 1 < len(execution_results) else {}
        ok = bool(result.get("ok"))
        tool = str(step.get("tool") or "")
        lines.append(f"- 第{idx}步 {tool}：{'成功' if ok else '失败'}")
        if not ok and result.get("error"):
            lines.append(f"  - 错误：{result.get('error')}")

    lines.extend(
        [
            "",
            "### 下一步建议",
            "",
            "- 对失败步骤启用重试或切换到稳定网络后重跑。",
            "- 若多次超时，可提升 --timeout-seconds 或 --max-tool-attempts。",
            f"- 本次总结兜底原因：{error_message}",
        ]
    )
    return "\n".join(lines)


def add_usage(totals: dict[str, Optional[int]], usage: dict[str, Any]) -> None:
    for key in ["input_tokens", "output_tokens", "total_tokens"]:
        value = usage.get(key)
        if value is None:
            continue
        current = totals.get(key) or 0
        totals[key] = current + int(value)


def write_report(
    path: Path,
    *,
    args: argparse.Namespace,
    trace_id: str,
    plan: list[dict[str, Any]],
    plan_source: str,
    execution_results: list[dict[str, Any]],
    summary: str,
    summary_source: str,
    usage_totals: dict[str, Optional[int]],
    audit_path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    retry_count = sum(max(0, int(item.get("attempts") or 1) - 1) for item in execution_results)
    failed_steps = [str(idx) for idx, item in enumerate(execution_results, start=1) if not item.get("ok")]

    lines = [
        "# Day 26-28 Agent Demo Report（失败重试 / 审计日志 / 演示版）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- Trace ID：{trace_id}",
        f"- 模型：{args.model}",
        f"- 问题：{args.question}",
        f"- 最大步骤：{args.max_steps}",
        f"- 单步最大重试：{args.max_tool_attempts}",
        f"- 退避秒数：{args.retry_backoff_seconds}",
        f"- 计划来源：{plan_source}",
        f"- 总结来源：{summary_source}",
        f"- 审计日志：{audit_path}",
        "",
        "## Day 26 - 失败重试与超时处理",
        "",
        f"- 工具重试总次数：{retry_count}",
        f"- 失败步骤：{', '.join(failed_steps) if failed_steps else '无'}",
        "- HTTP 工具对超时做了重试，LLM 计划/总结也有退避重试。",
        "",
        "## Day 27 - 审计日志",
        "",
        f"- 每个步骤尝试都会写入 `logs/day26_day28_agent_demo.audit.jsonl`。",
        "- 审计记录包含 trace_id、phase、event、step_index、attempt、latency_ms、error 等字段。",
        "",
        "## Day 28 - Agent Demo",
        "",
        "- 这个脚本可以直接作为演示入口：先规划，再执行工具，最后生成总结和报告。",
        "- 发生超时或模型失败时，会回退到本地兜底总结，保证演示不被中断。",
        "",
        "## Phase 1 - Analysis（执行计划）",
        "",
        "```json",
        json.dumps({"plan": plan}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Phase 2 - Tool Execution（步骤执行结果）",
        "",
        "| Step | Tool | Ok | Attempts | Why |",
        "|---:|---|---:|---:|---|",
    ]

    for idx, step in enumerate(plan, start=1):
        ok = False
        attempts = 0
        if idx - 1 < len(execution_results):
            ok = bool(execution_results[idx - 1].get("ok"))
            attempts = int(execution_results[idx - 1].get("attempts") or 1)
        lines.append(
            f"| {idx} | {step.get('tool')} | {int(ok)} | {attempts} | {(step.get('why') or '')[:120]} |"
        )

    lines.extend(
        [
            "",
            "## Phase 3 - Summary（最终总结）",
            "",
            summary,
            "",
            "## Token Usage",
            "",
            f"- input_tokens_total: {usage_totals['input_tokens']}",
            f"- output_tokens_total: {usage_totals['output_tokens']}",
            f"- total_tokens_total: {usage_totals['total_tokens']}",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def print_demo_summary(args: argparse.Namespace, trace_id: str, plan_source: str, summary_source: str) -> None:
    print("Done. Day26-28 agent demo generated.")
    print(f"Trace  => {trace_id}")
    print(f"Plan   => {plan_source}")
    print(f"Summary=> {summary_source}")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")
    print(f"Audit  => {args.audit}")


def main() -> None:
    global CURRENT_HTTP_TIMEOUT

    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    planner_prompt_path = resolve_project_path(args.planner_prompt)
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)
    audit_path = resolve_project_path(args.audit)

    CURRENT_HTTP_TIMEOUT = max(1.0, float(args.timeout_seconds))
    planner_prompt = load_system_prompt(planner_prompt_path)
    model_name = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
    args.model = model_name
    api_key, base_url, client = build_client()

    trace_id = uuid.uuid4().hex[:12]
    usage_totals: dict[str, Optional[int]] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    append_audit_event(
        audit_path,
        trace_id,
        "run",
        "run_started",
        question=args.question,
        model=model_name,
        timeout_seconds=CURRENT_HTTP_TIMEOUT,
    )

    plan, plan_usage, raw_plan, plan_source = build_plan(
        client=client,
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        planner_prompt=planner_prompt,
        question=args.question,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        trace_id=trace_id,
        audit_path=audit_path,
        max_attempts=args.max_tool_attempts,
        backoff_seconds=args.retry_backoff_seconds,
    )
    add_usage(usage_totals, plan_usage)
    append_jsonl(
        jsonl_path,
        {
            "timestamp_utc": utc_now_iso(),
            "trace_id": trace_id,
            "phase": "analysis",
            "plan_source": plan_source,
            "plan": plan,
            "raw_plan_text": raw_plan,
        },
    )

    execution_results: list[dict[str, Any]] = []
    for idx, step in enumerate(plan[: args.max_steps], start=1):
        result = execute_step_with_retry(
            step=step,
            step_index=idx,
            trace_id=trace_id,
            audit_path=audit_path,
            max_attempts=args.max_tool_attempts,
            backoff_seconds=args.retry_backoff_seconds,
        )
        execution_results.append(result)
        append_jsonl(
            jsonl_path,
            {
                "timestamp_utc": utc_now_iso(),
                "trace_id": trace_id,
                "phase": "tool_execution",
                "step_index": idx,
                "step": step,
                "result": result,
            },
        )

    summary_text, summary_usage, summary_source = summarize_results(
        client=client,
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        question=args.question,
        plan=plan[: args.max_steps],
        execution_results=execution_results,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        trace_id=trace_id,
        audit_path=audit_path,
        max_attempts=args.max_tool_attempts,
        backoff_seconds=args.retry_backoff_seconds,
    )
    add_usage(usage_totals, summary_usage)
    append_jsonl(
        jsonl_path,
        {
            "timestamp_utc": utc_now_iso(),
            "trace_id": trace_id,
            "phase": "summary",
            "summary_source": summary_source,
            "summary": summary_text,
        },
    )

    write_report(
        report_path,
        args=args,
        trace_id=trace_id,
        plan=plan[: args.max_steps],
        plan_source=plan_source,
        execution_results=execution_results,
        summary=summary_text,
        summary_source=summary_source,
        usage_totals=usage_totals,
        audit_path=audit_path,
    )

    append_audit_event(
        audit_path,
        trace_id,
        "run",
        "run_completed",
        plan_source=plan_source,
        summary_source=summary_source,
    )

    print_demo_summary(args, trace_id, plan_source, summary_source)


if __name__ == "__main__":
    main()