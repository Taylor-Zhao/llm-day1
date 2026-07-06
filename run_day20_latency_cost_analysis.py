#!/usr/bin/env python3
"""Day 20：延迟与成本统计（每次请求 token 与耗时）。

对比模式：
- baseline：不启用缓存与显式去重
- cache_dedup：启用查询缓存 + 显式候选去重

输出：
- Markdown 报告：experiments/day20_latency_cost_analysis.md
- CSV 汇总：experiments/day20_latency_cost_analysis.csv
- JSONL 明细：logs/day20_latency_cost_analysis.jsonl
"""

import argparse
import copy
import csv
import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import httpx
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
from run_day11_kb_qa_with_citations import answer_with_retry
from run_day13_reranker_comparison import rerank_hits

try:
    import faiss  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("faiss is not installed. Run: ./.venv/bin/pip install faiss-cpu") from exc

PROJECT_DIR = Path(__file__).resolve().parent


class EvalCaches:
    """按查询归档缓存，避免重复计算。"""

    def __init__(self) -> None:
        self.query_embedding: dict[str, np.ndarray] = {}
        self.vector_candidates: dict[str, list[dict]] = {}
        self.keyword_candidates: dict[str, list[dict]] = {}
        self.hybrid_candidates: dict[str, list[dict]] = {}


class CacheProbe:
    """记录单样本缓存命中情况。"""

    def __init__(self) -> None:
        self.lookup_count = 0
        self.hit_count = 0

    def lookup(self, hit: bool) -> None:
        self.lookup_count += 1
        if hit:
            self.hit_count += 1


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day20 latency and cost analysis")
    parser.add_argument("--corpus-file", default="inputs/day8_corpus_backend_notes.txt", help="语料文件")
    parser.add_argument("--evalset-file", default="inputs/day15_evalset_qa.json", help="评测集 JSON")
    parser.add_argument("--system-prompt", default="prompts/day11_kb_qa_with_citations_cn.txt", help="问答系统提示词")
    parser.add_argument("--chunk-size", type=int, default=80, help="切分 chunk size")
    parser.add_argument("--overlap", type=int, default=20, help="切分 overlap")
    parser.add_argument("--top-k", type=int, default=3, help="最终给问答模型的 chunk 数")
    parser.add_argument("--candidate-top-n", type=int, default=8, help="候选召回数")
    parser.add_argument("--use-rerank", action="store_true", help="是否启用重排")
    parser.add_argument("--rerank-alpha", type=float, default=0.70, help="重排语义分权重")
    parser.add_argument("--rerank-beta", type=float, default=0.25, help="重排词重叠权重")
    parser.add_argument("--rerank-gamma", type=float, default=0.05, help="重排名次先验权重")
    parser.add_argument("--hybrid-vector-weight", type=float, default=0.70, help="融合时向量路由权重")
    parser.add_argument("--hybrid-keyword-weight", type=float, default=0.30, help="融合时关键词路由权重")
    parser.add_argument("--rrf-k", type=int, default=60, help="RRF 融合常数")
    parser.add_argument("--embedding-model", default="", help="可选：覆盖 OPENAI_EMBEDDING_MODEL")
    parser.add_argument("--model", default="", help="问答模型（覆盖 OPENAI_MODEL）")
    parser.add_argument("--qa-timeout-seconds", type=float, default=90.0, help="问答请求超时秒数")
    parser.add_argument("--temperature", type=float, default=0.2, help="问答温度")
    parser.add_argument("--max-tokens", type=int, default=900, help="问答输出上限")
    parser.add_argument("--max-attempts", type=int, default=2, help="引用校验失败时最多重试次数")
    parser.add_argument("--eval-retries", type=int, default=2, help="单样本评测失败时额外重试次数")
    parser.add_argument("--eval-retry-backoff", type=float, default=1.5, help="评测重试退避秒数")
    parser.add_argument("--limit", type=int, default=0, help="仅评测前 N 条，0 表示全部")
    parser.add_argument("--report", default="experiments/day20_latency_cost_analysis.md", help="Markdown 报告")
    parser.add_argument("--csv", default="experiments/day20_latency_cost_analysis.csv", help="CSV 汇总")
    parser.add_argument("--jsonl", default="logs/day20_latency_cost_analysis.jsonl", help="JSONL 明细")
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
    return cases[:limit] if limit > 0 else cases


