#!/usr/bin/env python3
"""Day 6：输出控制（固定 JSON 输出 + 校验失败重试）。"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from chat_cli import build_client, chat_once

PROJECT_DIR = Path(__file__).resolve().parent
ALLOWED_RISK = {"low", "medium", "high"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day 6 JSON output control")
    parser.add_argument("--input-file", default="inputs/day6_sample_incident.log", help="输入日志文件")
    parser.add_argument("--system-prompt", default="prompts/day6_json_output_cn.txt", help="系统提示词路径")
    parser.add_argument("--question", default="输出可执行排查结论", help="附加问题")
    parser.add_argument("--temperature", type=float, default=0.0, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=800, help="输出 token 上限")
    parser.add_argument("--max-attempts", type=int, default=3, help="校验失败最大重试次数")
    parser.add_argument("--model", default="", help="可选：覆盖环境变量中的模型名")
    parser.add_argument("--output-json", default="experiments/day6_structured_output.json", help="最终 JSON 输出")
    parser.add_argument("--report", default="experiments/day6_json_output_control.md", help="Markdown 报告")
    parser.add_argument("--jsonl", default="logs/day6_json_output_control.jsonl", help="尝试明细 JSONL")
    return parser.parse_args()


def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_text(encoding="utf-8")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def extract_json_payload(text: str) -> str:
    payload = text.strip()
    if payload.startswith("```"):
        lines = payload.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        payload = "\n".join(lines).strip()
        if payload.lower().startswith("json"):
            payload = payload[4:].strip()
    return payload


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_output(data: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["top-level must be object"]

    required_keys = {
        "incident_summary",
        "root_cause_hypothesis",
        "evidence",
        "triage_steps",
        "risk_level",
        "rollback_plan",
        "confidence",
    }
    unknown_keys = set(data.keys()) - required_keys
    missing_keys = required_keys - set(data.keys())
    if unknown_keys:
        errors.append(f"unknown keys: {sorted(unknown_keys)}")
    if missing_keys:
        errors.append(f"missing keys: {sorted(missing_keys)}")
    if missing_keys:
        return errors

    if not _is_non_empty_string(data["incident_summary"]):
        errors.append("incident_summary must be non-empty string")

    root = data["root_cause_hypothesis"]
    if not isinstance(root, list) or not root or not all(_is_non_empty_string(item) for item in root):
        errors.append("root_cause_hypothesis must be non-empty string list")

    evidence = data["evidence"]
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence must be non-empty list")
    else:
        for idx, item in enumerate(evidence, start=1):
            if not isinstance(item, dict):
                errors.append(f"evidence[{idx}] must be object")
                continue
            if not _is_non_empty_string(item.get("source")):
                errors.append(f"evidence[{idx}].source must be non-empty string")
            if not _is_non_empty_string(item.get("detail")):
                errors.append(f"evidence[{idx}].detail must be non-empty string")

    triage = data["triage_steps"]
    if not isinstance(triage, list) or not triage:
        errors.append("triage_steps must be non-empty list")
    else:
        for idx, item in enumerate(triage, start=1):
            if not isinstance(item, dict):
                errors.append(f"triage_steps[{idx}] must be object")
                continue
            if not isinstance(item.get("step"), int):
                errors.append(f"triage_steps[{idx}].step must be integer")
            if not _is_non_empty_string(item.get("action")):
                errors.append(f"triage_steps[{idx}].action must be non-empty string")
            if not _is_non_empty_string(item.get("expected_signal")):
                errors.append(f"triage_steps[{idx}].expected_signal must be non-empty string")

    risk = data["risk_level"]
    if not isinstance(risk, str) or risk not in ALLOWED_RISK:
        errors.append("risk_level must be one of low/medium/high")

    rollback = data["rollback_plan"]
    if not isinstance(rollback, list) or not rollback or not all(_is_non_empty_string(item) for item in rollback):
        errors.append("rollback_plan must be non-empty string list")

    confidence = data["confidence"]
    if not isinstance(confidence, (int, float)) or not (0 <= float(confidence) <= 1):
        errors.append("confidence must be number in [0, 1]")

    return errors


def write_report(path: Path, meta: dict, attempts: list[dict], success: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 6 输出控制报告（自动生成）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 输入文件：{meta['input_file']}",
        f"- 模型：{meta['model']}",
        f"- 温度：{meta['temperature']}",
        f"- Max tokens：{meta['max_tokens']}",
        f"- 最大尝试次数：{meta['max_attempts']}",
        f"- 最终状态：{'SUCCESS' if success else 'FAILED'}",
        "",
        "## 尝试记录",
        "",
        "| Attempt | Valid | Error Count |",
        "|---:|---:|---:|",
    ]

    for item in attempts:
        lines.append(f"| {item['attempt']} | {'YES' if item['valid'] else 'NO'} | {len(item['errors'])} |")

    lines.append("")
    for item in attempts:
        lines.append(f"### Attempt {item['attempt']}")
        lines.append("")
        lines.append(f"- 校验结果：{'通过' if item['valid'] else '失败'}")
        if item["errors"]:
            lines.append(f"- 错误：{'; '.join(item['errors'])}")
        else:
            lines.append("- 错误：(无)")
        lines.append("")
        lines.append("原始输出：")
        lines.append("")
        lines.append("```text")
        lines.append(item["raw_output"])
        lines.append("```")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    input_file = resolve_project_path(args.input_file)
    prompt_file = resolve_project_path(args.system_prompt)
    output_json_file = resolve_project_path(args.output_json)
    report_file = resolve_project_path(args.report)
    jsonl_file = resolve_project_path(args.jsonl)

    input_text = load_text(input_file)
    system_prompt = load_text(prompt_file)
    model_name = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
    api_key, base_url, client = build_client()

    user_text_base = (
        "请基于以下异常日志输出固定 JSON。"
        "\n\n附加要求：" + args.question.strip() + "\n\n"
        "异常日志：\n" + input_text
    )

    attempts: list[dict] = []
    final_json: Optional[dict] = None

    feedback = ""
    for attempt in range(1, args.max_attempts + 1):
        user_text = user_text_base
        if feedback:
            user_text += (
                "\n\n上一次输出未通过校验，请仅输出 JSON，并修复这些错误：\n"
                + feedback
            )

        assistant, usage = chat_once(
            client=client,
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            system_prompt=system_prompt,
            user_text=user_text,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )

        payload = extract_json_payload(assistant)
        errors: list[str]
        parsed: object
        try:
            parsed = json.loads(payload)
            errors = validate_output(parsed)
        except Exception as exc:
            parsed = None
            errors = [f"invalid JSON: {exc}"]

        valid = len(errors) == 0
        record = {
            "timestamp_utc": utc_now_iso(),
            "attempt": attempt,
            "valid": valid,
            "errors": errors,
            "model": model_name,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
            "raw_output": assistant,
        }
        attempts.append(record)
        append_jsonl(jsonl_file, record)

        if valid:
            final_json = parsed  # type: ignore[assignment]
            break

        feedback = "\n".join(f"- {item}" for item in errors)

    success = final_json is not None
    meta = {
        "input_file": args.input_file,
        "model": model_name,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "max_attempts": args.max_attempts,
    }
    write_report(report_file, meta, attempts, success)

    if not success:
        print("Validation failed after all retries.")
        print(f"Report => {args.report}")
        print(f"Raw logs => {args.jsonl}")
        raise SystemExit(2)

    output_json_file.parent.mkdir(parents=True, exist_ok=True)
    output_json_file.write_text(json.dumps(final_json, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done. Structured JSON generated.")
    print(f"Output => {args.output_json}")
    print(f"Report => {args.report}")
    print(f"Raw logs => {args.jsonl}")


if __name__ == "__main__":
    main()