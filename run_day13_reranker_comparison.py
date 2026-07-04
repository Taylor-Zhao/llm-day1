#!/usr/bin/env python3
"""Day 13：加入重排（Reranker）并对比效果。"""

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Tuple

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
from run_day9_local_vector_search import l2_normalize, read_queries
from run_day11_kb_qa_with_citations import answer_with_retry, retrieve_hits

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
    parser = argparse.ArgumentParser(description="Run Day13 reranker comparison")
    parser.add_argument("--corpus-file", default="inputs/day8_corpus_backend_notes.txt", help="语料文件")
    parser.add_argument("--queries-file", default="inputs/day9_queries.txt", help="查询文件（每行一条）")
    parser.add_argument("--system-prompt", default="prompts/day11_kb_qa_with_citations_cn.txt", help="系统提示词")
    parser.add_argument("--chunk-size", type=int, default=80, help="切分 chunk size")
    parser.add_argument("--overlap", type=int, default=20, help="切分 overlap")
    parser.add_argument("--top-k", type=int, default=3, help="最终给问答模型的 chunk 数")
    parser.add_argument("--candidate-top-n", type=int, default=8, help="重排前先召回的候选数")
    parser.add_argument("--embedding-model", default="", help="可选：覆盖 OPENAI_EMBEDDING_MODEL")
    parser.add_argument("--model", default="", help="问答模型（覆盖 OPENAI_MODEL）")
    parser.add_argument("--temperature", type=float, default=0.2, help="问答温度")
    parser.add_argument("--max-tokens", type=int, default=800, help="问答输出上限")
    parser.add_argument("--max-attempts", type=int, default=2, help="单 query 最多重试次数")
    parser.add_argument("--rerank-alpha", type=float, default=0.70, help="重排分中语义分权重")
    parser.add_argument("--rerank-beta", type=float, default=0.25, help="重排分中词重叠权重")
    parser.add_argument("--rerank-gamma", type=float, default=0.05, help="重排分中原始名次先验权重")
    parser.add_argument("--report", default="experiments/day13_reranker_comparison.md", help="Markdown 报告")
    parser.add_argument("--csv", default="experiments/day13_reranker_comparison.csv", help="CSV 汇总")
    parser.add_argument("--jsonl", default="logs/day13_reranker_comparison.jsonl", help="JSONL 明细")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    tokens = tokenize_words(text)
    chunks = build_chunks(tokens, chunk_size, overlap)
    return ["".join(chunk) for chunk in chunks]


def lexical_overlap(query: str, text: str) -> float:
    q_set = set(tokenize_words(query))
    t_set = set(tokenize_words(text))
    if not q_set or not t_set:
        return 0.0
    inter = len(q_set & t_set)
    union = len(q_set | t_set)
    return inter / union if union else 0.0


def rerank_hits(query: str, candidates: list[dict], top_k: int, alpha: float, beta: float, gamma: float) -> list[dict]:
    if not candidates:
        return []

    base_scores = [float(hit["score"]) for hit in candidates]
    min_s = min(base_scores)
    max_s = max(base_scores)
    denom = (max_s - min_s) if max_s > min_s else 1.0

    ranked: list[dict] = []
    for idx, hit in enumerate(candidates, start=1):
        semantic_norm = (float(hit["score"]) - min_s) / denom
        overlap = lexical_overlap(query, hit["text"])
        position_prior = 1.0 / idx
        rerank_score = alpha * semantic_norm + beta * overlap + gamma * position_prior
        item = dict(hit)
        item["semantic_norm"] = semantic_norm
        item["lexical_overlap"] = overlap
        item["position_prior"] = position_prior
        item["rerank_score"] = rerank_score
        ranked.append(item)

    ranked.sort(key=lambda x: x["rerank_score"], reverse=True)
    out = []
    for rank, item in enumerate(ranked[:top_k], start=1):
        item2 = dict(item)
        item2["rank"] = rank
        out.append(item2)
    return out


