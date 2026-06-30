#!/usr/bin/env python3
"""Day 2：批量对比 temperature / max_tokens 对输出的影响。"""

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from chat_cli import build_client, chat_once, load_system_prompt


PROJECT_DIR = Path(__file__).resolve().parent


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day 2 parameter sweep")
    parser.add_argument(
        "--question",
        default="给我一个 Go HTTP 服务的错误日志排查清单，按优先级输出，并说明每一步预期现象。",
        help="固定问题，所有参数组合共用",
    )
    parser.add_argument(
        "--prompt",
        default="prompts/system_prompt_v2_backend_cn.txt",
        help="统一使用的系统提示词文件",
    )
    parser.add_argument(
        "--temperatures",
        default="0.1,0.3,0.7,1.0",
        help="逗号分隔的 temperature 列表",
    )
    parser.add_argument(
        "--max-tokens-list",
        default="150,300,450",
        help="逗号分隔的 max_tokens 列表",
    )
    parser.add_argument(
        "--report",
        default="experiments/day2_parameter_sweep.md",
        help="Markdown 报告输出路径",
    )
    parser.add_argument(
        "--jsonl",
        default="logs/day2_parameter_sweep.jsonl",
        help="JSONL 原始结果输出路径",
    )
    parser.add_argument(
        "--csv",
        default="experiments/day2_parameter_sweep.csv",
        help="CSV 摘要输出路径",
    )
    return parser.parse_args()


def parse_float_list(raw: str) -> list[float]:
    return [float(item.strip()) for item in raw.split(",") if item.strip()]


def parse_int_list(raw: str) -> list[int]:
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "model",
        "temperature",
        "max_tokens",
        "latency_seconds",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "output_chars",
        "output_lines",
        "system_prompt_file",
    ]
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fieldnames})


def write_report(path: Path, question: str, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 2 参数实验报告（自动生成）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 固定问题：{question}",
        "",
        "## 摘要表",
        "",
        "| Run | Temperature | Max Tokens | Latency(s) | Input Tokens | Output Tokens | Total Tokens | 输出字符数 | 输出行数 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for row in rows:
        lines.append(
            f"| {row['run_id']} | {row['temperature']} | {row['max_tokens']} | {row['latency_seconds']:.2f} | {row['input_tokens']} | {row['output_tokens']} | {row['total_tokens']} | {row['output_chars']} | {row['output_lines']} |"
        )

    lines.extend([
        "",
        "## 逐组输出",
        "",
    ])

    for row in rows:
        lines.extend(
            [
                f"### Run {row['run_id']}",
                "",
                f"- Temperature：{row['temperature']}",
                f"- Max tokens：{row['max_tokens']}",
                f"- Latency：{row['latency_seconds']:.2f}s",
                f"- Input tokens：{row['input_tokens']}",
                f"- Output tokens：{row['output_tokens']}",
                f"- Total tokens：{row['total_tokens']}",
                f"- 输出字符数：{row['output_chars']}",
                f"- 输出行数：{row['output_lines']}",
                "",
                row["assistant"],
                "",
            ]
        )

    lines.extend(
        [
            "## 建议你人工观察的点",
            "",
            "- temperature 升高后，是否更发散、更容易跑偏",
            "- max_tokens 增加后，是否更完整，还是只是更啰嗦",
            "- 哪组最适合你的后端排障场景",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    temperatures = parse_float_list(args.temperatures)
    max_tokens_list = parse_int_list(args.max_tokens_list)
    prompt_path = resolve_project_path(args.prompt)
    system_prompt = load_system_prompt(prompt_path)
    model_name = (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"

    api_key, base_url, client = build_client()
    rows: list[dict] = []
    run_id = 1

    for temperature in temperatures:
        for max_tokens in max_tokens_list:
            start_time = perf_counter()
            assistant, usage = chat_once(
                client=client,
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                system_prompt=system_prompt,
                user_text=args.question,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency_seconds = perf_counter() - start_time

            record = {
                "run_id": run_id,
                "timestamp_utc": utc_now_iso(),
                "question": args.question,
                "model": model_name,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "latency_seconds": round(latency_seconds, 4),
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_tokens": usage["total_tokens"],
                "output_chars": len(assistant),
                "output_lines": len(assistant.splitlines()) if assistant else 0,
                "system_prompt_file": str(prompt_path.relative_to(PROJECT_DIR)),
                "assistant": assistant,
            }
            append_jsonl(resolve_project_path(args.jsonl), record)
            rows.append(record)
            run_id += 1

    write_csv(resolve_project_path(args.csv), rows)
    write_report(resolve_project_path(args.report), args.question, rows)
    print(f"Done. Report => {args.report}")
    print(f"Summary CSV => {args.csv}")
    print(f"Raw logs => {args.jsonl}")


if __name__ == "__main__":
    main()