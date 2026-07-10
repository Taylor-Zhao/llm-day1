#!/usr/bin/env python3
"""Day 33: compare inference acceleration strategies: quantization, batching, concurrency."""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from chat_cli import utc_now_iso
from run_day34_unified_inference_api import UnifiedInferenceEngine, build_backend_prompt, resolve_project_path

PROJECT_DIR = Path(__file__).resolve().parent


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"eval jsonl not found: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day33 inference acceleration comparison")
    parser.add_argument("--eval-file", default="data/day30_backend_sft_eval.jsonl", help="fixed eval set jsonl")
    parser.add_argument("--base-model-id", default="HuggingFaceTB/SmolLM2-135M-Instruct", help="base model id")
    parser.add_argument("--adapter-dir", default="outputs/day31_sft_lora/adapter", help="optional tuned adapter dir")
    parser.add_argument("--max-samples", type=int, default=8, help="number of prompts to benchmark")
    parser.add_argument("--batch-size", type=int, default=4, help="batch benchmark size")
    parser.add_argument("--concurrency-workers", type=int, default=2, help="worker count for concurrent benchmark")
    parser.add_argument("--max-new-tokens", type=int, default=180, help="generation max new tokens")
    parser.add_argument("--report", default="experiments/day33_inference_acceleration_report.md", help="markdown report")
    parser.add_argument("--jsonl", default="logs/day33_inference_acceleration.jsonl", help="jsonl metrics")
    return parser.parse_args()


def build_prompts(rows: list[dict[str, Any]]) -> list[str]:
    prompts: list[str] = []
    for row in rows:
        prompts.append(build_backend_prompt(str(row.get("instruction") or ""), str(row.get("input") or "")))
    return prompts


def benchmark_engine(
    mode_name: str,
    engine: UnifiedInferenceEngine,
    prompts: list[str],
    *,
    max_new_tokens: int,
    batch_size: int,
) -> dict[str, Any]:
    start = time.perf_counter()
    responses = engine.generate(prompts, max_new_tokens=max_new_tokens, batch_size=batch_size)
    elapsed = time.perf_counter() - start
    sample_count = max(1, len(prompts))
    return {
        "mode": mode_name,
        "sample_count": len(prompts),
        "batch_size": batch_size,
        "total_seconds": elapsed,
        "avg_seconds_per_sample": elapsed / float(sample_count),
        "throughput_samples_per_second": sample_count / elapsed if elapsed > 0 else 0.0,
        "response_preview": responses[0][:160] if responses else "",
    }


def benchmark_concurrent(
    *,
    base_model_id: str,
    prompts: list[str],
    worker_count: int,
    max_new_tokens: int,
) -> dict[str, Any]:
    shards = [prompts[i::worker_count] for i in range(worker_count)]
    engines = [UnifiedInferenceEngine(base_model_id=base_model_id, inference_mode="fp32").load() for _ in range(worker_count)]

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = []
        for idx, shard in enumerate(shards):
            if not shard:
                continue
            futures.append(executor.submit(engines[idx].generate, shard, max_new_tokens=max_new_tokens, batch_size=1))
        responses: list[str] = []
        for future in futures:
            responses.extend(future.result())
    elapsed = time.perf_counter() - start

    sample_count = max(1, len(prompts))
    return {
        "mode": f"base_concurrent_fp32_x{worker_count}",
        "sample_count": len(prompts),
        "batch_size": 1,
        "worker_count": worker_count,
        "total_seconds": elapsed,
        "avg_seconds_per_sample": elapsed / float(sample_count),
        "throughput_samples_per_second": sample_count / elapsed if elapsed > 0 else 0.0,
        "response_preview": responses[0][:160] if responses else "",
    }


