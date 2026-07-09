#!/usr/bin/env python3
"""Day 30: build 50-200 instruction samples for backend SFT."""

import argparse
import json
import random
from pathlib import Path
from typing import Any

from chat_cli import utc_now_iso

PROJECT_DIR = Path(__file__).resolve().parent

CATEGORIES = [
    "api_design",
    "sql_optimization",
    "incident_response",
    "cache_strategy",
    "message_queue",
    "observability",
    "security",
    "deployment",
    "concurrency",
    "cost_optimization",
]

INSTRUCTION_TEMPLATES = [
    "请针对{service}的{problem}给出可执行排查步骤。",
    "你是后端专家，请为{service}设计{feature}的实施方案。",
    "请分析{service}中{problem}的根因，并给出修复建议。",
    "请输出{service}在{traffic}场景下的优化清单。",
    "请给出{service}接入{component}时的风险与规避策略。",
]

SERVICES = [
    "订单服务",
    "支付服务",
    "库存服务",
    "用户服务",
    "网关服务",
    "消息分发服务",
    "结算服务",
    "推荐服务",
]

PROBLEMS = [
    "接口超时",
    "数据库慢查询",
    "缓存击穿",
    "消息堆积",
    "线程池耗尽",
    "CPU 飙升",
    "连接池耗尽",
    "幂等冲突",
]

FEATURES = [
    "幂等写入",
    "灰度发布",
    "限流熔断",
    "异步补偿",
    "多级缓存",
    "审计日志",
    "热点隔离",
    "降级开关",
]

TRAFFIC = [
    "大促高峰",
    "日常稳定流量",
    "夜间批处理",
    "突发活动流量",
]

COMPONENTS = [
    "Redis",
    "Kafka",
    "MySQL",
    "OpenTelemetry",
    "Nginx",
    "Prometheus",
]

OUTPUT_SKELETON = (
    "结论：{conclusion}\n"
    "分析：{analysis}\n"
    "操作步骤：\n"
    "1. {step1}\n"
    "2. {step2}\n"
    "3. {step3}\n"
    "风险与回滚：{risk}"
)


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Day30 backend instruction dataset")
    parser.add_argument("--samples", type=int, default=120, help="total sample count (50-200)")
    parser.add_argument("--eval-size", type=int, default=20, help="eval split size")
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    parser.add_argument("--output-train", default="data/day30_backend_sft_train.jsonl", help="train jsonl path")
    parser.add_argument("--output-eval", default="data/day30_backend_sft_eval.jsonl", help="eval jsonl path")
    parser.add_argument("--report", default="experiments/day30_instruction_dataset_report.md", help="markdown report")
    parser.add_argument("--jsonl", default="logs/day30_instruction_dataset.jsonl", help="run metadata jsonl")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    # 记录每次数据集构造任务的参数，方便追踪“样本规模 -> 训练效果”的关系。
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    # 训练/评测数据统一使用 JSONL，兼容 TRL/Datasets 的常见读取方式。
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_one_sample(rng: random.Random, idx: int) -> dict[str, Any]:
    # 模板化合成样本：保持结构稳定，降低小规模 SFT 的学习难度。
    service = rng.choice(SERVICES)
    problem = rng.choice(PROBLEMS)
    feature = rng.choice(FEATURES)
    traffic = rng.choice(TRAFFIC)
    component = rng.choice(COMPONENTS)
    template = rng.choice(INSTRUCTION_TEMPLATES)

    instruction = template.format(
        service=service,
        problem=problem,
        feature=feature,
        traffic=traffic,
        component=component,
    )

    category = rng.choice(CATEGORIES)
    difficulty = rng.choice(["easy", "medium", "hard"])

    output = OUTPUT_SKELETON.format(
        conclusion=f"建议优先通过可观测性定位 {problem} 的关键瓶颈，再实施分层优化。",
        analysis=f"{service} 在 {traffic} 场景下，{problem} 常由容量配置、热点访问或慢依赖导致。",
        step1=f"补齐 {component} 与应用指标，确认异常时间窗内的延迟、错误率、吞吐。",
        step2=f"按调用链逐层定位瓶颈，对数据库/缓存/队列做容量与参数调优。",
        step3=f"上线灰度策略并验证 SLA，保留回滚开关与限流阈值。",
        risk="注意放大流量可能触发级联故障，需预设熔断和回滚预案。",
    )

    return {
        "id": f"day30-{idx:04d}",
        "instruction": instruction,
        "input": "",
        "output": output,
        "category": category,
        "difficulty": difficulty,
        "tags": [service, problem, component],
    }


def build_report(
    *,
    train_rows: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> str:
    category_count: dict[str, int] = {}
    for row in train_rows + eval_rows:
        category = str(row.get("category") or "unknown")
        category_count[category] = category_count.get(category, 0) + 1

    lines = [
        "# Day 30 - 指令数据集构造报告",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 总样本数：{len(train_rows) + len(eval_rows)}",
        f"- 训练集：{len(train_rows)}",
        f"- 评测集：{len(eval_rows)}",
        f"- 随机种子：{args.seed}",
        "",
        "## 类别分布",
        "",
        "| Category | Count |",
        "|---|---:|",
    ]

    for category in sorted(category_count):
        lines.append(f"| {category} | {category_count[category]} |")

    lines.extend(["", "## 样本预览（前 3 条）", ""])
    for row in train_rows[:3]:
        lines.extend(
            [
                f"### {row['id']}",
                f"- instruction: {row['instruction']}",
                f"- output: {row['output'][:180]}...",
                "",
            ]
        )

    lines.extend(
        [
            "## 数据质量建议",
            "",
            "- 保持输出结构稳定（结论/分析/步骤/风险），便于 SFT 学习格式。",
            "- 同一问题尽量覆盖不同服务上下文，降低过拟合模板风险。",
            "- Day31 训练前建议抽样人工检查 10 条，剔除重复和空泛样本。",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    if args.samples < 50 or args.samples > 200:
        raise ValueError("--samples must be in [50, 200]")
    if args.eval_size < 10 or args.eval_size >= args.samples:
        raise ValueError("--eval-size must be >=10 and < samples")

    # 先全量生成再切分，保证 train/eval 使用同一分布且可复现。
    rng = random.Random(args.seed)
    rows = [build_one_sample(rng, i + 1) for i in range(args.samples)]
    rng.shuffle(rows)

    eval_rows = rows[: args.eval_size]
    train_rows = rows[args.eval_size :]

    train_path = resolve_project_path(args.output_train)
    eval_path = resolve_project_path(args.output_eval)
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)

    # 产出数据文件 + 报告 + 元信息日志，形成 Day30 的完整交付物。
    dump_jsonl(train_path, train_rows)
    dump_jsonl(eval_path, eval_rows)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_text = build_report(train_rows=train_rows, eval_rows=eval_rows, args=args)
    report_path.write_text(report_text, encoding="utf-8")

    append_jsonl(
        jsonl_path,
        {
            "timestamp_utc": utc_now_iso(),
            "phase": "day30_dataset",
            "samples": args.samples,
            "eval_size": args.eval_size,
            "seed": args.seed,
            "train_file": args.output_train,
            "eval_file": args.output_eval,
            "report": args.report,
        },
    )

    print("Done. Day30 dataset generated.")
    print(f"Train  => {args.output_train}")
    print(f"Eval   => {args.output_eval}")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()