def normalize_query_key(query: str) -> str:
    return " ".join(query.strip().lower().split())


def build_keyword_stats(chunks: list[str]) -> tuple[list[set[str]], dict[str, float]]:
    chunk_term_sets: list[set[str]] = []
    df_counter: Counter[str] = Counter()

    for text in chunks:
        term_set = set(tokenize_words(text))
        chunk_term_sets.append(term_set)
        for term in term_set:
            df_counter[term] += 1

    n = len(chunks)
    idf: dict[str, float] = {}
    for term, df in df_counter.items():
        idf[term] = math.log((n + 1.0) / (df + 1.0)) + 1.0
    return chunk_term_sets, idf


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def summarize_metric(rows: list[dict], key: str) -> dict[str, float]:
    values = [float(r.get(key) or 0.0) for r in rows]
    if not values:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "avg": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


def retrieve_keyword_hits(
    *,
    query: str,
    chunks: list[str],
    chunk_term_sets: list[set[str]],
    idf: dict[str, float],
    top_n: int,
) -> list[dict]:
    query_terms = set(tokenize_words(query))
    if not query_terms:
        return []

    denom = sum(idf.get(term, 1.0) for term in query_terms)
    if denom <= 0:
        denom = float(len(query_terms))

    scored: list[tuple[int, float]] = []
    for chunk_id, term_set in enumerate(chunk_term_sets):
        matched = query_terms & term_set
        if not matched:
            continue
        score = sum(idf.get(term, 1.0) for term in matched) / denom
        if score > 0:
            scored.append((chunk_id, float(score)))

    scored.sort(key=lambda x: x[1], reverse=True)
    out: list[dict] = []
    for rank, (chunk_id, score) in enumerate(scored[:top_n], start=1):
        out.append(
            {
                "rank": rank,
                "score": score,
                "keyword_score": score,
                "chunk_id": int(chunk_id),
                "text": chunks[chunk_id],
            }
        )
    return out


def retrieve_vector_hits(
    *,
    query: str,
    index,
    chunks: list[str],
    embedding_client,
    api_key: str,
    base_url: str,
    embedding_model: str,
    top_n: int,
    caches: EvalCaches,
    probe: Optional[CacheProbe],
    enable_cache: bool,
) -> tuple[list[dict], int]:
    query_key = normalize_query_key(query)
    cache_key = f"{embedding_model}|{top_n}|{query_key}"

    if enable_cache:
        hit = cache_key in caches.vector_candidates
        if probe is not None:
            probe.lookup(hit)
        if hit:
            return copy.deepcopy(caches.vector_candidates[cache_key]), 0

    emb_hit = query_key in caches.query_embedding if enable_cache else False
    if probe is not None and enable_cache:
        probe.lookup(emb_hit)

    embedding_request_count = 0
    if emb_hit:
        qvec = caches.query_embedding[query_key]
    else:
        qvec = np.asarray(
            embedding_vector(embedding_client, api_key, base_url, embedding_model, query),
            dtype=np.float32,
        )
        embedding_request_count = 1
        if qvec.ndim != 1:
            qvec = qvec.reshape(-1)
        qvec = l2_normalize(qvec[np.newaxis, :])[0]
        if enable_cache:
            caches.query_embedding[query_key] = qvec

    distances, indices = index.search(qvec[np.newaxis, :], top_n)
    out: list[dict] = []
    for rank, (chunk_id, score) in enumerate(zip(indices[0].tolist(), distances[0].tolist()), start=1):
        if chunk_id < 0:
            continue
        cid = int(chunk_id)
        out.append(
            {
                "rank": rank,
                "score": float(score),
                "chunk_id": cid,
                "text": chunks[cid],
            }
        )

    if enable_cache:
        caches.vector_candidates[cache_key] = copy.deepcopy(out)
    return out, embedding_request_count