def build_report(args: argparse.Namespace, rows: list[dict[str, Any]]) -> str:
    baseline = next((row for row in rows if row.get("mode") == "base_serial_fp32"), None)
    lines = [
        "# Day 33 - 推理加速对比报告",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 样本数：{args.max_samples}",
        f"- 基础模型：{args.base_model_id}",
        f"- batch_size：{args.batch_size}",
        f"- concurrency_workers：{args.concurrency_workers}",
        "",
        "## 对比结果",
        "",
        "| Mode | Total Seconds | Avg / Sample | Throughput |",
        "|---|---:|---:|---:|",
    ]

    best_mode = None
    best_avg = None
    for row in rows:
        lines.append(
            f"| {row['mode']} | {row['total_seconds']:.4f} | {row['avg_seconds_per_sample']:.4f} | {row['throughput_samples_per_second']:.4f} |"
        )
        avg_value = float(row["avg_seconds_per_sample"])
        if best_avg is None or avg_value < best_avg:
            best_avg = avg_value
            best_mode = str(row["mode"])

    lines.extend(["", "## 观察结论", ""])
    if baseline is not None:
        base_avg = float(baseline["avg_seconds_per_sample"])
        for row in rows:
            if row["mode"] == "base_serial_fp32":
                continue
            speedup = (base_avg / float(row["avg_seconds_per_sample"])) if float(row["avg_seconds_per_sample"]) > 0 else 0.0
            lines.append(f"- {row['mode']} 相对 base_serial_fp32 的单样本速度倍率：{speedup:.2f}x")

    lines.extend(
        [
            f"- 本次最快模式：{best_mode or 'N/A'}。",
            "- 动态量化主要用于 CPU 轻量实验；真实线上 GPU 场景可继续评估更成熟的量化方案。",
            "- 批处理更适合离线批评测；并发更适合多请求场景，但单机 CPU 上未必线性提速。",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    eval_rows = read_jsonl(resolve_project_path(args.eval_file))[: max(1, int(args.max_samples))]
    prompts = build_prompts(eval_rows)
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)

    results: list[dict[str, Any]] = []

    serial_engine = UnifiedInferenceEngine(base_model_id=args.base_model_id, inference_mode="fp32").load()
    results.append(
        benchmark_engine(
            "base_serial_fp32",
            serial_engine,
            prompts,
            max_new_tokens=args.max_new_tokens,
            batch_size=1,
        )
    )

    batch_engine = UnifiedInferenceEngine(base_model_id=args.base_model_id, inference_mode="fp32").load()
    results.append(
        benchmark_engine(
            "base_batch_fp32",
            batch_engine,
            prompts,
            max_new_tokens=args.max_new_tokens,
            batch_size=max(1, args.batch_size),
        )
    )

    try:
        quant_engine = UnifiedInferenceEngine(base_model_id=args.base_model_id, inference_mode="dynamic_int8").load()
        results.append(
            benchmark_engine(
                "base_serial_dynamic_int8",
                quant_engine,
                prompts,
                max_new_tokens=args.max_new_tokens,
                batch_size=1,
            )
        )
    except Exception as exc:
        results.append(
            {
                "mode": "base_serial_dynamic_int8_skipped",
                "sample_count": len(prompts),
                "batch_size": 1,
                "total_seconds": 0.0,
                "avg_seconds_per_sample": 0.0,
                "throughput_samples_per_second": 0.0,
                "response_preview": f"skipped: {exc}",
            }
        )

    if args.concurrency_workers > 1:
        results.append(
            benchmark_concurrent(
                base_model_id=args.base_model_id,
                prompts=prompts,
                worker_count=args.concurrency_workers,
                max_new_tokens=args.max_new_tokens,
            )
        )

    adapter_path = resolve_project_path(args.adapter_dir)
    if adapter_path.exists():
        tuned_engine = UnifiedInferenceEngine(base_model_id=args.base_model_id, adapter_dir=adapter_path, inference_mode="fp32").load()
        results.append(
            benchmark_engine(
                "tuned_serial_fp32",
                tuned_engine,
                prompts,
                max_new_tokens=args.max_new_tokens,
                batch_size=1,
            )
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(args, results), encoding="utf-8")

    for row in results:
        append_jsonl(
            jsonl_path,
            {
                "timestamp_utc": utc_now_iso(),
                "phase": "day33_inference_acceleration",
                **row,
            },
        )

    print("Done. Day33 inference acceleration report generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()
