#!/usr/bin/env python3
"""Day 4：后端场景回归评测（结构化质量门禁）。"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from chat_cli import build_client, chat_once

PROJECT_DIR = Path(__file__).resolve().parent
REQUIRED_SECTIONS = [
    "根因判断",
    "证据",
    "最小修复方案",
    "风险与回滚",
    "下一步验证",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day 4 regression eval")
    parser.add_argument("--cases-file", default="inputs/day4_eval_cases.json", help="评测样例 JSON 文件")
    parser.add_argument("--system-prompt", default="prompts/day4_incident_reviewer_cn.txt", help="系统提示词路径")
    parser.add_argument("--temperature", type=float, default=0.2, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=700, help="输出 token 上限")
    parser.add_argument("--model", default="", help="可选：覆盖环境变量中的模型名")
    parser.add_argument("--models", default="", help="可选：多模型对比，逗号分隔，例如 qwen2.5:0.5b,qwen2.5:3b")
    parser.add_argument("--retry-failed", type=int, default=1, help="失败样例的额外重试次数")
    parser.add_argument("--min-pass-rate", type=float, default=0.0, help="最低通过率门禁，低于该值时以非 0 退出")
    parser.add_argument("--report", default="experiments/day4_regression_eval.md", help="Markdown 报告路径")
    parser.add_argument("--csv", default="experiments/day4_regression_eval.csv", help="CSV 汇总路径")
    parser.add_argument("--jsonl", default="logs/day4_regression_eval.jsonl", help="JSONL 明细路径")
    return parser.parse_args()


def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_text(encoding="utf-8")


def load_cases(path: Path) -> list[dict]:
    data = json.loads(load_text(path))
    if not isinstance(data, list) or not data:
        raise ValueError("Cases file must be a non-empty JSON list")
    return data


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def section_score(answer: str) -> tuple[int, list[str]]:
    hit_sections: list[str] = []
    for title in REQUIRED_SECTIONS:
        if title in answer:
            hit_sections.append(title)
    return len(hit_sections), hit_sections


def keyword_score(answer: str, keywords: list[str]) -> tuple[int, list[str]]:
    hit_keywords: list[str] = []
    answer_lower = answer.lower()
    for keyword in keywords:
        if keyword.lower() in answer_lower:
            hit_keywords.append(keyword)
    return len(hit_keywords), hit_keywords


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "model",
        "case_name",
        "attempt",
        "is_retry",
        "pass",
        "section_hits",
        "section_total",
        "keyword_hits",
        "keyword_total",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def write_report(path: Path, args: argparse.Namespace, rows: list[dict], pass_rate: float, pass_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 4 回归评测报告（自动生成）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 样例文件：{args.cases_file}",
        f"- 系统提示词：{args.system_prompt}",
        f"- 模型：{args.model}",
        f"- 多模型：{args.models if args.models else '(未启用)'}",
        f"- 温度：{args.temperature}",
        f"- Max tokens：{args.max_tokens}",
        f"- 失败重试次数：{args.retry_failed}",
        f"- 最低通过率门禁：{args.min_pass_rate:.1%}",
        f"- 通过率：{pass_rate:.1%}",
        f"- 通过数：{pass_count}/{len(rows)}",
        "",
        "## 汇总",
        "",
        "| Model | Case | Attempt | Pass | Sections | Keywords | Latency(ms) | Tokens(in/out/total) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    for row in rows:
        lines.append(
            "| {model} | {case} | {attempt} | {ok} | {s_hit}/{s_all} | {k_hit}/{k_all} | {latency:.0f} | {i}/{o}/{t} |".format(
                model=row["model"],
                case=row["case_name"],
                attempt=row["attempt"],
                ok="YES" if row["pass"] else "NO",
                s_hit=row["section_hits"],
                s_all=row["section_total"],
                k_hit=row["keyword_hits"],
                k_all=row["keyword_total"],
                latency=row["latency_ms"],
                i=row["input_tokens"],
                o=row["output_tokens"],
                t=row["total_tokens"],
            )
        )

    lines.append("")
    lines.append("## 明细")
    lines.append("")

    for row in rows:
        lines.append(f"### {row['model']} / {row['case_name']} / attempt {row['attempt']}")
        lines.append("")
        lines.append(f"- 结果：{'PASS' if row['pass'] else 'FAIL'}")
        lines.append(f"- 是否重试：{'是' if row['is_retry'] else '否'}")
        lines.append(f"- 命中章节：{', '.join(row['hit_sections']) if row['hit_sections'] else '(无)'}")
        lines.append(f"- 命中关键词：{', '.join(row['hit_keywords']) if row['hit_keywords'] else '(无)'}")
        lines.append("")
        lines.append("模型输出：")
        lines.append("")
        lines.append(row["assistant"])
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def get_model_list(args: argparse.Namespace, env_model: str) -> list[str]:
    if args.models.strip():
        model_list = [item.strip() for item in args.models.split(",") if item.strip()]
        if model_list:
            return model_list
    single = args.model.strip() or env_model or "qwen2.5:0.5b"
    return [single]


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    cases_file = resolve_project_path(args.cases_file)
    system_prompt_file = resolve_project_path(args.system_prompt)
    report_file = resolve_project_path(args.report)
    csv_file = resolve_project_path(args.csv)
    jsonl_file = resolve_project_path(args.jsonl)

    cases = load_cases(cases_file)
    system_prompt = load_text(system_prompt_file)
    env_model = (os.getenv("OPENAI_MODEL") or "").strip()
    model_list = get_model_list(args, env_model)
    args.model = model_list[0] if len(model_list) == 1 else "(see --models)"
    args.models = ",".join(model_list) if len(model_list) > 1 else ""

    api_key, base_url, client = build_client()

    rows: list[dict] = []
    for model_name in model_list:
        for case in cases:
            case_name = case.get("name", "unnamed_case")
            task = case.get("task", "请分析问题并给出修复建议")
            input_text = case.get("input", "")
            must_have_keywords = case.get("must_have_keywords", [])
            if not isinstance(must_have_keywords, list):
                must_have_keywords = []

            user_text = (
                f"任务：{task}\n\n"
                "请严格按以下 5 个标题输出：根因判断、证据、最小修复方案、风险与回滚、下一步验证。\n\n"
                f"输入信息：\n{input_text}"
            )

            max_attempts = 1 + max(0, args.retry_failed)
            for attempt in range(1, max_attempts + 1):
                start = perf_counter()
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
                latency_ms = (perf_counter() - start) * 1000

                sec_hits, hit_sections = section_score(assistant)
                kw_hits, hit_keywords = keyword_score(assistant, must_have_keywords)

                # 通过标准：
                # 1) 结构化输出：5 个标题至少命中 4 个
                # 2) 关键证据覆盖：关键词命中率至少 50%
                sec_ok = sec_hits >= 4
                kw_ok = (kw_hits / len(must_have_keywords)) >= 0.5 if must_have_keywords else True
                passed = sec_ok and kw_ok

                row = {
                    "timestamp_utc": utc_now_iso(),
                    "model": model_name,
                    "case_name": case_name,
                    "attempt": attempt,
                    "is_retry": attempt > 1,
                    "pass": passed,
                    "section_hits": sec_hits,
                    "section_total": len(REQUIRED_SECTIONS),
                    "keyword_hits": kw_hits,
                    "keyword_total": len(must_have_keywords),
                    "hit_sections": hit_sections,
                    "hit_keywords": hit_keywords,
                    "latency_ms": round(latency_ms, 2),
                    "temperature": args.temperature,
                    "max_tokens": args.max_tokens,
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "total_tokens": usage["total_tokens"],
                    "assistant": assistant,
                }
                rows.append(row)
                append_jsonl(jsonl_file, row)

                # 非失败不重试；失败时继续下一次 attempt，并保留对比记录。
                if passed:
                    break

    pass_count = sum(1 for row in rows if row["pass"])
    pass_rate = pass_count / len(rows)

    write_csv(csv_file, rows)
    write_report(report_file, args, rows, pass_rate, pass_count)

    print(f"Done. Pass rate: {pass_rate:.1%} ({pass_count}/{len(rows)})")
    print(f"Report => {args.report}")
    print(f"CSV    => {args.csv}")
    print(f"JSONL  => {args.jsonl}")

    if pass_rate < args.min_pass_rate:
        print(
            "Quality gate failed: "
            f"pass rate {pass_rate:.1%} < min required {args.min_pass_rate:.1%}",
            file=sys.stderr,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()