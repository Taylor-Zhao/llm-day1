#!/usr/bin/env python3
"""Day 34: unified local inference API for base / LoRA models.

教学阅读导向：
1) 这个脚本抽象了“本地推理引擎”UnifiedInferenceEngine。
2) 重点是统一入口：同一套 API 同时支持 base 模型、LoRA adapter、不同推理模式。
3) Day33 把它当 benchmark 后端，Day35 把它当实验链路中的可复用基础组件。
"""

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from chat_cli import utc_now_iso

PROJECT_DIR = Path(__file__).resolve().parent


def resolve_project_path(path_str: str) -> Path:
    # 支持相对路径与绝对路径：
    # - 绝对路径直接使用
    # - 相对路径默认相对于当前脚本目录（项目目录）
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    # 统一 JSONL 追加写，便于 Day35 做跨天聚合统计。
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_backend_prompt(instruction: str, user_input: str = "") -> str:
    # 构造统一的后端任务提示模板：
    # Instruction 放任务要求，Input 放上下文（可为空），Response 作为模型输出起始锚点。
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
        # 只保存配置，不在 __init__ 中加载大模型，避免创建对象时就产生重开销。
        self.base_model_id = base_model_id
        self.adapter_dir = adapter_dir
        self.inference_mode = inference_mode
        self.tokenizer = None
        self.model = None
        self.torch = None
        self.device = None

    def load(self) -> "UnifiedInferenceEngine":
        # 这里把“初始化依赖 + tokenizer + model + 可选 adapter/量化”一次性完成。
        # 调用者只关心 load() 后是否可 generate()，不用关心内部细节。
        # 运行时再导入深度学习依赖，便于在缺少依赖时给出明确报错信息。
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except Exception as exc:
            raise RuntimeError(
                "Missing inference dependencies. Install with: pip install -r requirements_day29_day31.txt"
            ) from exc

        self.torch = torch
        # tokenizer 负责分词/反分词；use_fast=True 优先使用 Rust fast tokenizer。
        self.tokenizer = AutoTokenizer.from_pretrained(self.base_model_id, use_fast=True)
        if self.tokenizer.pad_token is None:
            # 对于没有 pad_token 的模型，回退到 eos_token，避免 batch padding 报错。
            self.tokenizer.pad_token = self.tokenizer.eos_token
        # Decoder-only 模型做 batch 生成时应使用左填充，避免右填充影响生成对齐。
        self.tokenizer.padding_side = "left"

        # 这里固定加载 FP32，便于 Day33/Day34 的行为保持可解释一致。
        model = AutoModelForCausalLM.from_pretrained(self.base_model_id, dtype=torch.float32)

        if self.adapter_dir is not None:
            # 挂载 LoRA adapter：保持 base 权重不变，在其上叠加微调增量。
            from peft import PeftModel

            model = PeftModel.from_pretrained(model, str(self.adapter_dir))

        if self.inference_mode == "dynamic_int8":
            # CPU 下可用动态量化，适合做 Day33 的轻量量化实验。
            if self.adapter_dir is not None:
                raise ValueError("dynamic_int8 mode currently supports base model only")
            model = torch.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)

        self.model = model.eval()
        # 记录实际 device，供 generate 阶段把输入 tensor 移动到同一设备。
        self.device = self._infer_device(model)
        return self

    def _infer_device(self, model: Any):
        # 尽量稳健地推断模型所在设备：
        # 1) 优先直接读 model.device
        # 2) 否则读取首个参数的 device
        # 3) 再兜底到 CPU
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
        # generate 的职责是：
        # - 接受一批 prompt
        # - 按 batch_size 分批推理
        # - 对每条结果仅返回“新增 token 对应的文本”
        # 因而调用侧拿到的是纯回答，不含原始 prompt。
        # 懒加载：如果调用 generate 时尚未 load，则自动加载一次。
        if self.model is None or self.tokenizer is None or self.torch is None:
            self.load()

        responses: list[str] = []
        # temperature=0 走贪心解码；>0 走采样。
        do_sample = temperature > 0.0

        # 支持批处理：把 prompts 按 batch_size 切块，逐批推理。
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

            # 推理关闭梯度，减少显存/内存与计算开销。
            with self.torch.no_grad():
                outputs = self.model.generate(**generate_kwargs)

            # 关键点：只截取新增 token，避免把 prompt 本体重复解码出来。
            # 这也是很多“输出看起来重复输入”的常见修复点。
            input_lengths = attention_mask.sum(dim=1).tolist() if attention_mask is not None else [input_ids.shape[1]] * len(batch)
            for idx, prompt_len in enumerate(input_lengths):
                new_tokens = outputs[idx][int(prompt_len) :]
                text = self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
                responses.append(text)

        return responses


def parse_args() -> argparse.Namespace:
    # Day34 命令行侧重 API 演示参数：模型来源、模式、解码参数、输出路径。
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
    # 产出一份最小可追溯报告：记录输入参数与输出示例，方便回看与复现实验。
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
    # 教学视角主线：
    # Step 1 解析参数，确定模型来源和推理策略
    # Step 2 初始化统一引擎（隐藏底层加载细节）
    # Step 3 构造标准 prompt 并执行生成
    # Step 4 持久化结果（markdown + jsonl）
    # 主流程：解析参数 -> 初始化统一引擎 -> 生成一次回答 -> 写报告与日志。
    args = parse_args()
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)
    adapter_dir = resolve_project_path(args.adapter_dir) if args.adapter_dir.strip() else None

    engine = UnifiedInferenceEngine(
        base_model_id=args.base_model_id,
        adapter_dir=adapter_dir,
        inference_mode=args.inference_mode,
    ).load()

    # Day34 作为 API 演示，默认执行单请求；但 generate 内部支持 batch 扩展。
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
