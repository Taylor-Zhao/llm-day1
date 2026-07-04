#!/usr/bin/env python3
"""Day 11：知识库问答 V2（回答必须附原文引用）。"""

import argparse
import json
import os
import re
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

from chat_cli import build_client, chat_once, load_system_prompt
from run_day8_chunking_experiment import (
    build_chunks,
    build_embedding_client,
    embedding_vector,
    load_text,
    tokenize_words,
    utc_now_iso,
)
from run_day9_local_vector_search import l2_normalize, read_queries

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
    parser = argparse.ArgumentParser(description="Run Day11 KB QA with required citations")
    parser.add_argument("--corpus-file", default="inputs/day8_corpus_backend_notes.txt", help="语料文件")
    parser.add_argument("--queries-file", default="inputs/day9_queries.txt", help="查询文件（每行一条）")
    parser.add_argument("--system-prompt", default="prompts/day11_kb_qa_with_citations_cn.txt", help="系统提示词")
    parser.add_argument("--chunk-size", type=int, default=80, help="切分 chunk size")
    parser.add_argument("--overlap", type=int, default=20, help="切分 overlap")
    parser.add_argument("--top-k", type=int, default=3, help="每个 query 召回的 chunk 数")
    parser.add_argument("--embedding-model", default="", help="可选：覆盖 OPENAI_EMBEDDING_MODEL")
    parser.add_argument("--model", default="", help="问答模型（覆盖 OPENAI_MODEL）")
    parser.add_argument("--temperature", type=float, default=0.2, help="问答温度")
    parser.add_argument("--max-tokens", type=int, default=800, help="问答输出上限")
    parser.add_argument("--max-attempts", type=int, default=3, help="引用校验失败时最多重试次数")
    parser.add_argument("--report", default="experiments/day11_kb_qa_with_citations.md", help="Markdown 报告")
    parser.add_argument("--jsonl", default="logs/day11_kb_qa_with_citations.jsonl", help="JSONL 明细")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    tokens = tokenize_words(text)
    chunks = build_chunks(tokens, chunk_size, overlap)
    return ["".join(chunk) for chunk in chunks]


def retrieve_hits(
    index: "faiss.IndexFlatIP",
    chunks: list[str],
    query: str,
    top_k: int,
    embedding_client,
    api_key: str,
    base_url: str,
    embedding_model: str,
) -> list[dict]:
    q_vec = embedding_vector(embedding_client, api_key, base_url, embedding_model, query)
    q_matrix = np.asarray([q_vec], dtype=np.float32)
    q_matrix = l2_normalize(q_matrix)
    scores, ids = index.search(q_matrix, top_k)

    hits: list[dict] = []
    for rank, (score, chunk_id) in enumerate(zip(scores[0], ids[0]), start=1):
        if chunk_id < 0 or chunk_id >= len(chunks):
            continue
        hits.append(
            {
                "rank": rank,
                "score": float(score),
                "chunk_id": int(chunk_id),
                "text": chunks[chunk_id],
            }
        )
    return hits


def build_user_text(query: str, hits: list[dict], extra_instruction: str = "") -> str:
    blocks = [f"[chunk-{hit['chunk_id']}] {hit['text']}" for hit in hits]
    context = "\n\n".join(blocks)
    suffix = f"\n\n补充要求：{extra_instruction}" if extra_instruction else ""
    return (
        f"用户问题：{query}\n\n"
        "以下是召回片段（按相似度降序）：\n"
        f"{context}\n\n"
        "请仅基于这些片段回答，并严格按指定格式输出。"
        f"{suffix}"
    )


def parse_citations(answer_text: str) -> list[dict]:
    pattern = re.compile(r"^-\s*\[chunk-(\d+)\]\s*(.+)$", re.MULTILINE)
    citations: list[dict] = []
    for m in pattern.finditer(answer_text):
        citations.append({"chunk_id": int(m.group(1)), "quote": m.group(2).strip()})
    return citations


def validate_answer_with_citations(answer_text: str, hits: list[dict]) -> tuple[bool, str, list[dict]]:
    if "回答：" not in answer_text:
        return False, "缺少“回答：”小节。", []
    if "引用来源" not in answer_text:
        return False, "缺少“引用来源”小节。", []

    citations = parse_citations(answer_text)
    if not citations:
        return False, "未检测到合法引用行，格式应为 - [chunk-<id>] <原文片段>。", []

    hit_map = {int(hit["chunk_id"]): hit["text"] for hit in hits}
    for item in citations:
        cid = item["chunk_id"]
        quote = item["quote"]
        if cid not in hit_map:
            return False, f"引用了未召回的 chunk-{cid}。", citations
        if quote not in hit_map[cid]:
            return False, f"引用内容不是 chunk-{cid} 的原文连续子串。", citations

    return True, "ok", citations


