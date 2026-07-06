#!/usr/bin/env python3
"""Day 16：离线评测脚本（命中率、引用正确率、信息不足正确率）。

输入：Day15 评测集（JSON）
输出：
- Markdown 报告：experiments/day16_offline_eval.md
- CSV 汇总：experiments/day16_offline_eval_summary.csv
- JSONL 明细：logs/day16_offline_eval.jsonl
"""

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv

from chat_cli import build_client, load_system_prompt
from run_day8_chunking_experiment import (
    build_chunks,
    build_embedding_client,
    embedding_vector,
    load_text,
    tokenize_words,
    utc_now_iso,
)
from run_day9_local_vector_search import l2_normalize
from run_day11_kb_qa_with_citations import answer_with_retry, retrieve_hits
from run_day13_reranker_comparison import rerank_hits

try:
    import faiss  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("faiss is not installed. Run: ./.venv/bin/pip install faiss-cpu") from exc

PROJECT_DIR = Path(__file__).resolve().parent


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day16 offline evaluation")
    parser.add_argument("--corpus-file", default="inputs/day8_corpus_backend_notes.txt", help="语料文件")
    parser.add_argument("--evalset-file", default="inputs/day15_evalset_qa.json", help="评测集 JSON")
    parser.add_argument("--system-prompt", default="prompts/day11_kb_qa_with_citations_cn.txt", help="系统提示词")
    parser.add_argument("--chunk-size", type=int, default=80, help="切分 chunk size")
    parser.add_argument("--overlap", type=int, default=20, help="切分 overlap")
    parser.add_argument("--top-k", type=int, default=3, help="最终给问答模型的 chunk 数")
    parser.add_argument("--candidate-top-n", type=int, default=8, help="重排前召回候选数")
    parser.add_argument("--use-rerank", action="store_true", help="是否启用重排")
    parser.add_argument("--rerank-alpha", type=float, default=0.70, help="重排语义分权重")
    parser.add_argument("--rerank-beta", type=float, default=0.25, help="重排词重叠权重")
    parser.add_argument("--rerank-gamma", type=float, default=0.05, help="重排名次先验权重")
    parser.add_argument("--embedding-model", default="", help="可选：覆盖 OPENAI_EMBEDDING_MODEL")
    parser.add_argument("--model", default="", help="问答模型（覆盖 OPENAI_MODEL）")
    parser.add_argument("--temperature", type=float, default=0.2, help="问答温度")
    parser.add_argument("--max-tokens", type=int, default=900, help="问答输出上限")
    parser.add_argument("--max-attempts", type=int, default=2, help="引用校验失败时最多重试次数")
    parser.add_argument("--limit", type=int, default=0, help="仅评测前 N 条，0 表示全部")
    parser.add_argument("--report", default="experiments/day16_offline_eval.md", help="Markdown 报告")
    parser.add_argument("--csv", default="experiments/day16_offline_eval_summary.csv", help="CSV 汇总")
    parser.add_argument("--jsonl", default="logs/day16_offline_eval.jsonl", help="JSONL 明细")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    tokens = tokenize_words(text)
    chunks = build_chunks(tokens, chunk_size, overlap)
    return ["".join(chunk) for chunk in chunks]


def load_eval_cases(path: Path, limit: int) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    if not isinstance(cases, list):
        raise ValueError("evalset JSON field 'cases' must be a list")
    if limit > 0:
        return cases[:limit]
    return cases


