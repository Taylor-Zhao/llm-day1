#!/usr/bin/env python3
"""Day 35: aggregate Day29-Day34 outputs into a concise SFT experiment report.

教学阅读导向：
1) Day35 是“汇总层”，不再训练/推理，而是消费前几天的日志产物。
2) 核心目标是把 训练(31) + 质量(32) + 速度(33) 串成一份可交付报告。
3) 同时写入一条摘要 JSONL，便于后续做长期趋势看板。
"""

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from chat_cli import utc_now_iso
from run_day34_unified_inference_api import resolve_project_path

PROJECT_DIR = Path(__file__).resolve().parent


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    # 统一 JSONL 追加写：Day35 只写一条摘要记录，供后续长期追踪使用。
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    # 通用 JSONL 读取器：按行解析为字典列表。
    if not path.exists():
        raise FileNotFoundError(f"jsonl file not found: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def parse_args() -> argparse.Namespace:
    # 输入主要是 Day31/32/33 的日志路径，输出是 Day35 汇总报告与摘要日志路径。
    parser = argparse.ArgumentParser(description="Generate Day35 SFT experiment report")
    parser.add_argument("--day31-jsonl", default="logs/day31_sft_lora.jsonl", help="day31 summary jsonl")
    parser.add_argument("--day32-jsonl", default="logs/day32_sft_before_after_eval.jsonl", help="day32 detail jsonl")
    parser.add_argument("--day33-jsonl", default="logs/day33_inference_acceleration.jsonl", help="day33 metrics jsonl")
    parser.add_argument("--report", default="experiments/day35_sft_experiment_report.md", help="markdown report")
    parser.add_argument("--jsonl", default="logs/day35_sft_experiment_report.jsonl", help="summary jsonl")
    return parser.parse_args()


def summarize_day32(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # 对 Day32 逐样本评测明细做汇总：
    # - 计算 base/tuned 平均质量分
    # - 计算 tuned 胜出次数
    # - 给出平均提升 delta
    if not rows:
        return {"base_avg": 0.0, "tuned_avg": 0.0, "win_count": 0, "sample_count": 0}

    # Day32 日志是追加写入；按 index=1 作为一次新运行的起点，只汇总最后一次运行。
    # 这样可以避免“历史旧结果”污染本次报告结论。
    latest_start = 0
    for idx, row in enumerate(rows):
        if int(row.get("index") or 0) == 1:
            latest_start = idx
    rows = rows[latest_start:]

    base_total = 0.0
    tuned_total = 0.0
    win_count = 0
    for row in rows:
        base_score = float(row.get("base_quality") or 0.0)
        tuned_score = float(row.get("tuned_quality") or 0.0)
        base_total += base_score
        tuned_total += tuned_score
        if tuned_score > base_score:
            win_count += 1

    sample_count = len(rows)
    return {
        "base_avg": base_total / float(sample_count),
        "tuned_avg": tuned_total / float(sample_count),
        "delta": (tuned_total - base_total) / float(sample_count),
        "win_count": win_count,
        "sample_count": sample_count,
    }


def summarize_day33(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # 对 Day33 加速结果汇总：
    # 1) 同一 mode 可能多次运行，只保留最后一次
    # 2) 过滤掉 _skipped 项
    # 3) 选出 avg_seconds_per_sample 最小的最佳模式
    # 4) 基于 base_serial_fp32 计算 speedup
    # Day33 同一个 mode 可能多次运行，这里只保留每个 mode 最后一次结果。
    latest_by_mode: dict[str, dict[str, Any]] = {}
    for row in rows:
        mode = str(row.get("mode") or "")
        if not mode:
            continue
        latest_by_mode[mode] = row

    # _skipped 表示该模式在当前机器/环境不可用，不应参与最佳模式比较。
    benchmark_rows = [row for row in latest_by_mode.values() if not str(row.get("mode") or "").endswith("_skipped")]
    if not benchmark_rows:
        return {"best_mode": "N/A", "best_avg_seconds": 0.0}

    best_row = min(benchmark_rows, key=lambda row: float(row.get("avg_seconds_per_sample") or 0.0))
    baseline = next((row for row in benchmark_rows if row.get("mode") == "base_serial_fp32"), None)
    speedup = 0.0
    if baseline is not None:
        baseline_avg = float(baseline.get("avg_seconds_per_sample") or 0.0)
        best_avg = float(best_row.get("avg_seconds_per_sample") or 0.0)
        if best_avg > 0:
            speedup = baseline_avg / best_avg

    return {
        "best_mode": str(best_row.get("mode") or "N/A"),
        "best_avg_seconds": float(best_row.get("avg_seconds_per_sample") or 0.0),
        "speedup_vs_baseline": speedup,
    }


def build_report(
    *,
    day31_summary: dict[str, Any],
    day32_summary: dict[str, Any],
    day33_summary: dict[str, Any],
) -> str:
    # 报告目标：将 Day31(训练) + Day32(效果) + Day33(速度) 拼成可阅读的闭环结论。
    # 其中结论与局限是固定模板，指标值来自最新日志汇总结果。
    train_metrics = day31_summary.get("train_metrics") or {}
    eval_metrics = day31_summary.get("eval_metrics") or {}

    lines = [
        "# Day 35 - 微调实验报告（结论 + 局限）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- Day31 基础模型：{day31_summary.get('model_id') or 'N/A'}",
        f"- Day31 训练方式：{'QLoRA' if day31_summary.get('use_qlora') else 'LoRA'}",
        "",
        "## 1) 训练结果概览",
        "",
        f"- train_loss: {train_metrics.get('train_loss')}",
        f"- eval_loss: {eval_metrics.get('eval_loss')}",
        f"- eval_mean_token_accuracy: {eval_metrics.get('eval_mean_token_accuracy')}",
        "",
        "## 2) 微调前后效果（Day32）",
        "",
        f"- base_avg_quality: {day32_summary.get('base_avg'):.4f}",
        f"- tuned_avg_quality: {day32_summary.get('tuned_avg'):.4f}",
        f"- delta_quality: {day32_summary.get('delta'):+.4f}",
        f"- tuned_win_count: {day32_summary.get('win_count')}/{day32_summary.get('sample_count')}",
        "",
        "## 3) 推理加速结果（Day33）",
        "",
        f"- best_mode: {day33_summary.get('best_mode')}",
        f"- best_avg_seconds_per_sample: {day33_summary.get('best_avg_seconds'):.4f}",
        f"- speedup_vs_baseline: {day33_summary.get('speedup_vs_baseline'):.2f}x",
        "",
        "## 4) 结论",
        "",
        "1. Day31 证明 LoRA 训练流程已跑通，能够稳定产出 adapter。",
        "2. Day32 显示微调后模型在固定评测集上相对基础模型有可见提升。",
        "3. Day33 说明推理加速不能只看一种手段，批处理、量化、并发需要结合场景权衡。",
        "4. 整体上，Day29-Day35 已形成从概念、数据、训练、评测到推理优化的完整实验链路。",
        "",
        "## 5) 局限",
        "",
        "1. Day30 数据是模板化合成数据，不等同于真实线上问答分布。",
        "2. Day32 当前评分属于启发式规则，还不是人工标注或更强评测器。",
        "3. Day33 在本机 CPU/macOS 环境下验证的是轻量加速思路，不代表 GPU 生产环境结论。",
        "4. 当前样本规模较小，提升结果需要在更大评测集上继续验证。",
    ]
    return "\n".join(lines)


def main() -> None:
    # 教学视角主线：
    # Step 1 读取各阶段日志（输入）
    # Step 2 生成阶段摘要（中间态）
    # Step 3 产出最终 markdown 报告（面向人）
    # Step 4 写入摘要 jsonl（面向程序）
    # 主流程：读取三天日志 -> 分别汇总 -> 生成 Day35 报告 -> 记录一条摘要 JSONL。
    args = parse_args()
    day31_rows = read_jsonl(resolve_project_path(args.day31_jsonl))
    day32_rows = read_jsonl(resolve_project_path(args.day32_jsonl))
    day33_rows = read_jsonl(resolve_project_path(args.day33_jsonl))

    # Day31 通常是一轮一条 summary，取最后一条即可。
    # 若不存在日志，回退为空字典，报告中会显示 N/A，不会直接崩溃。
    day31_summary = day31_rows[-1] if day31_rows else {}
    day32_summary = summarize_day32(day32_rows)
    day33_summary = summarize_day33(day33_rows)

    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)
    # 先写 Markdown 报告供人读，再写 JSONL 摘要供程序消费。
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        build_report(day31_summary=day31_summary, day32_summary=day32_summary, day33_summary=day33_summary),
        encoding="utf-8",
    )

    append_jsonl(
        jsonl_path,
        {
            "timestamp_utc": utc_now_iso(),
            "phase": "day35_sft_experiment_report",
            "day31_model_id": day31_summary.get("model_id") or "",
            "day32_delta_quality": day32_summary.get("delta"),
            "day33_best_mode": day33_summary.get("best_mode"),
            "report": args.report,
        },
    )

    print("Done. Day35 SFT experiment report generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()
