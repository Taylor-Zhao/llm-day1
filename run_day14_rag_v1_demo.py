#!/usr/bin/env python3
"""Day 14：RAG V1 演示版（可回答你熟悉的后端文档）。"""

import argparse
import json
import os
from pathlib import Path
from typing import List

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

DEFAULT_QUERIES = [
    "如何排查数据库连接池打满导致的超时问题？",
    "缓存命中率下降时应该优先看哪些指标？",
    "限流和降级策略在高并发场景下怎么落地？",
]


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day14 RAG V1 backend docs demo")
    parser.add_argument("--corpus-file", default="inputs/day8_corpus_backend_notes.txt", help="语料文件（可替换为你的后端文档）")
    parser.add_argument("--system-prompt", default="prompts/day14_rag_demo_cn.txt", help="系统提示词")
    parser.add_argument("--chunk-size", type=int, default=80, help="切分 chunk size")
    parser.add_argument("--overlap", type=int, default=20, help="切分 overlap")
    parser.add_argument("--top-k", type=int, default=3, help="最终给问答模型的 chunk 数")
    parser.add_argument("--candidate-top-n", type=int, default=8, help="重排前先召回候选数")
    parser.add_argument("--use-rerank", action="store_true", help="启用重排")
    parser.add_argument("--embedding-model", default="", help="可选：覆盖 OPENAI_EMBEDDING_MODEL")
    parser.add_argument("--model", default="", help="问答模型（覆盖 OPENAI_MODEL）")
    parser.add_argument("--temperature", type=float, default=0.2, help="问答温度")
    parser.add_argument("--max-tokens", type=int, default=900, help="问答输出上限")
    parser.add_argument("--max-attempts", type=int, default=2, help="引用校验失败时最多重试次数")
    parser.add_argument("--rerank-alpha", type=float, default=0.70, help="重排语义分权重")
    parser.add_argument("--rerank-beta", type=float, default=0.25, help="重排词重叠权重")
    parser.add_argument("--rerank-gamma", type=float, default=0.05, help="重排名次先验权重")
    parser.add_argument("--query", action="append", default=[], help="单次提问，可重复传多个 --query")
    parser.add_argument("--repl", action="store_true", help="进入交互问答模式（输入 exit 退出）")
    parser.add_argument("--report", default="experiments/day14_rag_v1_demo.md", help="Markdown 报告")
    parser.add_argument("--jsonl", default="logs/day14_rag_v1_demo.jsonl", help="JSONL 明细")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    tokens = tokenize_words(text)
    chunks = build_chunks(tokens, chunk_size, overlap)
    return ["".join(chunk) for chunk in chunks]


def choose_hits(args: argparse.Namespace, query: str, candidates: List[dict]) -> List[dict]:
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


def write_report(path: Path, args: argparse.Namespace, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 14 RAG V1 演示报告（后端文档问答）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 语料文件：{args.corpus_file}",
        f"- chunk_size / overlap：{args.chunk_size} / {args.overlap}",
        f"- top-k：{args.top_k}",
        f"- candidate_top_n：{args.candidate_top_n}",
        f"- use_rerank：{args.use_rerank}",
        f"- embedding 模型：{args.embedding_model}",
        f"- QA 模型：{args.model}",
        "",
    ]

    for row in rows:
        lines.append(f"## Query: {row['query']}")
        lines.append("")
        lines.append(f"- citation_valid: {row['citation_valid']}")
        lines.append(f"- attempts: {row['attempt_count']}")
        lines.append("")
        lines.append("### Top-K Chunks")
        lines.append("")
        for hit in row["hits"]:
            score = hit.get("rerank_score", hit.get("score", 0.0))
            lines.append(f"- rank={hit['rank']}, chunk_id={hit['chunk_id']}, score={score:.4f}")
            lines.append(f"  - text: {hit['text'][:220]}")
        lines.append("")
        lines.append("### Answer")
        lines.append("")
        lines.append(row["answer"])
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def ask_once(args: argparse.Namespace, query: str, chunks: List[str], index, emb_client, emb_api_key: str, emb_base_url: str,
             embedding_model: str, qa_client, qa_api_key: str, qa_base_url: str, qa_model: str, system_prompt: str) -> dict:
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
    hits = choose_hits(args, query, candidates)

    answer, usage, citation_valid, citations, attempt_count, validation_error = answer_with_retry(
        query=query,
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

    return {
        "timestamp_utc": utc_now_iso(),
        "query": query,
        "hits": hits,
        "answer": answer,
        "citations": citations,
        "citation_valid": citation_valid,
        "validation_error": validation_error,
        "attempt_count": attempt_count,
        "total_tokens": usage.get("total_tokens"),
        "mode": "rerank" if args.use_rerank else "no_rerank",
    }


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
    system_prompt_file = resolve_project_path(args.system_prompt)
    report_file = resolve_project_path(args.report)
    jsonl_file = resolve_project_path(args.jsonl)

    corpus_text = load_text(corpus_file)
    chunks = chunk_text(corpus_text, args.chunk_size, args.overlap)
    if not chunks:
        raise RuntimeError("no chunks generated")

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

    rows: List[dict] = []
    queries = args.query or DEFAULT_QUERIES
    for query in queries:
        row = ask_once(
            args=args,
            query=query,
            chunks=chunks,
            index=index,
            emb_client=emb_client,
            emb_api_key=emb_api_key,
            emb_base_url=emb_base_url,
            embedding_model=embedding_model,
            qa_client=qa_client,
            qa_api_key=qa_api_key,
            qa_base_url=qa_base_url,
            qa_model=qa_model,
            system_prompt=system_prompt,
        )
        rows.append(row)
        append_jsonl(jsonl_file, row)

    if args.repl:
        print("\n[Day14 RAG Demo] 进入交互模式，输入 exit 退出。")
        while True:
            user_q = input("\n你问> ").strip()
            if not user_q:
                continue
            if user_q.lower() in {"exit", "quit", "q"}:
                break
            row = ask_once(
                args=args,
                query=user_q,
                chunks=chunks,
                index=index,
                emb_client=emb_client,
                emb_api_key=emb_api_key,
                emb_base_url=emb_base_url,
                embedding_model=embedding_model,
                qa_client=qa_client,
                qa_api_key=qa_api_key,
                qa_base_url=qa_base_url,
                qa_model=qa_model,
                system_prompt=system_prompt,
            )
            rows.append(row)
            append_jsonl(jsonl_file, row)
            print("\n回答如下：\n")
            print(row["answer"])

    write_report(report_file, args, rows)

    print("Done. Day14 RAG V1 demo generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()