#!/usr/bin/env python3
"""Day 5：接口文档生成器（根据函数签名产出 API 文档草稿）。"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day 5 API doc generator")
    parser.add_argument("--input-file", default="inputs/day5_function_signatures.txt", help="函数签名输入文件")
    parser.add_argument("--system-prompt", default="prompts/day5_api_doc_writer_cn.txt", help="系统提示词路径")
    parser.add_argument("--question", default="请按接口逐个输出 API 文档草稿", help="附加指令")
    parser.add_argument("--temperature", type=float, default=0.2, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=900, help="输出 token 上限")
    parser.add_argument("--model", default="", help="可选：覆盖环境变量中的模型名")
    parser.add_argument("--output", default="experiments/day5_api_doc_draft.md", help="Markdown 输出路径")
    parser.add_argument("--jsonl", default="logs/day5_api_doc_generator.jsonl", help="JSONL 记录路径")
    return parser.parse_args()


def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_text(encoding="utf-8")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_markdown(path: Path, input_file: str, model: str, question: str, assistant: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 5 API 文档草稿（自动生成）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 输入文件：{input_file}",
        f"- 模型：{model}",
        f"- 附加指令：{question}",
        "",
        "## 文档草稿",
        "",
        assistant,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    input_file = resolve_project_path(args.input_file)
    prompt_file = resolve_project_path(args.system_prompt)
    output_file = resolve_project_path(args.output)
    jsonl_file = resolve_project_path(args.jsonl)

    signatures_text = load_text(input_file)
    system_prompt = load_text(prompt_file)
    model_name = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"

    user_text = (
        args.question.strip()
        + "\n\n请基于以下函数签名和路由信息生成 API 文档草稿：\n\n"
        + signatures_text
    )

    api_key, base_url, client = build_client()
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

    write_markdown(output_file, args.input_file, model_name, args.question, assistant)

    record = {
        "timestamp_utc": utc_now_iso(),
        "input_file": args.input_file,
        "system_prompt": args.system_prompt,
        "model": model_name,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "output_file": args.output,
    }
    append_jsonl(jsonl_file, record)

    print("Done. API doc draft generated.")
    print(f"Output => {args.output}")
    print(f"Raw logs => {args.jsonl}")


if __name__ == "__main__":
    main()