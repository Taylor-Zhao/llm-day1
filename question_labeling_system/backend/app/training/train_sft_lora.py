"""Fine-tune a chat model with the conversational JSONL exported by export_sft_dataset."""

from __future__ import annotations

import argparse
import inspect
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALLOWED_ROLES = {"system", "user", "assistant"}


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def iter_jsonl_rows(path: str) -> Iterator[Dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"JSONL file not found: {source}")

    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {source} at line {line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"expected a JSON object in {source} at line {line_number}")
            if not isinstance(row.get("id"), str) or not row["id"].strip():
                raise ValueError(f"missing non-empty id in {source} at line {line_number}")
            messages = row.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError(f"missing non-empty messages in {source} at line {line_number}")

            roles = set()
            for message in messages:
                if not isinstance(message, dict):
                    raise ValueError(f"invalid message in {source} at line {line_number}")
                role = message.get("role")
                content = message.get("content")
                if role not in ALLOWED_ROLES or not isinstance(content, str):
                    raise ValueError(f"invalid role/content in {source} at line {line_number}")
                roles.add(role)
            if "user" not in roles or "assistant" not in roles:
                raise ValueError(f"messages must include user and assistant turns in {source} at line {line_number}")
            yield row


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return list(iter_jsonl_rows(str(path)))


def format_chat_row(row: Dict[str, Any], tokenizer: Any) -> Dict[str, str]:
    apply_chat_template = getattr(tokenizer, "apply_chat_template", None)
    if not callable(apply_chat_template):
        raise RuntimeError("the selected tokenizer does not support chat templates")
    try:
        text = apply_chat_template(row["messages"], tokenize=False, add_generation_prompt=False)
    except Exception as exc:
        raise RuntimeError(
            f"failed to apply the chat template for sample {row['id']!r}; "
            "choose an instruct/chat model with a compatible tokenizer template"
        ) from exc
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"chat template produced empty text for sample {row['id']!r}")
    return {"text": text}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LoRA/QLoRA fine-tuning for export_sft_dataset.py conversational JSONL"
    )
    parser.add_argument("--train-file", default="artifacts/sft_train.jsonl")
    parser.add_argument("--eval-file", default="artifacts/sft_eval.jsonl")
    parser.add_argument("--model-id", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--output-dir", default="artifacts/sft_lora")
    parser.add_argument("--max-seq-len", type=int, default=1024)
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--qlora", action="store_true", help="use 4-bit QLoRA when CUDA is available")
    return parser.parse_args()


def _load_training_dependencies() -> Dict[str, Any]:
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
        from trl import SFTConfig, SFTTrainer
    except Exception as exc:
        raise RuntimeError(
            "Missing SFT dependencies. From the llm-day1 root, run "
            "pip install -r requirements_day29_day31.txt"
        ) from exc
    return {
        "torch": torch,
        "Dataset": Dataset,
        "LoraConfig": LoraConfig,
        "AutoModelForCausalLM": AutoModelForCausalLM,
        "AutoTokenizer": AutoTokenizer,
        "BitsAndBytesConfig": BitsAndBytesConfig,
        "set_seed": set_seed,
        "SFTConfig": SFTConfig,
        "SFTTrainer": SFTTrainer,
    }


def _build_training_config(SFTConfig: Any, args: argparse.Namespace, output_dir: Path, torch: Any, use_qlora: bool) -> Any:
    config_parameters = inspect.signature(SFTConfig.__init__).parameters
    kwargs: Dict[str, Any] = {
        "output_dir": str(output_dir),
        "per_device_train_batch_size": args.batch_size,
        "per_device_eval_batch_size": args.batch_size,
        "gradient_accumulation_steps": args.grad_accum,
        "learning_rate": args.learning_rate,
        "num_train_epochs": args.epochs,
        "max_steps": args.max_steps,
        "logging_steps": 10,
        "save_strategy": "epoch",
        "bf16": bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported()),
        "fp16": bool(torch.cuda.is_available() and not torch.cuda.is_bf16_supported() and not use_qlora),
        "report_to": "none",
        "gradient_checkpointing": True,
        "remove_unused_columns": False,
        "dataloader_num_workers": 0,
        "packing": False,
    }
    if "eval_strategy" in config_parameters:
        kwargs["eval_strategy"] = "epoch"
    elif "evaluation_strategy" in config_parameters:
        kwargs["evaluation_strategy"] = "epoch"
    else:
        raise RuntimeError("installed TRL version has no evaluation strategy option")

    if "dataset_text_field" in config_parameters:
        kwargs["dataset_text_field"] = "text"
    if "max_length" in config_parameters:
        kwargs["max_length"] = args.max_seq_len
    elif "max_seq_length" in config_parameters:
        kwargs["max_seq_length"] = args.max_seq_len

    return SFTConfig(**kwargs)


