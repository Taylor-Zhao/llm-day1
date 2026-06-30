#!/usr/bin/env python3
"""Day 1 最小可运行 LLM CLI。

功能：读取系统提示词 -> 发起对话 -> 将每轮结果写入 JSONL，便于后续评测复盘。

Python 语法提示：
- `def f(...) -> str` 里的 `-> str` 是返回值类型注解，不会强制校验，但能提高可读性。
- `Path` 来自 pathlib，用于跨平台路径处理，比字符串拼路径更安全。
- `dict` / `list` 是 Python 最常用的结构化数据类型。
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import sleep

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Prompt

console = Console()


def normalize_usage(data: dict) -> dict:
    # 兼容 OpenAI-compatible usage 字段；若服务端不返回，则用 None 占位。
    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")

    # 某些本地服务可能只返回部分字段，尽量补齐 total_tokens。
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    return {
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def utc_now_iso() -> str:
    # 统一使用 UTC 时间，避免跨时区分析日志时出现混乱。
    return datetime.now(timezone.utc).isoformat()


def load_system_prompt(prompt_path: Path) -> str:
    # `Path.exists()` 检查文件是否存在；不存在时主动抛出异常，便于快速定位问题。
    if not prompt_path.exists():
        raise FileNotFoundError(f"System prompt file not found: {prompt_path}")
    # `read_text` 会一次性读入整个文本文件；`strip()` 去掉首尾空白字符。
    return prompt_path.read_text(encoding="utf-8").strip()


def append_jsonl(log_path: Path, record: dict) -> None:
    # JSONL 适合逐条追加，后续可直接用于脚本评测或数据分析。
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # `with ... as f` 是上下文管理器，用完会自动关闭文件。
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_client() -> tuple[str, str, httpx.Client]:
    # tuple[str, str, httpx.Client] 表示返回一个三元组：(api_key, base_url, client)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    # 兼容 OpenAI 官方与 OpenAI-compatible 网关。
    base_url = (os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1").rstrip("/")
    is_local_compatible = (
        base_url.startswith("http://127.0.0.1")
        or base_url.startswith("http://localhost")
    )
    if not api_key and is_local_compatible:
        # 本地 OpenAI-compatible 服务（如 Ollama）通常不校验真实 key。
        api_key = "ollama"
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Add it to .env first.")

    http_proxy = os.getenv("OPENAI_HTTP_PROXY", "").strip()
    # `httpx.Client(...)` 是可复用的 HTTP 客户端，比每次临时请求更高效。
    client = httpx.Client(proxy=http_proxy or None, timeout=30.0)
    return api_key, base_url, client


def chat_once(
    client: httpx.Client,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_text: str,
    temperature: float,
    max_tokens: int,
) -> tuple[str, dict]:
    # f-string: f"{base_url}/chat/completions" 会把变量值直接格式化到字符串里。
    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # `messages` 是列表，元素是字典；这是 OpenAI-compatible 接口常见格式。
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
    }

    # 本地模型服务在冷启动或刚加载模型时，偶发 502/503 是常见现象。
    # 这里做 3 次轻量重试，避免批量实验因为瞬时失败中断。
    last_error = None
    for attempt in range(3):
        try:
            resp = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < 2:
                sleep(1 + attempt)
                continue
            raise RuntimeError(f"Connection error: {exc}") from exc

        # 非 2xx 视为失败；对 502/503/504 做短暂重试。
        if resp.status_code in {502, 503, 504} and attempt < 2:
            last_error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            sleep(1 + attempt)
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        # 多层 get 可避免 KeyError；若字段缺失则回退到空字符串。
        assistant_text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        return assistant_text, normalize_usage(data)

    raise RuntimeError(f"Request failed after retries: {last_error}")


def parse_args() -> argparse.Namespace:
    # argparse 用于解析命令行参数，例如：python chat_cli.py --temperature 0.2
    parser = argparse.ArgumentParser(description="Day 1 LLM CLI")
    # If OPENAI_MODEL is set but empty, fall back to a safe default.
    default_model = (os.getenv("OPENAI_MODEL") or "").strip() or "gpt-4.1-mini"
    parser.add_argument("--model", default=default_model, help="Model name")
    parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature")
    parser.add_argument("--max-tokens", type=int, default=500, help="Max tokens for output")
    parser.add_argument(
        "--system-prompt",
        default="prompts/system_prompt_v1.txt",
        help="Path to system prompt file",
    )
    parser.add_argument("--log", default="logs/chat_history.jsonl", help="JSONL log path")
    return parser.parse_args()


def main() -> None:
    # 从 .env 加载环境变量到当前进程（例如 OPENAI_API_KEY）。
    load_dotenv()
    args = parse_args()

    system_prompt_path = Path(args.system_prompt)
    log_path = Path(args.log)
    system_prompt = load_system_prompt(system_prompt_path)
    api_key, base_url, client = build_client()

    console.print("[bold green]Day 1 LLM CLI is ready.[/bold green]")
    console.print("Type /exit to quit.")

    # 交互式循环：输入问题 -> 调用模型 -> 输出并记录日志。
    while True:
        user_text = Prompt.ask("\n[cyan]You[/cyan]").strip()
        if user_text.lower() in {"/exit", "exit", "quit"}:
            console.print("Bye.")
            break
        if not user_text:
            continue

        try:
            # try/except：捕获网络、鉴权、接口异常，避免程序直接崩溃退出。
            assistant_text, usage = chat_once(
                client=client,
                api_key=api_key,
                base_url=base_url,
                model=args.model,
                system_prompt=system_prompt,
                user_text=user_text,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
        except Exception as exc:
            exc_text = str(exc)
            if "Connection error" in exc_text:
                console.print("[red]Request failed:[/red] Connection error")
                console.print("[yellow]Hint:[/yellow] Your network cannot reach api.openai.com:443.")
                console.print("- Verify VPN/global proxy is enabled")
                console.print("- Test: curl -I https://api.openai.com/v1/models")
            elif "HTTP 429" in exc_text and "insufficient_quota" in exc_text:
                console.print("[red]Request failed:[/red] HTTP 429 insufficient_quota")
                console.print("[yellow]Hint:[/yellow] API quota/billing is insufficient.")
                console.print("- Open https://platform.openai.com/usage to check usage")
                console.print("- Open https://platform.openai.com/settings/organization/billing to add billing")
            else:
                console.print(f"[red]Request failed:[/red] {exc}")
            continue

        console.print(f"\n[bold yellow]Assistant[/bold yellow]: {assistant_text}")
        console.print(
            "[dim]Usage:[/dim] "
            f"input={usage['input_tokens']} "
            f"output={usage['output_tokens']} "
            f"total={usage['total_tokens']}"
        )

        # 每轮完整记录参数与问答内容，便于后续对比不同 prompt/参数效果。
        record = {
            "timestamp_utc": utc_now_iso(),
            "model": args.model,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "system_prompt_file": str(system_prompt_path),
            "user": user_text,
            "assistant": assistant_text,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
        }
        append_jsonl(log_path, record)


if __name__ == "__main__":
    main()