def retrieve_keyword_hits_with_cache(
    *,
    query: str,
    chunks: list[str],
    chunk_term_sets: list[set[str]],
    idf: dict[str, float],
    top_n: int,
    caches: EvalCaches,
    probe: Optional[CacheProbe],
    enable_cache: bool,
) -> list[dict]:
    query_key = normalize_query_key(query)
    cache_key = f"{top_n}|{query_key}"

    if enable_cache:
        hit = cache_key in caches.keyword_candidates
        if probe is not None:
            probe.lookup(hit)
        if hit:
            return copy.deepcopy(caches.keyword_candidates[cache_key])

    out = retrieve_keyword_hits(
        query=query,
        chunks=chunks,
        chunk_term_sets=chunk_term_sets,
        idf=idf,
        top_n=top_n,
    )
    if enable_cache:
        caches.keyword_candidates[cache_key] = copy.deepcopy(out)
    return out


def fuse_candidates_rrf(
    *,
    chunks: list[str],
    vector_hits: list[dict],
    keyword_hits: list[dict],
    top_n: int,
    rrf_k: int,
    vector_weight: float,
    keyword_weight: float,
) -> list[dict]:
    v_rank = {int(hit["chunk_id"]): int(hit["rank"]) for hit in vector_hits}
    k_rank = {int(hit["chunk_id"]): int(hit["rank"]) for hit in keyword_hits}
    v_score = {int(hit["chunk_id"]): float(hit.get("score") or 0.0) for hit in vector_hits}
    k_score = {int(hit["chunk_id"]): float(hit.get("keyword_score", hit.get("score") or 0.0)) for hit in keyword_hits}

    all_ids = set(v_rank.keys()) | set(k_rank.keys())
    if not all_ids:
        return []

    fused: list[dict] = []
    for cid in all_ids:
        score = 0.0
        if cid in v_rank:
            score += vector_weight * (1.0 / (rrf_k + v_rank[cid]))
        if cid in k_rank:
            score += keyword_weight * (1.0 / (rrf_k + k_rank[cid]))
        fused.append(
            {
                "chunk_id": cid,
                "score": score,
                "vector_score": v_score.get(cid, 0.0),
                "keyword_score": k_score.get(cid, 0.0),
                "text": chunks[cid],
            }
        )

    fused.sort(key=lambda x: x["score"], reverse=True)
    out: list[dict] = []
    for rank, item in enumerate(fused[:top_n], start=1):
        row = dict(item)
        row["rank"] = rank
        out.append(row)
    return out


def fuse_candidates_with_cache(
    *,
    query: str,
    chunks: list[str],
    vector_hits: list[dict],
    keyword_hits: list[dict],
    top_n: int,
    rrf_k: int,
    vector_weight: float,
    keyword_weight: float,
    caches: EvalCaches,
    probe: Optional[CacheProbe],
    enable_cache: bool,
) -> list[dict]:
    query_key = normalize_query_key(query)
    cache_key = f"{top_n}|{rrf_k}|{vector_weight:.4f}|{keyword_weight:.4f}|{query_key}"

    if enable_cache:
        hit = cache_key in caches.hybrid_candidates
        if probe is not None:
            probe.lookup(hit)
        if hit:
            return copy.deepcopy(caches.hybrid_candidates[cache_key])

    out = fuse_candidates_rrf(
        chunks=chunks,
        vector_hits=vector_hits,
        keyword_hits=keyword_hits,
        top_n=top_n,
        rrf_k=rrf_k,
        vector_weight=vector_weight,
        keyword_weight=keyword_weight,
    )
    if enable_cache:
        caches.hybrid_candidates[cache_key] = copy.deepcopy(out)
    return out


def dedup_candidates(candidates: list[dict]) -> tuple[list[dict], int]:
    seen: set[int] = set()
    out: list[dict] = []
    removed = 0

    for item in candidates:
        cid = int(item.get("chunk_id", -1))
        if cid < 0:
            removed += 1
            continue
        if cid in seen:
            removed += 1
            continue
        seen.add(cid)
        out.append(dict(item))

    for rank, item in enumerate(out, start=1):
        item["rank"] = rank
    return out, removed


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


def choose_hits(args: argparse.Namespace, retrieval_query: str, candidates: list[dict]) -> list[dict]:
    if args.use_rerank:
        return rerank_hits(
            query=retrieval_query,
            candidates=candidates,
            top_k=args.top_k,
            alpha=args.rerank_alpha,
            beta=args.rerank_beta,
            gamma=args.rerank_gamma,
        )
    return [dict(hit) for hit in candidates[: args.top_k]]