def main() -> None:
    args = parse_args()
    if args.max_seq_len <= 0 or args.batch_size <= 0 or args.grad_accum <= 0:
        raise ValueError("max-seq-len, batch-size and grad-accum must be positive")
    if args.epochs <= 0 or args.lora_r <= 0 or args.lora_alpha <= 0:
        raise ValueError("epochs, lora-r and lora-alpha must be positive")

    train_path = resolve_path(args.train_file)
    eval_path = resolve_path(args.eval_file)
    if not train_path.is_file() or not eval_path.is_file():
        raise FileNotFoundError("both train and eval JSONL files must exist")

    dependencies = _load_training_dependencies()
    torch = dependencies["torch"]
    dependencies["set_seed"](args.seed)

    use_qlora = bool(args.qlora and torch.cuda.is_available())
    if args.qlora and not use_qlora:
        print("[warn] QLoRA requested but CUDA is unavailable; continuing with LoRA.")

    tokenizer = dependencies["AutoTokenizer"].from_pretrained(args.model_id, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    train_dataset = dependencies["Dataset"].from_generator(
        iter_jsonl_rows, gen_kwargs={"path": str(train_path)}
    )
    eval_dataset = dependencies["Dataset"].from_generator(
        iter_jsonl_rows, gen_kwargs={"path": str(eval_path)}
    )
    if len(train_dataset) == 0 or len(eval_dataset) == 0:
        raise ValueError("both train and eval JSONL files must contain examples")
    train_dataset = train_dataset.map(
        lambda row: format_chat_row(row, tokenizer),
        remove_columns=train_dataset.column_names,
        desc="Applying chat template to training messages",
    )
    eval_dataset = eval_dataset.map(
        lambda row: format_chat_row(row, tokenizer),
        remove_columns=eval_dataset.column_names,
        desc="Applying chat template to evaluation messages",
    )

    model_kwargs: Dict[str, Any] = {}
    if use_qlora:
        compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        model_kwargs["quantization_config"] = dependencies["BitsAndBytesConfig"](
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=compute_dtype,
        )
        model_kwargs["device_map"] = "auto"
    elif torch.cuda.is_available():
        model_kwargs["torch_dtype"] = (
            torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        )
        model_kwargs["device_map"] = "auto"

    model = dependencies["AutoModelForCausalLM"].from_pretrained(args.model_id, **model_kwargs)
    if hasattr(model, "config"):
        model.config.use_cache = False

    peft_config = dependencies["LoraConfig"](
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules="all-linear",
    )
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    training_config = _build_training_config(
        dependencies["SFTConfig"], args, output_dir, torch, use_qlora
    )

    trainer_kwargs: Dict[str, Any] = {
        "model": model,
        "train_dataset": train_dataset,
        "eval_dataset": eval_dataset,
        "peft_config": peft_config,
        "args": training_config,
    }
    trainer_parameters = inspect.signature(dependencies["SFTTrainer"].__init__).parameters
    if "processing_class" in trainer_parameters:
        trainer_kwargs["processing_class"] = tokenizer
    else:
        trainer_kwargs["tokenizer"] = tokenizer
    trainer = dependencies["SFTTrainer"](**trainer_kwargs)

    train_result = trainer.train()
    train_metrics = dict(train_result.metrics or {})
    eval_metrics = dict(trainer.evaluate() or {})

    adapter_dir = output_dir / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    trainer.model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(output_dir / "tokenizer"))
    summary = {
        "model_id": args.model_id,
        "train_file": str(resolve_path(args.train_file)),
        "eval_file": str(resolve_path(args.eval_file)),
        "train_examples": len(train_dataset),
        "eval_examples": len(eval_dataset),
        "method": "qlora" if use_qlora else "lora",
        "adapter_dir": str(adapter_dir),
        "train_metrics": train_metrics,
        "eval_metrics": eval_metrics,
    }
    (output_dir / "training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    main()