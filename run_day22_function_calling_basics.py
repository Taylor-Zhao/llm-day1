#!/usr/bin/env python3
"""Day 22：学习函数调用机制，定义 2-3 个工具。"""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

from chat_cli import build_client, load_system_prompt, normalize_usage, utc_now_iso
from tooling import LocalToolGateway, ToolDefinition, build_openai_tool_specs, resolve_gateway

PROJECT_DIR = Path(__file__).resolve().parent

SERVICE_OWNERS = {
    "payment": {"owner": "finops-oncall", "slack": "#team-finops", "severity": "high"},
    "order": {"owner": "order-platform", "slack": "#team-order", "severity": "high"},
    "search": {"owner": "search-infra", "slack": "#team-search", "severity": "medium"},
    "user": {"owner": "account-core", "slack": "#team-account", "severity": "medium"},
}

INCIDENT_PLAYBOOK = {
    "timeout": [
        "先看 5xx 比例与 p95/p99 是否同步升高。",
        "排查下游依赖是否变慢，并核对连接池或线程池是否耗尽。",
        "优先保护核心接口，对非核心请求做限流或降级。",
    ],
    "cache": [
        "检查缓存 key 设计与过期策略是否合理。",
        "核对是否出现批量失效或缓存穿透。",
        "观察数据库与远程服务请求是否同步抬升。",
    ],
    "database": [
        "检查 wait_count、active 连接数与慢查询数量。",
        "联动分析 SQL 执行时间、锁等待和应用线程数。",
        "确认连接池参数是否与并发规模匹配。",
    ],
}


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day22 function calling basics")
    parser.add_argument(
        "--question",
        default="payment 服务现在应该联系谁？顺便帮我算一下 12 + 30，并给一个 timeout 排查建议。",
        help="演示问题",
    )
    parser.add_argument(
        "--system-prompt",
        default="prompts/day22_tool_calling_assistant_cn.txt",
        help="系统提示词路径",
    )
    parser.add_argument("--temperature", type=float, default=0.0, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=600, help="输出 token 上限")
    parser.add_argument("--max-tool-rounds", type=int, default=4, help="最多工具调用轮数")
    parser.add_argument("--model", default="", help="可选：覆盖环境变量中的模型名")
    parser.add_argument(
        "--report",
        default="experiments/day22_function_calling_basics.md",
        help="Markdown 报告",
    )
    parser.add_argument(
        "--jsonl",
        default="logs/day22_function_calling_basics.jsonl",
        help="逐步日志 JSONL",
    )
    return parser.parse_args()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def add_numbers(a: float, b: float) -> dict[str, Any]:
    return {"a": a, "b": b, "sum": a + b}


def lookup_service_owner(service: str) -> dict[str, Any]:
    key = service.strip().lower()
    data = SERVICE_OWNERS.get(key)
    if not data:
        return {
            "service": service,
            "found": False,
            "message": "未找到该服务负责人，请补充服务名或扩展映射表。",
        }
    return {"service": service, "found": True, **data}


def search_incident_playbook(topic: str) -> dict[str, Any]:
    key = topic.strip().lower()
    tips = INCIDENT_PLAYBOOK.get(key)
    if not tips:
        return {
            "topic": topic,
            "found": False,
            "message": "当前内置手册未覆盖该主题。",
            "tips": [],
        }
    return {"topic": topic, "found": True, "tips": tips}


def build_tool_gateway() -> LocalToolGateway:
    """Build local gateway so orchestration is decoupled from tool registration."""

    return LocalToolGateway(
        [
            ToolDefinition(
                name="add_numbers",
                description="计算两个数字之和。",
                parameters={
                    "type": "object",
                    "properties": {
                        "a": {"type": "number", "description": "第一个数字"},
                        "b": {"type": "number", "description": "第二个数字"},
                    },
                    "required": ["a", "b"],
                },
                executor=add_numbers,
            ),
            ToolDefinition(
                name="lookup_service_owner",
                description="查询某个服务的负责人和值班渠道。",
                parameters={
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": "服务名，例如 payment、order",
                        },
                    },
                    "required": ["service"],
                },
                executor=lookup_service_owner,
            ),
            ToolDefinition(
                name="search_incident_playbook",
                description="查询指定故障主题的内置处理手册。",
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "主题，例如 timeout、cache、database",
                        },
                    },
                    "required": ["topic"],
                },
                executor=search_incident_playbook,
            ),
        ]
    )


