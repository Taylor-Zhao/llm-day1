#!/usr/bin/env python3
"""Day 21：RAG V2 发布（指标面板 + 评测报告）。

输入：
- Day18 / Day19 / Day20 的 CSV 汇总

输出：
- Markdown 报告：experiments/day21_rag_v2_release_report.md
- JSON 摘要：logs/day21_rag_v2_release_summary.json
"""

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from run_day8_chunking_experiment import utc_now_iso

PROJECT_DIR = Path(__file__).resolve().parent


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day21 RAG V2 release report")
    parser.add_argument(
        "--day18-csv",
        default="experiments/day18_hybrid_retrieval_comparison.csv",
        help="Day18 对比 CSV",
    )
    parser.add_argument(
        "--day19-csv",
        default="experiments/day19_cache_dedup_comparison_full_20260706.csv",
        help="Day19 对比 CSV",
    )
    parser.add_argument(
        "--day20-csv",
        default="experiments/day20_latency_cost_analysis_full_20260706.csv",
        help="Day20 对比 CSV",
    )
    parser.add_argument(
        "--target-mode",
        default="cache_dedup",
        choices=["baseline", "cache_dedup"],
        help="发布候选模式",
    )

    parser.add_argument("--min-retrieval-hit-rate", type=float, default=0.85)
    parser.add_argument("--min-citation-correct-rate", type=float, default=0.60)
    parser.add_argument("--min-citation-format-valid-rate", type=float, default=0.65)
    parser.add_argument("--min-insufficient-correct-rate", type=float, default=0.50)
    parser.add_argument("--max-qa-total-tokens-avg", type=float, default=680.0)
    parser.add_argument("--max-end-to-end-ms-avg", type=float, default=10000.0)
    parser.add_argument("--max-end-to-end-ms-p95", type=float, default=17000.0)

    parser.add_argument(
        "--report",
        default="experiments/day21_rag_v2_release_report.md",
        help="发布报告 Markdown",
    )
    parser.add_argument(
        "--summary-json",
        default="logs/day21_rag_v2_release_summary.json",
        help="发布摘要 JSON",
    )
    return parser.parse_args()


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def row_by_mode(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    for row in rows:
        if str(row.get("mode") or "").strip() == mode:
            return row
    raise ValueError(f"mode '{mode}' not found in CSV")


def as_float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    val = row.get(key)
    if val is None or val == "":
        return default
    return float(val)


def as_int(row: dict[str, Any], key: str, default: int = 0) -> int:
    val = row.get(key)
    if val is None or val == "":
        return default
    return int(float(val))


def bool_gate(actual: float, target: float, op: str) -> bool:
    if op == ">=":
        return actual >= target
    if op == "<=":
        return actual <= target
    raise ValueError(f"Unsupported op: {op}")


def gate_line(name: str, actual: float, target: float, op: str) -> dict[str, Any]:
    passed = bool_gate(actual, target, op)
    return {
        "name": name,
        "actual": actual,
        "target": target,
        "op": op,
        "passed": passed,
    }


def format_gate_mark(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def write_report(
    path: Path,
    args: argparse.Namespace,
    panel_rows: list[dict[str, Any]],
    gates: list[dict[str, Any]],
    decision: str,
    notes: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Day 21 RAG V2 发布报告（指标面板 + 评测门禁）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 发布候选模式：{args.target_mode}",
        f"- Day18 CSV：{args.day18_csv}",
        f"- Day19 CSV：{args.day19_csv}",
        f"- Day20 CSV：{args.day20_csv}",
        "",
        "## 发布结论",
        "",
        f"- 决策：**{decision}**",
        "",
        "## 指标面板",
        "",
        "| 指标 | Day18 (hybrid) | Day19 (cache_dedup) | Day20 (target) |",
        "|---|---:|---:|---:|",
    ]

    for row in panel_rows:
        lines.append(
            f"| {row['name']} | {row['day18']:.3f} | {row['day19']:.3f} | {row['day20']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## 发布门禁",
            "",
            "| 门禁项 | 实际值 | 目标 | 结果 |",
            "|---|---:|---:|---|",
        ]
    )

    for gate in gates:
        lines.append(
            f"| {gate['name']} | {gate['actual']:.3f} | {gate['op']} {gate['target']:.3f} | {format_gate_mark(gate['passed'])} |"
        )

    lines.extend(["", "## 备注", ""])
    for note in notes:
        lines.append(f"- {note}")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    day18_csv = resolve_project_path(args.day18_csv)
    day19_csv = resolve_project_path(args.day19_csv)
    day20_csv = resolve_project_path(args.day20_csv)
    report_file = resolve_project_path(args.report)
    summary_json_file = resolve_project_path(args.summary_json)

    day18_rows = read_csv_rows(day18_csv)
    day19_rows = read_csv_rows(day19_csv)
    day20_rows = read_csv_rows(day20_csv)

    day18_hybrid = row_by_mode(day18_rows, "hybrid")
    day19_target = row_by_mode(day19_rows, args.target_mode)
    day20_target = row_by_mode(day20_rows, args.target_mode)

    panel_rows = [
        {
            "name": "retrieval_hit_rate",
            "day18": as_float(day18_hybrid, "retrieval_hit_rate"),
            "day19": as_float(day19_target, "retrieval_hit_rate"),
            "day20": as_float(day20_target, "retrieval_hit_rate"),
        },
        {
            "name": "citation_correct_rate",
            "day18": as_float(day18_hybrid, "citation_correct_rate"),
            "day19": as_float(day19_target, "citation_correct_rate"),
            "day20": as_float(day20_target, "citation_correct_rate"),
        },
        {
            "name": "citation_format_valid_rate",
            "day18": as_float(day18_hybrid, "citation_format_valid_rate"),
            "day19": as_float(day19_target, "citation_format_valid_rate"),
            "day20": as_float(day20_target, "citation_format_valid_rate"),
        },
        {
            "name": "insufficient_correct_rate",
            "day18": as_float(day18_hybrid, "insufficient_correct_rate"),
            "day19": as_float(day19_target, "insufficient_correct_rate"),
            "day20": as_float(day20_target, "insufficient_correct_rate"),
        },
        {
            "name": "qa_total_tokens_avg",
            "day18": as_float(day18_hybrid, "avg_total_tokens"),
            "day19": as_float(day19_target, "avg_total_tokens"),
            "day20": as_float(day20_target, "qa_total_tokens_avg"),
        },
        {
            "name": "end_to_end_ms_avg",
            "day18": 0.0,
            "day19": as_float(day19_target, "avg_elapsed_ms"),
            "day20": as_float(day20_target, "end_to_end_ms_avg"),
        },
        {
            "name": "end_to_end_ms_p95",
            "day18": 0.0,
            "day19": 0.0,
            "day20": as_float(day20_target, "end_to_end_ms_p95"),
        },
    ]

    gates = [
        gate_line(
            "retrieval_hit_rate",
            as_float(day20_target, "retrieval_hit_rate"),
            args.min_retrieval_hit_rate,
            ">=",
        ),
        gate_line(
            "citation_correct_rate",
            as_float(day20_target, "citation_correct_rate"),
            args.min_citation_correct_rate,
            ">=",
        ),
        gate_line(
            "citation_format_valid_rate",
            as_float(day20_target, "citation_format_valid_rate"),
            args.min_citation_format_valid_rate,
            ">=",
        ),
        gate_line(
            "insufficient_correct_rate",
            as_float(day20_target, "insufficient_correct_rate"),
            args.min_insufficient_correct_rate,
            ">=",
        ),
        gate_line(
            "qa_total_tokens_avg",
            as_float(day20_target, "qa_total_tokens_avg"),
            args.max_qa_total_tokens_avg,
            "<=",
        ),
        gate_line(
            "end_to_end_ms_avg",
            as_float(day20_target, "end_to_end_ms_avg"),
            args.max_end_to_end_ms_avg,
            "<=",
        ),
        gate_line(
            "end_to_end_ms_p95",
            as_float(day20_target, "end_to_end_ms_p95"),
            args.max_end_to_end_ms_p95,
            "<=",
        ),
    ]

    decision = "GO" if all(g["passed"] for g in gates) else "NO-GO"

    notes = [
        "Day20 的 token 为 QA usage 精确值；embedding 成本为请求次数+耗时估算。",
        f"当前 target mode={args.target_mode}，query_count={as_int(day20_target, 'query_count')}。",
        "若 insufficient_correct_rate 低于阈值，建议先优化拒答策略再发布。",
    ]

    write_report(report_file, args, panel_rows, gates, decision, notes)

    summary_payload = {
        "generated_utc": utc_now_iso(),
        "decision": decision,
        "target_mode": args.target_mode,
        "inputs": {
            "day18_csv": str(args.day18_csv),
            "day19_csv": str(args.day19_csv),
            "day20_csv": str(args.day20_csv),
        },
        "target_metrics": {
            "retrieval_hit_rate": as_float(day20_target, "retrieval_hit_rate"),
            "citation_correct_rate": as_float(day20_target, "citation_correct_rate"),
            "citation_format_valid_rate": as_float(day20_target, "citation_format_valid_rate"),
            "insufficient_correct_rate": as_float(day20_target, "insufficient_correct_rate"),
            "qa_total_tokens_avg": as_float(day20_target, "qa_total_tokens_avg"),
            "end_to_end_ms_avg": as_float(day20_target, "end_to_end_ms_avg"),
            "end_to_end_ms_p95": as_float(day20_target, "end_to_end_ms_p95"),
        },
        "gates": gates,
    }
    write_summary_json(summary_json_file, summary_payload)

    print("Done. Day21 RAG V2 release report generated.")
    print(f"Report => {args.report}")
    print(f"JSON   => {args.summary_json}")


if __name__ == "__main__":
    main()
