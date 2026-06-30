#!/usr/bin/env python3
"""Day 3：后端助手（日志分析 + 代码解释）。"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import sleep

from dotenv import load_dotenv

from chat_cli import build_client, chat_once

PROJECT_DIR = Path(__file__).resolve().parent


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    return path.read_text(encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day 3 backend assistant")
    parser.add_argument("--mode", choices=["log-analysis", "code-explain"], required=True, help="助手模式")
    parser.add_argument("--input-file", required=True, help="输入文件路径（日志或代码）")
    parser.add_argument("--question", default="", help="附加问题（可选）")
    parser.add_argument("--temperature", type=float, default=0.2, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=600, help="输出 token 上限")
    parser.add_argument("--model", default="", help="可选：覆盖环境变量中的模型名")
    parser.add_argument("--report", default="experiments/day3_backend_assistant.md", help="Markdown 报告路径")
    parser.add_argument("--jsonl", default="logs/day3_backend_assistant.jsonl", help="JSONL 记录路径")
    return parser.parse_args()


def build_system_prompt(mode: str) -> str:
    prompt_file = {
        "log-analysis": "prompts/day3_log_analyst_cn.txt",
        "code-explain": "prompts/day3_code_explainer_cn.txt",
    }[mode]
    return load_text(resolve_project_path(prompt_file))


def build_user_text(mode: str, input_text: str, question: str) -> str:
    if mode == "log-analysis":
        base = "请分析下面日志，输出根因、证据、排查步骤和修复建议。\n\n日志内容:\n" + input_text
    else:
        base = "请解释下面代码的职责、流程、风险和可改进点。\n\n代码内容:\n" + input_text

    if question.strip():
        base += "\n\n附加问题:\n" + question.strip()
    return base


def write_report(path: Path, mode: str, input_file: str, question: str, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 3 后端助手报告（自动生成）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 模式：{mode}",
        f"- 输入文件：{input_file}",
        f"- 附加问题：{question if question else '(无)'}",
        f"- 模型：{result['model']}",
        f"- Input tokens：{result['input_tokens']}",
        f"- Output tokens：{result['output_tokens']}",
        f"- Total tokens：{result['total_tokens']}",
        "",
        "## 分析输出",
        "",
        result["assistant"],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    input_path = resolve_project_path(args.input_file)
    input_text = load_text(input_path)
    system_prompt = build_system_prompt(args.mode)
    user_text = build_user_text(args.mode, input_text, args.question)

    model_name = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
    api_key, base_url, client = build_client()

    last_error = None
    for attempt in range(3):
        try:
            assistant, usage = chat_once(
                client=client,
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                system_prompt=system_prompt,
                user_text=user_text,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
            break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                sleep(1 + attempt)
                continue
            raise RuntimeError(f"Day3 request failed after retries: {exc}") from exc

    record = {
        "timestamp_utc": utc_now_iso(),
        "mode": args.mode,
        "input_file": str(input_path.relative_to(PROJECT_DIR) if str(input_path).startswith(str(PROJECT_DIR)) else input_path),
        "question": args.question,
        "model": model_name,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "assistant": assistant,
    }

    append_jsonl(resolve_project_path(args.jsonl), record)
    write_report(resolve_project_path(args.report), args.mode, args.input_file, args.question, record)

    print(f"Done. Report => {args.report}")
    print(f"Raw logs => {args.jsonl}")


if __name__ == "__main__":
    main()