def chat_once_with_tools(
    *,
    client: httpx.Client,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    tools: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    resp = client.post(url, headers=headers, json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    message = data.get("choices", [{}])[0].get("message", {})
    return message, normalize_usage(data)


def execute_tool_call(
    gateway: LocalToolGateway,
    tool_call: dict[str, Any],
) -> dict[str, Any]:
    function_obj = tool_call.get("function") or {}
    name = function_obj.get("name") or ""
    raw_args = function_obj.get("arguments") or "{}"
    try:
        kwargs = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        return {
            "tool_name": name,
            "ok": False,
            "error": f"invalid tool arguments: {exc}",
        }

    return gateway.call_tool(name, kwargs)


def write_report(
    path: Path,
    *,
    args: argparse.Namespace,
    final_answer: str,
    steps: list[dict[str, Any]],
    usage_totals: dict[str, Optional[int]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 22 函数调用机制学习报告",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 模型：{args.model}",
        f"- 问题：{args.question}",
        f"- 最大工具轮数：{args.max_tool_rounds}",
        "",
        "## 工具定义",
        "",
        "1. `add_numbers(a, b)`：计算两个数之和。",
        "2. `lookup_service_owner(service)`：查询服务负责人和值班渠道。",
        "3. `search_incident_playbook(topic)`：查询故障主题的处理建议。",
        "",
        "## 最终回答",
        "",
        final_answer,
        "",
        "## 调用过程",
        "",
        "| Step | Type | Name | Summary |",
        "|---:|---|---|---|",
    ]

    for idx, step in enumerate(steps, start=1):
        if step["type"] == "assistant_tool_call":
            summary = json.dumps(step["tool_calls"], ensure_ascii=False)
            lines.append(f"| {idx} | assistant_tool_call | multiple | {summary} |")
        elif step["type"] == "tool_result":
            summary = json.dumps(step["payload"], ensure_ascii=False)
            lines.append(f"| {idx} | tool_result | {step['tool_name']} | {summary} |")
        else:
            summary = (step.get("content") or "").replace("\n", " ")[:120]
            lines.append(f"| {idx} | assistant_final | final_answer | {summary} |")

    lines.extend(
        [
            "",
            "## Token Usage",
            "",
            f"- input_tokens_total: {usage_totals['input_tokens']}",
            f"- output_tokens_total: {usage_totals['output_tokens']}",
            f"- total_tokens_total: {usage_totals['total_tokens']}",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def add_usage(totals: dict[str, Optional[int]], usage: dict[str, Any]) -> None:
    for key in ["input_tokens", "output_tokens", "total_tokens"]:
        value = usage.get(key)
        if value is None:
            continue
        current = totals.get(key) or 0
        totals[key] = current + int(value)


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    system_prompt_path = resolve_project_path(args.system_prompt)
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)

    system_prompt = load_system_prompt(system_prompt_path)
    model_name = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
    args.model = model_name
    api_key, base_url, client = build_client()

    # 第三步（MCP + 回退）核心入口：
    # 1) 先构建本地网关（始终可用）；
    # 2) 再根据环境变量决定是否切到 MCP 网关；
    # 3) 如果是 auto 模式且 MCP 不可用，会自动回退 local，不中断脚本。
    local_gateway = build_tool_gateway()
    gateway_selection = resolve_gateway(local_gateway)
    tool_gateway = gateway_selection.gateway

    # 这里的 tool_specs 是 function calling 真正消费的“工具 schema 列表”。
    # 重点：
    # - 模型只看到 schema，并不知道背后是 local 还是 MCP；
    # - 因此后续迁移到 MCP 不需要改对话编排主流程。
    tool_specs = build_openai_tool_specs(tool_gateway)

    append_jsonl(
        jsonl_path,
        {
            "timestamp_utc": utc_now_iso(),
            "phase": "gateway",
            "mode": gateway_selection.mode,
            "source": gateway_selection.source,
            "message": gateway_selection.message,
            "tool_names": [item["function"]["name"] for item in tool_specs],
        },
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": args.question},
    ]

    steps: list[dict[str, Any]] = []
    usage_totals: dict[str, Optional[int]] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    final_answer = ""

    for round_idx in range(1, args.max_tool_rounds + 1):
        message, usage = chat_once_with_tools(
            client=client,
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            messages=messages,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            tools=tool_specs,
        )
        add_usage(usage_totals, usage)

        tool_calls = message.get("tool_calls") or []
        content = message.get("content") or ""

        if tool_calls:
            assistant_entry = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
            messages.append(assistant_entry)
            step = {
                "timestamp_utc": utc_now_iso(),
                "round": round_idx,
                "type": "assistant_tool_call",
                "tool_calls": [
                    {
                        "id": item.get("id"),
                        "name": (item.get("function") or {}).get("name"),
                        "arguments": (item.get("function") or {}).get("arguments"),
                    }
                    for item in tool_calls
                ],
            }
            steps.append(step)
            append_jsonl(jsonl_path, step)

            for item in tool_calls:
                result_payload = execute_tool_call(tool_gateway, item)
                tool_name = result_payload.get("tool_name") or "unknown"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": item.get("id"),
                        "name": tool_name,
                        "content": json.dumps(result_payload, ensure_ascii=False),
                    }
                )
                tool_step = {
                    "timestamp_utc": utc_now_iso(),
                    "round": round_idx,
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "payload": result_payload,
                }
                steps.append(tool_step)
                append_jsonl(jsonl_path, tool_step)
            continue

        final_answer = content.strip()
        final_step = {
            "timestamp_utc": utc_now_iso(),
            "round": round_idx,
            "type": "assistant_final",
            "content": final_answer,
        }
        steps.append(final_step)
        append_jsonl(jsonl_path, final_step)
        break

    if not final_answer:
        raise RuntimeError("model did not produce a final answer within max tool rounds")

    write_report(
        report_path,
        args=args,
        final_answer=final_answer,
        steps=steps,
        usage_totals=usage_totals,
    )

    print("Done. Day22 function calling demo generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")
    print(f"Gateway => {gateway_selection.mode} ({gateway_selection.source})")


if __name__ == "__main__":
    main()