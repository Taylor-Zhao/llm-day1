#!/usr/bin/env python3
"""Day 31: lightweight LoRA/QLoRA SFT run on a small open-source model."""

import argparse
import inspect
import json
import os
from pathlib import Path
from typing import Any, Optional

from chat_cli import utc_now_iso

PROJECT_DIR = Path(__file__).resolve().parent


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day31 lightweight SFT with LoRA/QLoRA")
    parser.add_argument("--train-file", default="data/day30_backend_sft_train.jsonl", help="train jsonl")
    parser.add_argument("--eval-file", default="data/day30_backend_sft_eval.jsonl", help="eval jsonl")
    parser.add_argument("--model-id", default="HuggingFaceTB/SmolLM2-135M-Instruct", help="base model id")
    parser.add_argument("--output-dir", default="outputs/day31_sft_lora", help="output dir")
    parser.add_argument("--report", default="experiments/day31_sft_lora_report.md", help="report path")
    parser.add_argument("--jsonl", default="logs/day31_sft_lora.jsonl", help="run metadata jsonl")
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    parser.add_argument("--max-seq-len", type=int, default=512, help="max sequence length")
    parser.add_argument("--epochs", type=float, default=1.0, help="num train epochs")
    parser.add_argument("--max-steps", type=int, default=80, help="max train steps, -1 means disabled")
    parser.add_argument("--batch-size", type=int, default=2, help="per-device train batch size")
    parser.add_argument("--grad-accum", type=int, default=8, help="gradient accumulation")
    parser.add_argument("--learning-rate", type=float, default=2e-4, help="learning rate")
    parser.add_argument("--lora-r", type=int, default=16, help="LoRA rank")
    parser.add_argument("--lora-alpha", type=int, default=32, help="LoRA alpha")
    parser.add_argument("--lora-dropout", type=float, default=0.05, help="LoRA dropout")
    parser.add_argument("--qlora", action="store_true", help="enable QLoRA 4-bit loading when possible")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    # 记录训练配置和核心指标，后续可直接用于 Day32 的前后对比。
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def format_example(row: dict[str, Any]) -> str:
    # 统一指令模板，减少数据格式噪声对小模型训练稳定性的影响。
    instruction = str(row.get("instruction") or "").strip()
    user_input = str(row.get("input") or "").strip()
    output = str(row.get("output") or "").strip()

    text = "### Instruction\n" + instruction + "\n\n"
    if user_input:
        text += "### Input\n" + user_input + "\n\n"
    text += "### Response\n" + output
    return text


