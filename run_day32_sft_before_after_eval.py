#!/usr/bin/env python3
"""Day 32: compare base model vs LoRA-adapted model on a fixed eval set."""

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional

from chat_cli import utc_now_iso

PROJECT_DIR = Path(__file__).resolve().parent

REQUIRED_SECTIONS = ["结论", "分析", "操作步骤", "风险"]


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day32 before/after SFT comparison")
    parser.add_argument("--eval-file", default="data/day30_backend_sft_eval.jsonl", help="fixed eval set jsonl")
    parser.add_argument("--base-model-id", default="HuggingFaceTB/SmolLM2-135M-Instruct", help="base model id")
    parser.add_argument("--adapter-dir", default="outputs/day31_sft_lora/adapter", help="LoRA adapter dir")
    parser.add_argument("--max-new-tokens", type=int, default=220, help="generation max new tokens")
    parser.add_argument("--temperature", type=float, default=0.0, help="generation temperature")
    parser.add_argument("--top-p", type=float, default=1.0, help="generation top-p")
    parser.add_argument("--max-eval-samples", type=int, default=20, help="max samples to evaluate")
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    parser.add_argument("--report", default="experiments/day32_sft_before_after_eval.md", help="markdown report")
    parser.add_argument("--jsonl", default="logs/day32_sft_before_after_eval.jsonl", help="detail jsonl")
    return parser.parse_args()


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


def build_prompt(row: dict[str, Any]) -> str:
    instruction = str(row.get("instruction") or "").strip()
    user_input = str(row.get("input") or "").strip()
    text = "你是后端工程助手，请给出结构化回答。\n\n"
    text += "### Instruction\n" + instruction + "\n\n"
    if user_input:
        text += "### Input\n" + user_input + "\n\n"
    text += "### Response\n"
    return text


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def section_score(answer: str) -> float:
    normalized = normalize_text(answer)
    hits = 0
    for section in REQUIRED_SECTIONS:
        if normalize_text(section) in normalized:
            hits += 1
    return hits / float(len(REQUIRED_SECTIONS))


def keyword_hit_ratio(answer: str, row: dict[str, Any]) -> float:
    tags = row.get("tags") or []
    keywords = [str(t).strip() for t in tags if str(t).strip()]
    if not keywords:
        return 0.0
    answer_n = normalize_text(answer)
    hits = 0
    for kw in keywords:
        if normalize_text(kw) in answer_n:
            hits += 1
    return hits / float(len(keywords))


def quality_score(answer: str, row: dict[str, Any]) -> float:
    # 强调格式遵循（60%）+ 关键信息覆盖（40%）
    return 0.6 * section_score(answer) + 0.4 * keyword_hit_ratio(answer, row)


def generate_response(model: Any, tokenizer: Any, row: dict[str, Any], args: argparse.Namespace) -> str:
    prompt = build_prompt(row)
    encoded = tokenizer(prompt, return_tensors="pt")
    input_ids = encoded["input_ids"].to(model.device)
    attention_mask = encoded.get("attention_mask")
    if attention_mask is not None:
        attention_mask = attention_mask.to(model.device)

    do_sample = args.temperature > 0.0
    outputs = model.generate(
        input_ids=input_ids,
        attention_mask=attention_mask,
        max_new_tokens=args.max_new_tokens,
        do_sample=do_sample,
        temperature=args.temperature if do_sample else None,
        top_p=args.top_p if do_sample else None,
        pad_token_id=tokenizer.pad_token_id,
        eos_token_id=tokenizer.eos_token_id,
    )

    new_tokens = outputs[0][input_ids.shape[1] :]
    text = tokenizer.decode(new_tokens, skip_special_tokens=True)
    return text.strip()