def text_contains_any_keyword(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return False
    return any(keyword in text for keyword in keywords)


def hits_match_keywords(hits: list[dict], keywords: list[str]) -> bool:
    if not keywords:
        return False
    for hit in hits:
        hit_text = str(hit.get("text") or "")
        if text_contains_any_keyword(hit_text, keywords):
            return True
    return False


def citation_chunks_match_keywords(hits: list[dict], citations: list[dict], keywords: list[str]) -> bool:
    if not keywords or not citations:
        return False
    hit_map = {int(hit["chunk_id"]): str(hit.get("text") or "") for hit in hits}
    for item in citations:
        cid = int(item.get("chunk_id", -1))
        text = hit_map.get(cid, "")
        if text and text_contains_any_keyword(text, keywords):
            return True
    return False


def choose_hits(args: argparse.Namespace, query: str, candidates: list[dict]) -> list[dict]:
    if args.use_rerank:
        return rerank_hits(
            query=query,
            candidates=candidates,
            top_k=args.top_k,
            alpha=args.rerank_alpha,
            beta=args.rerank_beta,
            gamma=args.rerank_gamma,
        )
    return [dict(hit) for hit in candidates[: args.top_k]]


def compute_summary(rows: list[dict]) -> dict:
    total = len(rows)
    if total == 0:
        return {
            "total_cases": 0,
            "answerable_cases": 0,
            "unanswerable_cases": 0,
            "retrieval_hit_rate": 0.0,
            "citation_correct_rate": 0.0,
            "insufficient_correct_rate": 0.0,
            "citation_format_valid_rate": 0.0,
            "avg_attempt_count": 0.0,
            "avg_total_tokens": 0.0,
        }

    answerable_rows = [r for r in rows if r["answerable"]]
    unanswerable_rows = [r for r in rows if not r["answerable"]]

    answerable_n = len(answerable_rows)
    unanswerable_n = len(unanswerable_rows)

    retrieval_hit = sum(1 for r in answerable_rows if r["retrieval_hit"])
    citation_correct = sum(1 for r in answerable_rows if r["citation_correct"])
    insufficient_correct = sum(1 for r in unanswerable_rows if r["insufficient_correct"])

    citation_valid = sum(1 for r in rows if r["citation_valid"])
    attempt_total = sum(int(r["attempt_count"]) for r in rows)
    token_total = sum(float(r.get("total_tokens") or 0) for r in rows)

    return {
        "total_cases": total,
        "answerable_cases": answerable_n,
        "unanswerable_cases": unanswerable_n,
        "retrieval_hit_rate": retrieval_hit / answerable_n if answerable_n else 0.0,
        "citation_correct_rate": citation_correct / answerable_n if answerable_n else 0.0,
        "insufficient_correct_rate": insufficient_correct / unanswerable_n if unanswerable_n else 0.0,
        "citation_format_valid_rate": citation_valid / total,
        "avg_attempt_count": attempt_total / total,
        "avg_total_tokens": token_total / total,
    }


def write_summary_csv(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "total_cases",
        "answerable_cases",
        "unanswerable_cases",
        "retrieval_hit_rate",
        "citation_correct_rate",
        "insufficient_correct_rate",
        "citation_format_valid_rate",
        "avg_attempt_count",
        "avg_total_tokens",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(summary)


def write_report(path: Path, args: argparse.Namespace, summary: dict, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 16 离线评测报告（命中率、引用正确率）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 评测集：{args.evalset_file}",
        f"- 语料文件：{args.corpus_file}",
        f"- chunk_size / overlap：{args.chunk_size} / {args.overlap}",
        f"- top-k：{args.top_k}",
        f"- candidate_top_n：{args.candidate_top_n}",
        f"- use_rerank：{args.use_rerank}",
        f"- embedding 模型：{args.embedding_model}",
        f"- QA 模型：{args.model}",
        "",
        "## 汇总指标",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| total_cases | {summary['total_cases']} |",
        f"| answerable_cases | {summary['answerable_cases']} |",
        f"| unanswerable_cases | {summary['unanswerable_cases']} |",
        f"| retrieval_hit_rate | {summary['retrieval_hit_rate']:.3f} |",
        f"| citation_correct_rate | {summary['citation_correct_rate']:.3f} |",
        f"| insufficient_correct_rate | {summary['insufficient_correct_rate']:.3f} |",
        f"| citation_format_valid_rate | {summary['citation_format_valid_rate']:.3f} |",
        f"| avg_attempt_count | {summary['avg_attempt_count']:.2f} |",
        f"| avg_total_tokens | {summary['avg_total_tokens']:.1f} |",
        "",
        "## 指标口径",
        "",
        "- retrieval_hit_rate：仅对可回答样本统计；Top-K 片段命中 expected_source_keywords 即记为命中。",
        "- citation_correct_rate：仅对可回答样本统计；要求 citation_valid=true 且引用 chunk 命中 expected_source_keywords。",
        "- insufficient_correct_rate：仅对不可回答样本统计；回答包含“当前信息不足”即记为正确拒答。",
        "",
        "## 样本明细（前 12 条）",
        "",
        "| id | answerable | retrieval_hit | citation_valid | citation_correct | insufficient_correct | attempts | total_tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for row in rows[:12]:
        lines.append(
            f"| {row['id']} | {int(row['answerable'])} | {int(row['retrieval_hit'])} | "
            f"{int(row['citation_valid'])} | {int(row['citation_correct'])} | {int(row['insufficient_correct'])} | "
            f"{row['attempt_count']} | {row.get('total_tokens') or 0} |"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    if args.overlap >= args.chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    if args.top_k <= 0:
        raise ValueError("top-k must be > 0")
    if args.candidate_top_n < args.top_k:
        raise ValueError("candidate-top-n must be >= top-k")
    if args.max_attempts <= 0:
        raise ValueError("max-attempts must be > 0")

    corpus_file = resolve_project_path(args.corpus_file)
    evalset_file = resolve_project_path(args.evalset_file)
    system_prompt_file = resolve_project_path(args.system_prompt)
    report_file = resolve_project_path(args.report)
    csv_file = resolve_project_path(args.csv)
    jsonl_file = resolve_project_path(args.jsonl)

    corpus_text = load_text(corpus_file)
    chunks = chunk_text(corpus_text, args.chunk_size, args.overlap)
    if not chunks:
        raise RuntimeError("no chunks generated")

    cases = load_eval_cases(evalset_file, args.limit)
    if not cases:
        raise RuntimeError("no eval cases loaded")

    embedding_model = args.embedding_model.strip() or (os.getenv("OPENAI_EMBEDDING_MODEL") or "").strip() or "nomic-embed-text"
    qa_model = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
    args.embedding_model = embedding_model
    args.model = qa_model

    emb_api_key, emb_base_url, emb_client = build_embedding_client()
    qa_api_key, qa_base_url, qa_client = build_client()
    system_prompt = load_system_prompt(system_prompt_file)

    chunk_vectors = [embedding_vector(emb_client, emb_api_key, emb_base_url, embedding_model, chunk) for chunk in chunks]
    matrix = np.asarray(chunk_vectors, dtype=np.float32)
    matrix = l2_normalize(matrix)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)

    rows: list[dict] = []
    for case in cases:
        case_id = str(case.get("id") or "")
        question = str(case.get("question") or "").strip()
        answerable = bool(case.get("answerable"))
        expected_keywords = case.get("expected_source_keywords") or []
        if not isinstance(expected_keywords, list):
            expected_keywords = []
        expected_keywords = [str(x) for x in expected_keywords if str(x).strip()]

        candidates = retrieve_hits(
            index=index,
            chunks=chunks,
            query=question,
            top_k=args.candidate_top_n,
            embedding_client=emb_client,
            api_key=emb_api_key,
            base_url=emb_base_url,
            embedding_model=embedding_model,
        )
        hits = choose_hits(args, question, candidates)

        answer, usage, citation_valid, citations, attempt_count, validation_error = answer_with_retry(
            query=question,
            hits=hits,
            qa_client=qa_client,
            qa_api_key=qa_api_key,
            qa_base_url=qa_base_url,
            qa_model=qa_model,
            system_prompt=system_prompt,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            max_attempts=args.max_attempts,
        )

        retrieval_hit = hits_match_keywords(hits, expected_keywords)
        citation_keyword_match = citation_chunks_match_keywords(hits, citations, expected_keywords)
        citation_correct = bool(answerable and citation_valid and citation_keyword_match)
        insufficient_correct = bool((not answerable) and ("当前信息不足" in answer))

        row = {
            "timestamp_utc": utc_now_iso(),
            "id": case_id,
            "question": question,
            "answerable": answerable,
            "category": str(case.get("category") or ""),
            "difficulty": str(case.get("difficulty") or ""),
            "expected_source_keywords": expected_keywords,
            "hits": hits,
            "answer": answer,
            "citations": citations,
            "citation_valid": citation_valid,
            "validation_error": validation_error,
            "attempt_count": attempt_count,
            "retrieval_hit": retrieval_hit,
            "citation_correct": citation_correct,
            "insufficient_correct": insufficient_correct,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "mode": "rerank" if args.use_rerank else "no_rerank",
        }
        rows.append(row)
        append_jsonl(jsonl_file, row)

    summary = compute_summary(rows)
    write_summary_csv(csv_file, summary)
    write_report(report_file, args, summary, rows)

    print("Done. Day16 offline evaluation generated.")
    print(f"Report => {args.report}")
    print(f"CSV    => {args.csv}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()
