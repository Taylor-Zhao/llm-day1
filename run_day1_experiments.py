#!/usr/bin/env python3
"""一键执行 Day 1 三组实验并产出对比报告。"""

# Python 语法提示：
# - 该脚本是“批处理模式”，不需要交互输入。
# - 通过 argparse 接收参数，通过 Path 管理文件路径。

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from chat_cli import build_client, chat_once, load_system_prompt


PROJECT_DIR = Path(__file__).resolve().parent
# `__file__` 是当前脚本路径；resolve().parent 得到脚本所在目录。
# 这样做可以避免“从别的工作目录启动时，相对路径找不到”的问题。


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_project_path(path_str: str) -> Path:
    # 绝对路径直接返回；相对路径则以项目目录为基准拼接。
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    # argparse.Namespace 可以理解为“参数对象”，例如 args.max_tokens。
    parser = argparse.ArgumentParser(description="Run Day 1 experiments")
    parser.add_argument(
        "--question",
        default="给我一个 Go HTTP 服务的错误日志排查清单，按优先级输出，并说明每一步预期现象。",
        help="固定问题（3组实验共用）",
    )
    parser.add_argument("--max-tokens", type=int, default=450, help="输出 token 上限")
    parser.add_argument(
        "--run3-prompt",
        default="prompts/system_prompt_v2_backend_cn.txt",
        help="第3组使用的系统提示词文件",
    )
    parser.add_argument(
        "--report",
        default="experiments/day1_run_results.md",
        help="Markdown 报告输出路径",
    )
    parser.add_argument(
        "--jsonl",
        default="logs/day1_experiments.jsonl",
        help="JSONL 原始结果输出路径",
    )
    return parser.parse_args()


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 追加写入 JSONL：一行一条记录，便于后续脚本处理。
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_report(path: Path, question: str, rows: list[dict]) -> None:
    # list[dict] 表示一个“字典列表”，每个字典对应一次实验结果。
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 1 实验结果（自动生成）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 固定问题：{question}",
        "",
    ]

    for i, row in enumerate(rows, start=1):
        # enumerate(..., start=1) 常用于“带序号遍历”。
        lines.extend(
            [
                f"## Run {i}",
                "",
                f"- 系统提示词：{row['system_prompt_file']}",
                f"- Temperature：{row['temperature']}",
                f"- Max tokens：{row['max_tokens']}",
                "",
                "### 输出",
                "",
                row["assistant"],
                "",
            ]
        )

    lines.extend(
        [
            "## 建议你补充的人工评分",
            "",
            "- 准确性（1-5）",
            "- 结构化程度（1-5）",
            "- 可执行性（1-5）",
            "",
        ]
    )

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    # 显式指定 .env 路径，避免 VS Code Debug 在不同 cwd 下加载失败。
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    api_key, base_url, client = build_client()

    runs = [
        {"temperature": 0.2, "system_prompt_file": "prompts/system_prompt_v1.txt"},
        {"temperature": 0.7, "system_prompt_file": "prompts/system_prompt_v1.txt"},
        {"temperature": 0.3, "system_prompt_file": args.run3_prompt},
    ]
    # runs 是“实验配置列表”，每个元素描述一组实验参数。

    results: list[dict] = []
    for run in runs:
        # 每次循环执行一组实验：读取提示词 -> 调用模型 -> 写入日志。
        system_prompt_path = resolve_project_path(run["system_prompt_file"])
        system_prompt = load_system_prompt(system_prompt_path)
        model_name = (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
        assistant, usage = chat_once(
            client=client,
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            system_prompt=system_prompt,
            user_text=args.question,
            temperature=run["temperature"],
            max_tokens=args.max_tokens,
        )

        record = {
            "timestamp_utc": utc_now_iso(),
            "question": args.question,
            "model": model_name,
            "temperature": run["temperature"],
            "max_tokens": args.max_tokens,
            "system_prompt_file": str(system_prompt_path.relative_to(PROJECT_DIR)),
            "assistant": assistant,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
        }
        append_jsonl(resolve_project_path(args.jsonl), record)
        results.append(record)

    # 全部实验跑完后，再统一生成可读报告。
    write_report(resolve_project_path(args.report), args.question, results)
    print(f"Done. Report => {args.report}")
    print(f"Raw logs => {args.jsonl}")


if __name__ == "__main__":
    main()