def compute_mode_metrics(records: list[dict]) -> dict:
    n = len(records) or 1
    citation_valid_count = sum(1 for r in records if r["citation_valid"])
    info_insufficient_count = sum(1 for r in records if "当前信息不足" in r["answer"])
    attempt_total = sum(int(r["attempt_count"]) for r in records)
    token_total = sum(float(r.get("total_tokens") or 0) for r in records)
    top1_total = sum(float(r["hits"][0]["score"]) if r["hits"] else 0.0 for r in records)

    return {
        "query_count": len(records),
        "citation_valid_rate": citation_valid_count / n,
        "info_insufficient_rate": info_insufficient_count / n,
        "avg_attempt_count": attempt_total / n,
        "avg_total_tokens": token_total / n,
        "avg_top1_score": top1_total / n,
    }


def write_csv(path: Path, baseline_metrics: dict, rerank_metrics: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "mode",
        "query_count",
        "citation_valid_rate",
        "info_insufficient_rate",
        "avg_attempt_count",
        "avg_total_tokens",
        "avg_top1_score",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"mode": "no_rerank", **baseline_metrics})
        writer.writerow({"mode": "rerank", **rerank_metrics})


def write_report(
    path: Path,
    args: argparse.Namespace,
    baseline_metrics: dict,
    rerank_metrics: dict,
    baseline_rows: list[dict],
    rerank_rows: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    delta_valid = rerank_metrics["citation_valid_rate"] - baseline_metrics["citation_valid_rate"]
    delta_insufficient = rerank_metrics["info_insufficient_rate"] - baseline_metrics["info_insufficient_rate"]
    delta_attempt = rerank_metrics["avg_attempt_count"] - baseline_metrics["avg_attempt_count"]
    delta_tokens = rerank_metrics["avg_total_tokens"] - baseline_metrics["avg_total_tokens"]

    lines = [
        "# Day 13 重排对比报告（No-Rerank vs Rerank）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- chunk_size / overlap：{args.chunk_size} / {args.overlap}",
        f"- top-k：{args.top_k}",
        f"- candidate_top_n：{args.candidate_top_n}",
        f"- rerank 权重(alpha/beta/gamma)：{args.rerank_alpha}/{args.rerank_beta}/{args.rerank_gamma}",
        f"- embedding 模型：{args.embedding_model}",
        f"- QA 模型：{args.model}",
        "",
        "## 汇总对比",
        "",
        "| mode | query_count | citation_valid_rate | info_insufficient_rate | avg_attempt_count | avg_total_tokens | avg_top1_score |",
        "|---|---:|---:|---:|---:|---:|---:|",
        (
            f"| no_rerank | {baseline_metrics['query_count']} | {baseline_metrics['citation_valid_rate']:.3f} | "
            f"{baseline_metrics['info_insufficient_rate']:.3f} | {baseline_metrics['avg_attempt_count']:.2f} | "
            f"{baseline_metrics['avg_total_tokens']:.1f} | {baseline_metrics['avg_top1_score']:.3f} |"
        ),
        (
            f"| rerank | {rerank_metrics['query_count']} | {rerank_metrics['citation_valid_rate']:.3f} | "
            f"{rerank_metrics['info_insufficient_rate']:.3f} | {rerank_metrics['avg_attempt_count']:.2f} | "
            f"{rerank_metrics['avg_total_tokens']:.1f} | {rerank_metrics['avg_top1_score']:.3f} |"
        ),
        "",
        "## 指标变化（rerank - no_rerank）",
        "",
        f"- citation_valid_rate: {delta_valid:+.3f}",
        f"- info_insufficient_rate: {delta_insufficient:+.3f}",
        f"- avg_attempt_count: {delta_attempt:+.2f}",
        f"- avg_total_tokens: {delta_tokens:+.1f}",
        "",
        "## 每个 Query 的 Top-K 对比",
        "",
    ]

    base_map = {row["query"]: row for row in baseline_rows}
    rerank_map = {row["query"]: row for row in rerank_rows}
    for query in base_map.keys():
        b = base_map[query]
        r = rerank_map[query]
        lines.append(f"### Query: {query}")
        lines.append("")
        lines.append("- no_rerank chunk_ids: " + ", ".join(str(x["chunk_id"]) for x in b["hits"]))
        lines.append("- rerank chunk_ids: " + ", ".join(str(x["chunk_id"]) for x in r["hits"]))
        lines.append(f"- no_rerank citation_valid={b['citation_valid']}, attempts={b['attempt_count']}")
        lines.append(f"- rerank citation_valid={r['citation_valid']}, attempts={r['attempt_count']}")
        lines.append("")

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
    queries_file = resolve_project_path(args.queries_file)
    system_prompt_file = resolve_project_path(args.system_prompt)
    report_file = resolve_project_path(args.report)
    csv_file = resolve_project_path(args.csv)
    jsonl_file = resolve_project_path(args.jsonl)

    corpus_text = load_text(corpus_file)
    chunks = chunk_text(corpus_text, args.chunk_size, args.overlap)
    if not chunks:
        raise RuntimeError("no chunks generated")

    queries = read_queries(queries_file)
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

    baseline_rows: list[dict] = []
    rerank_rows: list[dict] = []

    for query in queries:
        candidates = retrieve_hits(
            index=index,
            chunks=chunks,
            query=query,
            top_k=args.candidate_top_n,
            embedding_client=emb_client,
            api_key=emb_api_key,
            base_url=emb_base_url,
            embedding_model=embedding_model,
        )

        baseline_hits = [dict(hit) for hit in candidates[: args.top_k]]
        rerank_hits_topk = rerank_hits(
            query=query,
            candidates=candidates,
            top_k=args.top_k,
            alpha=args.rerank_alpha,
            beta=args.rerank_beta,
            gamma=args.rerank_gamma,
        )

        b_answer, b_usage, b_valid, b_citations, b_attempt, b_error = answer_with_retry(
            query=query,
            hits=baseline_hits,
            qa_client=qa_client,
            qa_api_key=qa_api_key,
            qa_base_url=qa_base_url,
            qa_model=qa_model,
            system_prompt=system_prompt,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            max_attempts=args.max_attempts,
        )

        r_answer, r_usage, r_valid, r_citations, r_attempt, r_error = answer_with_retry(
            query=query,
            hits=rerank_hits_topk,
            qa_client=qa_client,
            qa_api_key=qa_api_key,
            qa_base_url=qa_base_url,
            qa_model=qa_model,
            system_prompt=system_prompt,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            max_attempts=args.max_attempts,
        )

        b_row = {
            "mode": "no_rerank",
            "timestamp_utc": utc_now_iso(),
            "query": query,
            "hits": baseline_hits,
            "answer": b_answer,
            "citations": b_citations,
            "citation_valid": b_valid,
            "validation_error": b_error,
            "attempt_count": b_attempt,
            "total_tokens": b_usage.get("total_tokens"),
        }
        r_row = {
            "mode": "rerank",
            "timestamp_utc": utc_now_iso(),
            "query": query,
            "hits": rerank_hits_topk,
            "answer": r_answer,
            "citations": r_citations,
            "citation_valid": r_valid,
            "validation_error": r_error,
            "attempt_count": r_attempt,
            "total_tokens": r_usage.get("total_tokens"),
        }
        baseline_rows.append(b_row)
        rerank_rows.append(r_row)
        append_jsonl(jsonl_file, b_row)
        append_jsonl(jsonl_file, r_row)

    baseline_metrics = compute_mode_metrics(baseline_rows)
    rerank_metrics = compute_mode_metrics(rerank_rows)
    write_csv(csv_file, baseline_metrics, rerank_metrics)
    write_report(report_file, args, baseline_metrics, rerank_metrics, baseline_rows, rerank_rows)

    print("Done. Day13 reranker comparison generated.")
    print(f"Report => {args.report}")
    print(f"CSV    => {args.csv}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()