def compute_mode_summary(rows: list[dict]) -> dict:
    total = len(rows)
    if total == 0:
        return {
            "query_count": 0,
            "answerable_cases": 0,
            "unanswerable_cases": 0,
            "retrieval_hit_rate": 0.0,
            "citation_correct_rate": 0.0,
            "insufficient_correct_rate": 0.0,
            "citation_format_valid_rate": 0.0,
            "avg_attempt_count": 0.0,
            "qa_total_tokens_avg": 0.0,
            "qa_total_tokens_p50": 0.0,
            "qa_total_tokens_p95": 0.0,
            "end_to_end_ms_avg": 0.0,
            "end_to_end_ms_p50": 0.0,
            "end_to_end_ms_p95": 0.0,
            "qa_ms_avg": 0.0,
            "qa_ms_p50": 0.0,
            "qa_ms_p95": 0.0,
            "embedding_ms_avg": 0.0,
            "cache_hit_rate": 0.0,
            "avg_dedup_removed": 0.0,
            "avg_embedding_request_count": 0.0,
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

    cache_lookup_total = sum(int(r.get("cache_lookup_count") or 0) for r in rows)
    cache_hit_total = sum(int(r.get("cache_hit_count") or 0) for r in rows)
    dedup_removed_total = sum(int(r.get("dedup_removed_count") or 0) for r in rows)
    embedding_request_total = sum(int(r.get("embedding_request_count") or 0) for r in rows)

    token_stats = summarize_metric(rows, "qa_total_tokens")
    end_to_end_stats = summarize_metric(rows, "end_to_end_ms")
    qa_stats = summarize_metric(rows, "qa_ms")
    embedding_stats = summarize_metric(rows, "embedding_ms")

    return {
        "query_count": total,
        "answerable_cases": answerable_n,
        "unanswerable_cases": unanswerable_n,
        "retrieval_hit_rate": retrieval_hit / answerable_n if answerable_n else 0.0,
        "citation_correct_rate": citation_correct / answerable_n if answerable_n else 0.0,
        "insufficient_correct_rate": insufficient_correct / unanswerable_n if unanswerable_n else 0.0,
        "citation_format_valid_rate": citation_valid / total,
        "avg_attempt_count": attempt_total / total,
        "qa_total_tokens_avg": token_stats["avg"],
        "qa_total_tokens_p50": token_stats["p50"],
        "qa_total_tokens_p95": token_stats["p95"],
        "end_to_end_ms_avg": end_to_end_stats["avg"],
        "end_to_end_ms_p50": end_to_end_stats["p50"],
        "end_to_end_ms_p95": end_to_end_stats["p95"],
        "qa_ms_avg": qa_stats["avg"],
        "qa_ms_p50": qa_stats["p50"],
        "qa_ms_p95": qa_stats["p95"],
        "embedding_ms_avg": embedding_stats["avg"],
        "cache_hit_rate": (cache_hit_total / cache_lookup_total) if cache_lookup_total else 0.0,
        "avg_dedup_removed": dedup_removed_total / total,
        "avg_embedding_request_count": embedding_request_total / total,
    }


def write_csv(path: Path, baseline_summary: dict, cache_dedup_summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "mode",
        "query_count",
        "answerable_cases",
        "unanswerable_cases",
        "retrieval_hit_rate",
        "citation_correct_rate",
        "insufficient_correct_rate",
        "citation_format_valid_rate",
        "avg_attempt_count",
        "qa_total_tokens_avg",
        "qa_total_tokens_p50",
        "qa_total_tokens_p95",
        "end_to_end_ms_avg",
        "end_to_end_ms_p50",
        "end_to_end_ms_p95",
        "qa_ms_avg",
        "qa_ms_p50",
        "qa_ms_p95",
        "embedding_ms_avg",
        "cache_hit_rate",
        "avg_dedup_removed",
        "avg_embedding_request_count",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"mode": "baseline", **baseline_summary})
        writer.writerow({"mode": "cache_dedup", **cache_dedup_summary})