def build_report(
    *,
    args: argparse.Namespace,
    use_qlora: bool,
    train_count: int,
    eval_count: int,
    train_metrics: dict[str, Any],
    eval_metrics: Optional[dict[str, Any]],
    output_dir: Path,
) -> str:
    lines = [
        "# Day 31 - 小规模 SFT 训练报告（LoRA/QLoRA）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 基础模型：{args.model_id}",
        f"- 训练方式：{'QLoRA(4-bit)' if use_qlora else 'LoRA'}",
        f"- 训练集样本数：{train_count}",
        f"- 评测集样本数：{eval_count}",
        f"- epochs：{args.epochs}",
        f"- max_steps：{args.max_steps}",
        f"- learning_rate：{args.learning_rate}",
        f"- LoRA(r/alpha/dropout)：{args.lora_r}/{args.lora_alpha}/{args.lora_dropout}",
        f"- 输出目录：{output_dir}",
        "",
        "## 训练指标",
        "",
    ]

    for key in sorted(train_metrics.keys()):
        lines.append(f"- train.{key}: {train_metrics[key]}")

    lines.extend(["", "## 评测指标", ""])
    if eval_metrics:
        for key in sorted(eval_metrics.keys()):
            lines.append(f"- eval.{key}: {eval_metrics[key]}")
    else:
        lines.append("- 本次未执行 eval（可能是 eval 数据为空或训练器未返回）。")

    lines.extend(
        [
            "",
            "## 结果说明",
            "",
            "- 本报告用于证明 Day31 的 SFT 流程可运行，不代表最终最优效果。",
            "- Day32 建议用固定评测集对比微调前后回答质量（格式遵循率/准确率/可执行性）。",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = parse_args()

    train_path = resolve_project_path(args.train_file)
    eval_path = resolve_project_path(args.eval_file)
    output_dir = resolve_project_path(args.output_dir)
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)

    # 这里拿到的还是“原始样本列表”，每条样本本质上是一个 dict：
    # {
    #   "instruction": "任务要求",
    #   "input": "补充上下文，可为空",
    #   "output": "期望答案"
    # }
    # 它们还不是训练器能直接消费的数据结构，后面还要格式化并转成 Dataset。
    train_rows = read_jsonl(train_path)
    eval_rows = read_jsonl(eval_path)
    if not train_rows:
        raise ValueError("train dataset is empty")

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
        from trl import SFTConfig, SFTTrainer
    except Exception as exc:
        raise RuntimeError(
            "Missing training dependencies. Install with: pip install -r requirements_day29_day31.txt"
        ) from exc

    set_seed(args.seed)

    # QLoRA 对 CUDA/量化后端依赖强，CPU 或非兼容环境下自动回退到 LoRA。
    use_qlora = bool(args.qlora)
    if use_qlora and not torch.cuda.is_available():
        print("[warn] --qlora requested but CUDA is unavailable. Fallback to LoRA.")
        use_qlora = False

    tokenizer = AutoTokenizer.from_pretrained(args.model_id, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = None
    model_kwargs: dict[str, Any] = {}
    if use_qlora:
        # 4-bit 量化配置：以更低显存占用加载基础模型权重。
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        )
        model_kwargs["quantization_config"] = quantization_config
        model_kwargs["device_map"] = "auto"
    elif torch.cuda.is_available():
        model_kwargs["torch_dtype"] = torch.bfloat16
        model_kwargs["device_map"] = "auto"

    model = AutoModelForCausalLM.from_pretrained(args.model_id, **model_kwargs)

    # train_ds: 训练集 Dataset。
    # 作用：提供给 SFTTrainer 在训练阶段反复采样，用来更新 LoRA 参数。
    # 关键点：
    # 1) 先对每条原始样本调用 format_example(row)
    # 2) 把 instruction / input / output 拼成一段完整监督文本
    # 3) 再包装成 {"text": ...} 结构，交给 Dataset.from_list
    #
    # 这样做之后，trainer 就能根据 dataset_text_field="text" 取出文本，
    # 再交给 tokenizer 切 token，最终用于计算语言建模损失。
    train_ds = Dataset.from_list([{"text": format_example(row)} for row in train_rows])

    # eval_ds: 评测集 Dataset。
    # 作用：提供给 SFTTrainer 在 evaluate 阶段计算评测指标。
    # 它和 train_ds 的数据格式完全一致，但用途不同：
    # - train_ds 用来“学习”
    # - eval_ds 用来“检查学得怎么样”
    #
    # eval_ds 不参与梯度更新，因此不会直接改变模型参数。
    # 它更像考试卷，而 train_ds 更像教材/练习题。
    eval_ds = Dataset.from_list([{"text": format_example(row)} for row in eval_rows])

    peft_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules="all-linear",
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    train_args_kwargs: dict[str, Any] = {
        "output_dir": str(output_dir),
        "per_device_train_batch_size": args.batch_size,
        "per_device_eval_batch_size": args.batch_size,
        "gradient_accumulation_steps": args.grad_accum,
        "learning_rate": args.learning_rate,
        "num_train_epochs": args.epochs,
        "max_steps": args.max_steps,
        "logging_steps": 10,
        "save_steps": 40,
        "eval_steps": 40,
        "save_strategy": "steps",
        "bf16": torch.cuda.is_available(),
        "fp16": False,
        "report_to": "none",
        "gradient_checkpointing": True,
        "remove_unused_columns": False,
        "dataloader_num_workers": 0,
        "dataset_text_field": "text",
        "max_length": args.max_seq_len,
        "packing": False,
    }

    # 兼容不同版本参数命名：有些版本用 evaluation_strategy，有些用 eval_strategy。
    ta_params = inspect.signature(SFTConfig.__init__).parameters
    if "evaluation_strategy" in ta_params:
        train_args_kwargs["evaluation_strategy"] = "steps"
    elif "eval_strategy" in ta_params:
        train_args_kwargs["eval_strategy"] = "steps"

    # 这里把一大组训练超参数打包成 SFTConfig，供 SFTTrainer 统一读取。
    training_args = SFTConfig(**train_args_kwargs)

    # SFTTrainer 可以理解成“训练总控器”：
    # - model: 要微调的基础模型
    # - processing_class=tokenizer: 负责把 text 转成 token
    # - train_dataset=train_ds: 训练时喂给模型的样本
    # - eval_dataset=eval_ds: 评测时用来算指标的样本
    # - peft_config=peft_config: 指定本次不是全参数训练，而是 LoRA 微调
    # - args=training_args: 训练批大小、学习率、评测步长等配置
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        peft_config=peft_config,
        args=training_args,
    )

    # 训练阶段会从 train_ds 中不断取样本，经过 tokenizer 编码后送入 model，
    # 再基于监督文本计算 loss，并仅更新 LoRA adapter 对应参数。
    # 这一步的返回值 train_result 中通常包含 train_loss、train_runtime 等统计信息。
    train_result = trainer.train()
    train_metrics = dict(train_result.metrics or {})

    eval_metrics: Optional[dict[str, Any]] = None
    if len(eval_rows) > 0:
        # evaluate() 使用的是 eval_ds，而不是 train_ds。
        # 它的作用是看看模型在“未参与参数更新的评测样本”上表现如何，
        # 从而避免只看训练集导致的过拟合假象。
        eval_result = trainer.evaluate()
        eval_metrics = dict(eval_result or {})

    adapter_dir = output_dir / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    trainer.model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(output_dir / "tokenizer"))

    report_text = build_report(
        args=args,
        use_qlora=use_qlora,
        train_count=len(train_rows),
        eval_count=len(eval_rows),
        train_metrics=train_metrics,
        eval_metrics=eval_metrics,
        output_dir=output_dir,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")

    append_jsonl(
        jsonl_path,
        {
            "timestamp_utc": utc_now_iso(),
            "phase": "day31_sft",
            "model_id": args.model_id,
            "train_file": args.train_file,
            "eval_file": args.eval_file,
            "use_qlora": use_qlora,
            "train_metrics": train_metrics,
            "eval_metrics": eval_metrics,
            "output_dir": args.output_dir,
            "report": args.report,
        },
    )

    print("Done. Day31 SFT finished.")
    print(f"Mode   => {'QLoRA' if use_qlora else 'LoRA'}")
    print(f"Output => {args.output_dir}")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    # Avoid tokenizer parallelism warning noise in small local runs.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    main()
