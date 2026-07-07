#!/usr/bin/env python3
"""Day 24：接口联调助手（可调用 HTTP 工具）。"""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from chat_cli import build_client, load_system_prompt, normalize_usage, utc_now_iso

PROJECT_DIR = Path(__file__).resolve().parent
ALLOWED_HTTP_HOSTS = {"httpbin.org"}
CURRENT_HTTP_TIMEOUT = 15.0

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
    parser = argparse.ArgumentParser(description="Run Day24 HTTP integration assistant")
    parser.add_argument(
        "--question",
        default="请联调订单查询接口：检查 order_id=1001 是否存在并返回状态",
        help="联调问题",
    )
    parser.add_argument(
        "--system-prompt",
        default="prompts/day24_http_integration_assistant_cn.txt",
        help="系统提示词路径",
    )
    parser.add_argument("--temperature", type=float, default=0.0, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=700, help="输出 token 上限")
    parser.add_argument("--max-tool-rounds", type=int, default=6, help="最多工具调用轮数")
    parser.add_argument("--timeout-seconds", type=float, default=15.0, help="HTTP 请求超时")
    parser.add_argument("--model", default="", help="可选：覆盖环境变量中的模型名")
    parser.add_argument(
        "--report",
        default="experiments/day24_http_integration_assistant.md",
        help="Markdown 报告",
    )
    parser.add_argument(
        "--jsonl",
        default="logs/day24_http_integration_assistant.jsonl",
        help="逐步日志 JSONL",
    )
    return parser.parse_args()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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


def http_post(url: str, json_body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
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


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "list_mock_endpoints",
            "description": "查看可联调的 mock HTTP endpoints。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_get",
            "description": "执行 GET 请求（仅白名单 URL）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "请求 URL"},
                    "params": {"type": "object", "description": "query 参数"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_post",
            "description": "执行 POST 请求（仅白名单 URL）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "请求 URL"},
                    "json_body": {"type": "object", "description": "JSON 请求体"},
                },
                "required": ["url"],
            },
        },
    },
]


TOOL_IMPLS: dict[str, Callable[..., dict[str, Any]]] = {
    "list_mock_endpoints": list_mock_endpoints,
    "http_get": http_get,
    "http_post": http_post,
}


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


def execute_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    function_obj = tool_call.get("function") or {}
    name = function_obj.get("name") or ""
    raw_args = function_obj.get("arguments") or "{}"
    try:
        kwargs = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        return {"tool_name": name, "ok": False, "error": f"invalid tool arguments: {exc}"}

    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return {"tool_name": name, "ok": False, "error": "unknown tool"}

    try:
        result = impl(**kwargs)
        return {"tool_name": name, "ok": True, "arguments": kwargs, "result": result}
    except Exception as exc:
        return {"tool_name": name, "ok": False, "arguments": kwargs, "error": str(exc)}


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
    final_answer: str,
    steps: list[dict[str, Any]],
    usage_totals: dict[str, Optional[int]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 24 接口联调助手报告",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 模型：{args.model}",
        f"- 问题：{args.question}",
        f"- 请求超时：{args.timeout_seconds}s",
        "",
        "## 安全限制",
        "",
        "1. 仅允许访问白名单域名（默认 https://httpbin.org）。",
        "2. 请求超时可配置，避免接口长时间阻塞。",
        "3. 响应体会被裁剪，避免日志过长。",
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
            summary = json.dumps(step["payload"], ensure_ascii=False)[:240]
            lines.append(f"| {idx} | tool_result | {step['tool_name']} | {summary} |")
        else:
            summary = (step.get("content") or "").replace("\n", " ")[:160]
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


def main() -> None:
    global CURRENT_HTTP_TIMEOUT

    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)
    system_prompt_path = resolve_project_path(args.system_prompt)

    CURRENT_HTTP_TIMEOUT = max(1.0, float(args.timeout_seconds))
    system_prompt = load_system_prompt(system_prompt_path)
    model_name = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
    args.model = model_name
    api_key, base_url, client = build_client()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "system",
            "content": "你只能调用白名单接口: https://httpbin.org。请先 list_mock_endpoints 再联调。",
        },
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
            tools=TOOL_SPECS,
        )
        add_usage(usage_totals, usage)

        tool_calls = message.get("tool_calls") or []
        content = message.get("content") or ""

        if tool_calls:
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
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
                result_payload = execute_tool_call(item)
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

    print("Done. Day24 HTTP integration assistant generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()