#!/usr/bin/env python3
"""Day 34: unified local inference API for base / LoRA models."""

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from chat_cli import utc_now_iso

PROJECT_DIR = Path(__file__).resolve().parent


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_backend_prompt(instruction: str, user_input: str = "") -> str:
    prompt = "你是后端工程助手，请给出结构化回答。\n\n"
    prompt += "### Instruction\n" + instruction.strip() + "\n\n"
    if user_input.strip():
        prompt += "### Input\n" + user_input.strip() + "\n\n"
    prompt += "### Response\n"
    return prompt


class UnifiedInferenceEngine:
    """统一封装基础模型、LoRA adapter 与不同推理模式。"""

    def __init__(
        self,
        *,
        base_model_id: str,
        adapter_dir: Optional[Path] = None,
        inference_mode: str = "fp32",
    ) -> None:
        self.base_model_id = base_model_id
        self.adapter_dir = adapter_dir
        self.inference_mode = inference_mode
        self.tokenizer = None
        self.model = None
        self.torch = None
        self.device = None

    def load(self) -> "UnifiedInferenceEngine":
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except Exception as exc:
            raise RuntimeError(
                "Missing inference dependencies. Install with: pip install -r requirements_day29_day31.txt"
            ) from exc

        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(self.base_model_id, use_fast=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        # Decoder-only 模型做 batch 生成时应使用左填充，避免右填充影响生成对齐。
        self.tokenizer.padding_side = "left"

        model = AutoModelForCausalLM.from_pretrained(self.base_model_id, dtype=torch.float32)

        if self.adapter_dir is not None:
            from peft import PeftModel

            model = PeftModel.from_pretrained(model, str(self.adapter_dir))

        if self.inference_mode == "dynamic_int8":
            # CPU 下可用动态量化，适合做 Day33 的轻量量化实验。
            if self.adapter_dir is not None:
                raise ValueError("dynamic_int8 mode currently supports base model only")
            model = torch.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)

        self.model = model.eval()
        self.device = self._infer_device(model)
        return self

    def _infer_device(self, model: Any):
        if hasattr(model, "device"):
            return model.device
        try:
            return next(model.parameters()).device
        except Exception:
            return self.torch.device("cpu") if self.torch is not None else "cpu"

    def generate(
        self,
        prompts: list[str],
        *,
        max_new_tokens: int = 220,
        temperature: float = 0.0,
        top_p: float = 1.0,
        batch_size: int = 1,
    ) -> list[str]:
        if self.model is None or self.tokenizer is None or self.torch is None:
            self.load()

        responses: list[str] = []
        do_sample = temperature > 0.0

        for start in range(0, len(prompts), max(1, batch_size)):
            batch = prompts[start : start + max(1, batch_size)]
            encoded = self.tokenizer(batch, return_tensors="pt", padding=True, truncation=True)
            input_ids = encoded["input_ids"].to(self.device)
            attention_mask = encoded.get("attention_mask")
            if attention_mask is not None:
                attention_mask = attention_mask.to(self.device)

            generate_kwargs: dict[str, Any] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "max_new_tokens": max_new_tokens,
                "do_sample": do_sample,
                "pad_token_id": self.tokenizer.pad_token_id,
                "eos_token_id": self.tokenizer.eos_token_id,
            }
            if do_sample:
                generate_kwargs["temperature"] = temperature
                generate_kwargs["top_p"] = top_p

            with self.torch.no_grad():
                outputs = self.model.generate(**generate_kwargs)

            input_lengths = attention_mask.sum(dim=1).tolist() if attention_mask is not None else [input_ids.shape[1]] * len(batch)
            for idx, prompt_len in enumerate(input_lengths):
                new_tokens = outputs[idx][int(prompt_len) :]
                text = self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
                responses.append(text)

        return responses


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day34 unified inference API demo")
    parser.add_argument("--base-model-id", default="HuggingFaceTB/SmolLM2-135M-Instruct", help="base model id")
    parser.add_argument("--adapter-dir", default="", help="optional LoRA adapter dir")
    parser.add_argument("--inference-mode", default="fp32", choices=["fp32", "dynamic_int8"], help="inference mode")
    parser.add_argument("--instruction", default="请给出订单服务接口超时的排查步骤。", help="backend prompt")
    parser.add_argument("--user-input", default="", help="optional extra input")
    parser.add_argument("--batch-size", type=int, default=1, help="batch size")
    parser.add_argument("--max-new-tokens", type=int, default=220, help="generation max new tokens")
    parser.add_argument("--temperature", type=float, default=0.0, help="temperature")
    parser.add_argument("--top-p", type=float, default=1.0, help="top-p")
    parser.add_argument("--report", default="experiments/day34_unified_inference_api.md", help="markdown report")
    parser.add_argument("--jsonl", default="logs/day34_unified_inference_api.jsonl", help="jsonl log")
    return parser.parse_args()


def build_report(args: argparse.Namespace, response_text: str, adapter_dir: Optional[Path]) -> str:
    lines = [
        "# Day 34 - 统一推理 API 报告",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- base_model_id：{args.base_model_id}",
        f"- adapter_dir：{adapter_dir if adapter_dir else '无'}",
        f"- inference_mode：{args.inference_mode}",
        f"- batch_size：{args.batch_size}",
        "",
        "## 请求示例",
        "",
        f"- instruction: {args.instruction}",
        f"- user_input: {args.user_input or '无'}",
        "",
        "## 响应示例",
        "",
        response_text,
        "",
        "## 说明",
        "",
        "- 这个脚本把模型加载、adapter 挂载、推理模式切换、batch 生成统一封装到一个 API 中。",
        "- Day33 可直接复用它做加速对比，Day35 可复用它扩展为统一实验接口。",
    ]
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)
    adapter_dir = resolve_project_path(args.adapter_dir) if args.adapter_dir.strip() else None

    engine = UnifiedInferenceEngine(
        base_model_id=args.base_model_id,
        adapter_dir=adapter_dir,
        inference_mode=args.inference_mode,
    ).load()

    prompt = build_backend_prompt(args.instruction, args.user_input)
    response_text = engine.generate(
        [prompt],
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        batch_size=args.batch_size,
    )[0]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_text = build_report(args, response_text, adapter_dir)
    report_path.write_text(report_text, encoding="utf-8")

    append_jsonl(
        jsonl_path,
        {
            "timestamp_utc": utc_now_iso(),
            "phase": "day34_unified_inference_api",
            "base_model_id": args.base_model_id,
            "adapter_dir": str(adapter_dir) if adapter_dir else "",
            "inference_mode": args.inference_mode,
            "instruction": args.instruction,
            "response_preview": response_text[:240],
        },
    )

    print("Done. Day34 unified inference API demo generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()
