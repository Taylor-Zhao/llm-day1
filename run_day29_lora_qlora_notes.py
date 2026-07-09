#!/usr/bin/env python3
"""Day 29: LoRA/QLoRA concept notes and experiment checklist."""

import argparse
import json
from pathlib import Path
from typing import Any

from chat_cli import utc_now_iso

PROJECT_DIR = Path(__file__).resolve().parent


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Day29 LoRA/QLoRA study notes")
    parser.add_argument("--report", default="experiments/day29_lora_qlora_notes.md", help="Markdown report path")
    parser.add_argument("--jsonl", default="logs/day29_lora_qlora_notes.jsonl", help="JSONL log path")
    parser.add_argument("--base-model-params-b", type=float, default=0.5, help="Base model params in billions")
    parser.add_argument("--lora-rank", type=int, default=16, help="LoRA rank r")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    # 统一 JSONL 记录格式，便于后续用脚本聚合实验元数据。
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_report(args: argparse.Namespace) -> str:
    # 这里只做量级估算，目的是帮助理解 LoRA/QLoRA 的资源差异，而不是精确显存计算。
    params = max(args.base_model_params_b, 0.1) * 1_000_000_000
    full_ft_memory_gb = params * 16 / (1024**3)
    qlora_weight_memory_gb = params * 0.5 / (1024**3)

    return "\n".join(
        [
            "# Day 29 - LoRA/QLoRA 学习笔记（轻量）",
            "",
            f"- 生成时间（UTC）：{utc_now_iso()}",
            f"- 参考模型规模：{args.base_model_params_b:.2f}B 参数",
            f"- LoRA rank（r）：{args.lora_rank}",
            "",
            "## 1) 核心概念",
            "",
            "- LoRA：冻结基础模型参数，仅训练低秩矩阵增量（A/B），大幅减少可训练参数量。",
            "- QLoRA：在 LoRA 基础上把基础模型权重量化到 4-bit（常见 NF4），进一步降低显存占用。",
            "- SFT：监督微调，通过指令-回答样本让模型更贴合特定任务风格。",
            "",
            "## 2) LoRA 与 QLoRA 对比（轻量视角）",
            "",
            "| 维度 | LoRA | QLoRA |",
            "|---|---|---|",
            "| 基础权重精度 | fp16/bf16 常见 | 4-bit 常见 |",
            "| 训练成本 | 中 | 更低 |",
            "| 硬件门槛 | GPU 推荐 | NVIDIA GPU 强依赖更明显 |",
            "| 适用场景 | 小规模领域适配 | 资源更紧张时优先 |",
            "",
            "## 3) 粗略显存认知（仅用于直觉）",
            "",
            f"- 若直接全参数训练，参数+梯度+优化器状态粗略量级可达约 {full_ft_memory_gb:.2f} GB（理论量级估算）。",
            f"- QLoRA 基础权重存储量级约 {qlora_weight_memory_gb:.2f} GB（不含激活与其他开销）。",
            "- 结论：LoRA/QLoRA 的价值主要来自‘少训参数 + 低精度存储’。",
            "",
            "## 4) Day30 数据准备原则",
            "",
            "- 样本量目标：50-200 条，先保证质量与格式一致性，再追求数量。",
            "- 场景聚焦：接口设计、SQL、缓存、消息队列、故障排查、可观测性。",
            "- 输出风格统一：建议固定结构（结论 -> 分析 -> 操作步骤 -> 风险）。",
            "",
            "## 5) Day31 小规模 SFT 执行要点",
            "",
            "- 先用小模型 + 1 epoch 验证流程可跑通。",
            "- 固定评测集（不参与训练）作为 Day32 对比基线。",
            "- 保存 adapter 与训练配置，确保可复现实验。",
        ]
    )


def main() -> None:
    args = parse_args()
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)

    # Day29 产出两个文件：人类可读报告 + 机器可读运行记录。
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_text = build_report(args)
    report_path.write_text(report_text, encoding="utf-8")

    append_jsonl(
        jsonl_path,
        {
            "timestamp_utc": utc_now_iso(),
            "phase": "day29_notes",
            "report": str(report_path),
            "base_model_params_b": args.base_model_params_b,
            "lora_rank": args.lora_rank,
        },
    )

    print("Done. Day29 notes generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()