def load_models(base_model_id: str, adapter_dir: Path) -> tuple[Any, Any, Any]:
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:
        raise RuntimeError(
            "Missing dependencies. Install with: pip install -r requirements_day29_day31.txt"
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(base_model_id, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    base_model = AutoModelForCausalLM.from_pretrained(base_model_id, torch_dtype=torch.float32)
    tuned_base = AutoModelForCausalLM.from_pretrained(base_model_id, torch_dtype=torch.float32)
    tuned_model = PeftModel.from_pretrained(tuned_base, str(adapter_dir))

    return tokenizer, base_model, tuned_model


def build_report(
    *,
    args: argparse.Namespace,
    eval_rows: list[dict[str, Any]],
    base_avg: float,
    tuned_avg: float,
    base_section_avg: float,
    tuned_section_avg: float,
    base_kw_avg: float,
    tuned_kw_avg: float,
    tuned_win: int,
    draw_count: int,
    sample_rows: list[dict[str, Any]],
) -> str:
    total = len(eval_rows)
    lines = [
        "# Day 32 - 微调前后效果对比（固定评测集）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 评测集：{args.eval_file}",
        f"- 样本数：{total}",
        f"- 基础模型：{args.base_model_id}",
        f"- LoRA Adapter：{args.adapter_dir}",
        f"- 评分规则：quality = 0.6 * section_score + 0.4 * keyword_hit_ratio",
        "",
        "## 汇总结果",
        "",
        f"- base_avg_quality: {base_avg:.4f}",
        f"- tuned_avg_quality: {tuned_avg:.4f}",
        f"- delta_quality: {tuned_avg - base_avg:+.4f}",
        f"- base_avg_section_score: {base_section_avg:.4f}",
        f"- tuned_avg_section_score: {tuned_section_avg:.4f}",
        f"- base_avg_keyword_hit: {base_kw_avg:.4f}",
        f"- tuned_avg_keyword_hit: {tuned_kw_avg:.4f}",
        f"- tuned_win_count: {tuned_win}/{total}",
        f"- draw_count: {draw_count}/{total}",
        "",
        "## 样本对比（前 5 条）",
        "",
        "| id | base_score | tuned_score | delta |",
        "|---|---:|---:|---:|",
    ]

    for row in sample_rows[:5]:
        lines.append(
            f"| {row['id']} | {row['base_quality']:.4f} | {row['tuned_quality']:.4f} | {row['delta']:+.4f} |"
        )

    lines.extend(
        [
            "",
            "## 结论建议",
            "",
            "1. 若 tuned_avg_quality 持续高于 base，说明 Day31 微调方向有效。",
            "2. 若提升主要来自 section_score，说明格式对齐收益明显。",
            "3. 若 keyword_hit 提升有限，可在 Day30 增加覆盖真实业务关键词的样本。",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = parse_args()

    eval_path = resolve_project_path(args.eval_file)
    adapter_path = resolve_project_path(args.adapter_dir)
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)

    if not adapter_path.exists():
        raise FileNotFoundError(f"adapter dir not found: {adapter_path}")

    eval_rows = read_jsonl(eval_path)
    eval_rows = eval_rows[: max(1, int(args.max_eval_samples))]

    try:
        import torch

        torch.manual_seed(args.seed)
    except Exception:
        pass

    tokenizer, base_model, tuned_model = load_models(args.base_model_id, adapter_path)

    detail_rows: list[dict[str, Any]] = []
    base_quality_total = 0.0
    tuned_quality_total = 0.0
    base_section_total = 0.0
    tuned_section_total = 0.0
    base_kw_total = 0.0
    tuned_kw_total = 0.0
    tuned_win = 0
    draw_count = 0

    for idx, row in enumerate(eval_rows, start=1):
        base_answer = generate_response(base_model, tokenizer, row, args)
        tuned_answer = generate_response(tuned_model, tokenizer, row, args)

        base_section = section_score(base_answer)
        tuned_section = section_score(tuned_answer)
        base_kw = keyword_hit_ratio(base_answer, row)
        tuned_kw = keyword_hit_ratio(tuned_answer, row)

        base_quality = 0.6 * base_section + 0.4 * base_kw
        tuned_quality = 0.6 * tuned_section + 0.4 * tuned_kw
        delta = tuned_quality - base_quality

        if delta > 1e-9:
            tuned_win += 1
        elif abs(delta) <= 1e-9:
            draw_count += 1

        base_quality_total += base_quality
        tuned_quality_total += tuned_quality
        base_section_total += base_section
        tuned_section_total += tuned_section
        base_kw_total += base_kw
        tuned_kw_total += tuned_kw

        detail = {
            "timestamp_utc": utc_now_iso(),
            "phase": "day32_eval",
            "index": idx,
            "id": row.get("id") or f"sample-{idx:03d}",
            "instruction": row.get("instruction") or "",
            "tags": row.get("tags") or [],
            "base_answer": base_answer,
            "tuned_answer": tuned_answer,
            "base_section_score": base_section,
            "tuned_section_score": tuned_section,
            "base_keyword_hit": base_kw,
            "tuned_keyword_hit": tuned_kw,
            "base_quality": base_quality,
            "tuned_quality": tuned_quality,
            "delta": delta,
        }
        detail_rows.append(detail)
        append_jsonl(jsonl_path, detail)

    n = float(len(eval_rows))
    base_avg = base_quality_total / n
    tuned_avg = tuned_quality_total / n
    base_section_avg = base_section_total / n
    tuned_section_avg = tuned_section_total / n
    base_kw_avg = base_kw_total / n
    tuned_kw_avg = tuned_kw_total / n

    report_text = build_report(
        args=args,
        eval_rows=eval_rows,
        base_avg=base_avg,
        tuned_avg=tuned_avg,
        base_section_avg=base_section_avg,
        tuned_section_avg=tuned_section_avg,
        base_kw_avg=base_kw_avg,
        tuned_kw_avg=tuned_kw_avg,
        tuned_win=tuned_win,
        draw_count=draw_count,
        sample_rows=detail_rows,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")

    print("Done. Day32 before/after SFT evaluation generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()