def write_report(path: Path, args: argparse.Namespace, baseline_summary: dict, cache_dedup_summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    delta_qa_tokens = cache_dedup_summary["qa_total_tokens_avg"] - baseline_summary["qa_total_tokens_avg"]
    delta_e2e = cache_dedup_summary["end_to_end_ms_avg"] - baseline_summary["end_to_end_ms_avg"]
    delta_qa = cache_dedup_summary["qa_ms_avg"] - baseline_summary["qa_ms_avg"]
    delta_embedding = cache_dedup_summary["embedding_ms_avg"] - baseline_summary["embedding_ms_avg"]

    lines = [
        "# Day 20 延迟与成本统计报告（Per-Request Token + Latency）",
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
        "## 统计口径",
        "",
        "- QA token：精确取自问答请求 usage（input/output/total）。",
        "- embedding 成本：当前仅统计请求次数与耗时；不统计 token，因为 embedding 接口未返回 usage。",
        "- 延迟拆分：vector_retrieval_ms / keyword_retrieval_ms / fusion_ms / dedup_ms / choose_hits_ms / qa_ms / end_to_end_ms。",
        "",
        "## 汇总对比",
        "",
        "| mode | query_count | qa_total_tokens_avg | qa_total_tokens_p95 | end_to_end_ms_avg | end_to_end_ms_p95 | qa_ms_avg | qa_ms_p95 | embedding_ms_avg | avg_embedding_request_count | cache_hit_rate | avg_dedup_removed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| baseline | {baseline_summary['query_count']} | {baseline_summary['qa_total_tokens_avg']:.1f} | "
            f"{baseline_summary['qa_total_tokens_p95']:.1f} | {baseline_summary['end_to_end_ms_avg']:.1f} | "
            f"{baseline_summary['end_to_end_ms_p95']:.1f} | {baseline_summary['qa_ms_avg']:.1f} | "
            f"{baseline_summary['qa_ms_p95']:.1f} | {baseline_summary['embedding_ms_avg']:.1f} | "
            f"{baseline_summary['avg_embedding_request_count']:.2f} | {baseline_summary['cache_hit_rate']:.3f} | "
            f"{baseline_summary['avg_dedup_removed']:.2f} |"
        ),
        (
            f"| cache_dedup | {cache_dedup_summary['query_count']} | {cache_dedup_summary['qa_total_tokens_avg']:.1f} | "
            f"{cache_dedup_summary['qa_total_tokens_p95']:.1f} | {cache_dedup_summary['end_to_end_ms_avg']:.1f} | "
            f"{cache_dedup_summary['end_to_end_ms_p95']:.1f} | {cache_dedup_summary['qa_ms_avg']:.1f} | "
            f"{cache_dedup_summary['qa_ms_p95']:.1f} | {cache_dedup_summary['embedding_ms_avg']:.1f} | "
            f"{cache_dedup_summary['avg_embedding_request_count']:.2f} | {cache_dedup_summary['cache_hit_rate']:.3f} | "
            f"{cache_dedup_summary['avg_dedup_removed']:.2f} |"
        ),
        "",
        "## 指标变化（cache_dedup - baseline）",
        "",
        f"- qa_total_tokens_avg: {delta_qa_tokens:+.1f}",
        f"- end_to_end_ms_avg: {delta_e2e:+.1f}",
        f"- qa_ms_avg: {delta_qa:+.1f}",
        f"- embedding_ms_avg: {delta_embedding:+.1f}",
        f"- cache_hit_rate(cache_dedup): {cache_dedup_summary['cache_hit_rate']:.3f}",
        f"- avg_embedding_request_count(cache_dedup): {cache_dedup_summary['avg_embedding_request_count']:.2f}",
        f"- avg_dedup_removed(cache_dedup): {cache_dedup_summary['avg_dedup_removed']:.2f}",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


def evaluate_case(
    *,
    args: argparse.Namespace,
    case: dict,
    mode: str,
    original_question: str,
    qa_client,
    qa_api_key: str,
    qa_base_url: str,
    qa_model: str,
    system_prompt: str,
    index,
    chunks: list[str],
    chunk_term_sets: list[set[str]],
    idf: dict[str, float],
    emb_client,
    emb_api_key: str,
    emb_base_url: str,
    embedding_model: str,
    caches: EvalCaches,
) -> dict:
    answerable = bool(case.get("answerable"))
    expected_keywords = case.get("expected_source_keywords") or []
    if not isinstance(expected_keywords, list):
        expected_keywords = []
    expected_keywords = [str(x) for x in expected_keywords if str(x).strip()]

    mode_cache_enabled = mode == "cache_dedup"
    probe = CacheProbe()

    t0 = time.perf_counter()

    vector_t0 = time.perf_counter()
    vector_candidates, embedding_request_count = retrieve_vector_hits(
        query=original_question,
        index=index,
        chunks=chunks,
        embedding_client=emb_client,
        api_key=emb_api_key,
        base_url=emb_base_url,
        embedding_model=embedding_model,
        top_n=args.candidate_top_n,
        caches=caches,
        probe=probe,
        enable_cache=mode_cache_enabled,
    )
    vector_ms = (time.perf_counter() - vector_t0) * 1000.0

    keyword_t0 = time.perf_counter()
    keyword_candidates = retrieve_keyword_hits_with_cache(
        query=original_question,
        chunks=chunks,
        chunk_term_sets=chunk_term_sets,
        idf=idf,
        top_n=args.candidate_top_n,
        caches=caches,
        probe=probe,
        enable_cache=mode_cache_enabled,
    )
    keyword_ms = (time.perf_counter() - keyword_t0) * 1000.0

    fusion_t0 = time.perf_counter()
    hybrid_candidates = fuse_candidates_with_cache(
        query=original_question,
        chunks=chunks,
        vector_hits=vector_candidates,
        keyword_hits=keyword_candidates,
        top_n=args.candidate_top_n,
        rrf_k=args.rrf_k,
        vector_weight=args.hybrid_vector_weight,
        keyword_weight=args.hybrid_keyword_weight,
        caches=caches,
        probe=probe,
        enable_cache=mode_cache_enabled,
    )
    fusion_ms = (time.perf_counter() - fusion_t0) * 1000.0

    dedup_removed_count = 0
    dedup_ms = 0.0
    if mode_cache_enabled:
        dedup_t0 = time.perf_counter()
        deduped_candidates, dedup_removed_count = dedup_candidates(hybrid_candidates)
        dedup_ms = (time.perf_counter() - dedup_t0) * 1000.0
        final_candidates = deduped_candidates
    else:
        final_candidates = hybrid_candidates

    choose_t0 = time.perf_counter()
    hits = choose_hits(args, original_question, final_candidates)
    choose_hits_ms = (time.perf_counter() - choose_t0) * 1000.0

    qa_t0 = time.perf_counter()
    answer, usage, citation_valid, citations, attempt_count, validation_error = answer_with_retry(
        query=original_question,
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
    qa_ms = (time.perf_counter() - qa_t0) * 1000.0

    end_to_end_ms = (time.perf_counter() - t0) * 1000.0

    retrieval_hit = hits_match_keywords(hits, expected_keywords)
    citation_keyword_match = citation_chunks_match_keywords(hits, citations, expected_keywords)
    citation_correct = bool(answerable and citation_valid and citation_keyword_match)
    insufficient_correct = bool((not answerable) and ("当前信息不足" in answer))

    return {
        "timestamp_utc": utc_now_iso(),
        "mode": mode,
        "id": str(case.get("id") or ""),
        "question": original_question,
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
        "qa_input_tokens": usage.get("input_tokens"),
        "qa_output_tokens": usage.get("output_tokens"),
        "qa_total_tokens": usage.get("total_tokens"),
        "end_to_end_ms": round(end_to_end_ms, 2),
        "vector_retrieval_ms": round(vector_ms, 2),
        "keyword_retrieval_ms": round(keyword_ms, 2),
        "fusion_ms": round(fusion_ms, 2),
        "dedup_ms": round(dedup_ms, 2),
        "choose_hits_ms": round(choose_hits_ms, 2),
        "qa_ms": round(qa_ms, 2),
        "embedding_ms": round(vector_ms, 2),
        "embedding_request_count": embedding_request_count,
        "cache_lookup_count": probe.lookup_count,
        "cache_hit_count": probe.hit_count,
        "dedup_removed_count": dedup_removed_count,
    }


def build_error_row(mode: str, case: dict, question: str, error_text: str) -> dict:
    return {
        "timestamp_utc": utc_now_iso(),
        "mode": mode,
        "id": str(case.get("id") or ""),
        "question": question,
        "answerable": bool(case.get("answerable")),
        "category": str(case.get("category") or ""),
        "difficulty": str(case.get("difficulty") or ""),
        "expected_source_keywords": case.get("expected_source_keywords") or [],
        "hits": [],
        "answer": "",
        "citations": [],
        "citation_valid": False,
        "validation_error": error_text,
        "attempt_count": 0,
        "retrieval_hit": False,
        "citation_correct": False,
        "insufficient_correct": False,
        "qa_input_tokens": None,
        "qa_output_tokens": None,
        "qa_total_tokens": None,
        "end_to_end_ms": 0.0,
        "vector_retrieval_ms": 0.0,
        "keyword_retrieval_ms": 0.0,
        "fusion_ms": 0.0,
        "dedup_ms": 0.0,
        "choose_hits_ms": 0.0,
        "qa_ms": 0.0,
        "embedding_ms": 0.0,
        "embedding_request_count": 0,
        "cache_lookup_count": 0,
        "cache_hit_count": 0,
        "dedup_removed_count": 0,
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
    if args.qa_timeout_seconds <= 0:
        raise ValueError("qa-timeout-seconds must be > 0")
    if args.eval_retries < 0:
        raise ValueError("eval-retries must be >= 0")
    if args.eval_retry_backoff < 0:
        raise ValueError("eval-retry-backoff must be >= 0")
    if args.hybrid_vector_weight < 0 or args.hybrid_keyword_weight < 0:
        raise ValueError("hybrid weights must be >= 0")
    if args.rrf_k <= 0:
        raise ValueError("rrf-k must be > 0")

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
    qa_client.timeout = httpx.Timeout(args.qa_timeout_seconds)
    system_prompt = load_system_prompt(system_prompt_file)

    chunk_vectors = [embedding_vector(emb_client, emb_api_key, emb_base_url, embedding_model, chunk) for chunk in chunks]
    matrix = np.asarray(chunk_vectors, dtype=np.float32)
    matrix = l2_normalize(matrix)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)

    chunk_term_sets, idf = build_keyword_stats(chunks)

    caches = EvalCaches()
    baseline_rows: list[dict] = []
    cache_dedup_rows: list[dict] = []

    total_cases = len(cases)
    for case_index, case in enumerate(cases, start=1):
        question = str(case.get("question") or "").strip()
        case_id = str(case.get("id") or "")

        for mode in ["baseline", "cache_dedup"]:
            row = None
            last_error = ""
            for retry_idx in range(args.eval_retries + 1):
                try:
                    row = evaluate_case(
                        args=args,
                        case=case,
                        mode=mode,
                        original_question=question,
                        qa_client=qa_client,
                        qa_api_key=qa_api_key,
                        qa_base_url=qa_base_url,
                        qa_model=qa_model,
                        system_prompt=system_prompt,
                        index=index,
                        chunks=chunks,
                        chunk_term_sets=chunk_term_sets,
                        idf=idf,
                        emb_client=emb_client,
                        emb_api_key=emb_api_key,
                        emb_base_url=emb_base_url,
                        embedding_model=embedding_model,
                        caches=caches,
                    )
                    break
                except Exception as exc:  # pragma: no cover
                    last_error = str(exc)
                    if retry_idx < args.eval_retries and args.eval_retry_backoff > 0:
                        time.sleep(args.eval_retry_backoff)

            if row is None:
                row = build_error_row(mode, case, question, f"eval_error: {last_error}")

            append_jsonl(jsonl_file, row)
            if mode == "baseline":
                baseline_rows.append(row)
            else:
                cache_dedup_rows.append(row)
            print(f"[{case_index}/{total_cases}] {case_id} mode={mode} done", flush=True)

    baseline_summary = compute_mode_summary(baseline_rows)
    cache_dedup_summary = compute_mode_summary(cache_dedup_rows)

    write_csv(csv_file, baseline_summary, cache_dedup_summary)
    write_report(report_file, args, baseline_summary, cache_dedup_summary)

    print("Done. Day20 latency and cost analysis generated.")
    print(f"Report => {args.report}")
    print(f"CSV    => {args.csv}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()