def write_report(path: Path, args: argparse.Namespace, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 11 知识库问答报告（必须附原文引用）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 语料文件：{args.corpus_file}",
        f"- 查询文件：{args.queries_file}",
        f"- chunk_size / overlap：{args.chunk_size} / {args.overlap}",
        f"- top-k：{args.top_k}",
        f"- embedding 模型：{args.embedding_model}",
        f"- QA 模型：{args.model}",
        f"- max_attempts：{args.max_attempts}",
        "",
    ]

    for row in rows:
        lines.append(f"## Query: {row['query']}")
        lines.append("")
        lines.append("### Retrieved Chunks")
        lines.append("")
        for hit in row["hits"]:
            lines.append(f"- rank={hit['rank']}, score={hit['score']:.4f}, chunk_id={hit['chunk_id']}")
            lines.append(f"  - text: {hit['text'][:220]}")
        lines.append("")
        lines.append(f"### Answer (attempts={row['attempt_count']}, citation_valid={row['citation_valid']})")
        lines.append("")
        lines.append(row["answer"])
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def answer_with_retry(
    query: str,
    hits: list[dict],
    qa_client,
    qa_api_key: str,
    qa_base_url: str,
    qa_model: str,
    system_prompt: str,
    temperature: float,
    max_tokens: int,
    max_attempts: int,
) -> tuple[str, dict, bool, list[dict], int, str]:
    last_answer = ""
    last_usage: dict = {}
    last_citations: list[dict] = []
    last_error = ""
    extra_instruction = ""

    for attempt in range(1, max_attempts + 1):
        user_text = build_user_text(query, hits, extra_instruction)
        answer, usage = chat_once(
            client=qa_client,
            api_key=qa_api_key,
            base_url=qa_base_url,
            model=qa_model,
            system_prompt=system_prompt,
            user_text=user_text,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        ok, message, citations = validate_answer_with_citations(answer, hits)

        last_answer = answer
        last_usage = usage
        last_citations = citations
        last_error = message

        if ok:
            return answer, usage, True, citations, attempt, ""

        extra_instruction = (
            "你上一轮输出不合规，原因："
            f"{message}。请严格重写，必须包含“回答：”和“引用来源：”，"
            "并且每条引用都使用 - [chunk-<id>] <原文连续子串>。"
        )

    return last_answer, last_usage, False, last_citations, max_attempts, last_error


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    if args.overlap >= args.chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    if args.top_k <= 0:
        raise ValueError("top-k must be > 0")
    if args.max_attempts <= 0:
        raise ValueError("max-attempts must be > 0")

    corpus_file = resolve_project_path(args.corpus_file)
    queries_file = resolve_project_path(args.queries_file)
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
    chunk_vectors = [embedding_vector(emb_client, emb_api_key, emb_base_url, embedding_model, chunk) for chunk in chunks]

    matrix = np.asarray(chunk_vectors, dtype=np.float32)
    matrix = l2_normalize(matrix)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)

    system_prompt = load_system_prompt(system_prompt_file)
    qa_api_key, qa_base_url, qa_client = build_client()

    queries = read_queries(queries_file)
    rows: list[dict] = []
    for query in queries:
        hits = retrieve_hits(
            index=index,
            chunks=chunks,
            query=query,
            top_k=args.top_k,
            embedding_client=emb_client,
            api_key=emb_api_key,
            base_url=emb_base_url,
            embedding_model=embedding_model,
        )

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

        row = {
            "timestamp_utc": utc_now_iso(),
            "query": query,
            "hits": hits,
            "answer": answer,
            "citations": citations,
            "citation_valid": citation_valid,
            "validation_error": validation_error,
            "attempt_count": attempt_count,
            "embedding_model": embedding_model,
            "qa_model": qa_model,
            "top_k": args.top_k,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
        rows.append(row)
        append_jsonl(jsonl_file, row)

    write_report(report_file, args, rows)

    print("Done. Day11 KB QA with citations generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()