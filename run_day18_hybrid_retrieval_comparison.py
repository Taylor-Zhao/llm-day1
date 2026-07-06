#!/usr/bin/env python3
"""Day 18：加入多路召回（关键词 + 向量）并对比效果。

对比模式：
- vector_only：仅向量召回
- hybrid：关键词召回 + 向量召回融合（RRF）

输出：
- Markdown 报告：experiments/day18_hybrid_retrieval_comparison.md
- CSV 汇总：experiments/day18_hybrid_retrieval_comparison.csv
- JSONL 明细：logs/day18_hybrid_retrieval_comparison.jsonl
"""

import argparse
import csv
import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

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
from run_day11_kb_qa_with_citations import answer_with_retry, retrieve_hits
from run_day13_reranker_comparison import rerank_hits

try:
    import faiss  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("faiss is not installed. Run: ./.venv/bin/pip install faiss-cpu") from exc

PROJECT_DIR = Path(__file__).resolve().parent


def resolve_project_path(path_str: str) -> Path:
    # 将相对路径统一解析到项目根目录，避免不同 cwd 下路径错位。
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    # Day18 参数分为三类：检索参数、评测参数、稳定性参数（超时/重试）。
    parser = argparse.ArgumentParser(description="Run Day18 hybrid retrieval comparison")
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
    parser.add_argument("--qa-timeout-seconds", type=float, default=90.0, help="问答与改写请求超时秒数")
    parser.add_argument("--temperature", type=float, default=0.2, help="问答温度")
    parser.add_argument("--max-tokens", type=int, default=900, help="问答输出上限")
    parser.add_argument("--max-attempts", type=int, default=2, help="引用校验失败时最多重试次数")
    parser.add_argument("--eval-retries", type=int, default=2, help="单样本评测失败时额外重试次数")
    parser.add_argument("--eval-retry-backoff", type=float, default=1.5, help="评测重试退避秒数")
    parser.add_argument("--limit", type=int, default=0, help="仅评测前 N 条，0 表示全部")
    parser.add_argument("--report", default="experiments/day18_hybrid_retrieval_comparison.md", help="Markdown 报告")
    parser.add_argument("--csv", default="experiments/day18_hybrid_retrieval_comparison.csv", help="CSV 汇总")
    parser.add_argument("--jsonl", default="logs/day18_hybrid_retrieval_comparison.jsonl", help="JSONL 明细")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict) -> None:
    # 逐条落盘 JSONL，便于后续按样本复盘或增量分析。
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    # 保持与 Day8/Day16 同口径切分，确保跨天指标可比。
    tokens = tokenize_words(text)
    chunks = build_chunks(tokens, chunk_size, overlap)
    return ["".join(chunk) for chunk in chunks]


def load_eval_cases(path: Path, limit: int) -> list[dict[str, Any]]:
    # 读取 Day15 评测集；limit>0 时仅截取前 N 条用于冒烟。
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    if not isinstance(cases, list):
        raise ValueError("evalset JSON field 'cases' must be a list")
    return cases[:limit] if limit > 0 else cases


def build_keyword_stats(chunks: list[str]) -> tuple[list[set[str]], dict[str, float]]:
    # 为关键词召回预先构建：
    # 1) 每个 chunk 的 term set
    # 2) 基于 chunk 文档频次的 IDF 词权重
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


def retrieve_keyword_hits(
    *,
    query: str,
    chunks: list[str],
    chunk_term_sets: list[set[str]],
    idf: dict[str, float],
    top_n: int,
) -> list[dict]:
    # 关键词路由：用 query term 与 chunk term 的匹配强度（加权 IDF）打分。
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
    # 多路融合采用 RRF（Reciprocal Rank Fusion）：
    # 最终分数 = 各路 1/(k+rank) 的加权和，避免不同分数空间直接相加。
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


def text_contains_any_keyword(text: str, keywords: list[str]) -> bool:
    # 轻量命中判定：只要出现任一期望关键词即视为命中。
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
    # 在最终 top-k 前可选重排：
    # - 不开重排：直接截断候选
    # - 开重排：按语义+词重叠+名次先验再次排序
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
    # 与 Day16 指标口径保持一致，便于横向对比：
    # retrieval_hit_rate / citation_correct_rate / insufficient_correct_rate 等。
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
        "query_count": total,
        "answerable_cases": answerable_n,
        "unanswerable_cases": unanswerable_n,
        "retrieval_hit_rate": retrieval_hit / answerable_n if answerable_n else 0.0,
        "citation_correct_rate": citation_correct / answerable_n if answerable_n else 0.0,
        "insufficient_correct_rate": insufficient_correct / unanswerable_n if unanswerable_n else 0.0,
        "citation_format_valid_rate": citation_valid / total,
        "avg_attempt_count": attempt_total / total,
        "avg_total_tokens": token_total / total,
    }


def write_csv(path: Path, vector_summary: dict, hybrid_summary: dict) -> None:
    # 输出结构化汇总，方便后续做图或进一步统计。
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
        "avg_total_tokens",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"mode": "vector_only", **vector_summary})
        writer.writerow({"mode": "hybrid", **hybrid_summary})


def write_report(path: Path, args: argparse.Namespace, vector_summary: dict, hybrid_summary: dict) -> None:
    # 输出人读报告：包含两种模式的绝对值与差分值（hybrid - vector_only）。
    path.parent.mkdir(parents=True, exist_ok=True)

    delta_hit = hybrid_summary["retrieval_hit_rate"] - vector_summary["retrieval_hit_rate"]
    delta_cite = hybrid_summary["citation_correct_rate"] - vector_summary["citation_correct_rate"]
    delta_ins = hybrid_summary["insufficient_correct_rate"] - vector_summary["insufficient_correct_rate"]
    delta_fmt = hybrid_summary["citation_format_valid_rate"] - vector_summary["citation_format_valid_rate"]
    delta_tok = hybrid_summary["avg_total_tokens"] - vector_summary["avg_total_tokens"]

    lines = [
        "# Day 18 多路召回对比报告（Vector-Only vs Hybrid）",
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
        f"- hybrid vector/keyword weight：{args.hybrid_vector_weight}/{args.hybrid_keyword_weight}",
        "",
        "## 汇总对比",
        "",
        "| mode | query_count | retrieval_hit_rate | citation_correct_rate | insufficient_correct_rate | citation_format_valid_rate | avg_total_tokens |",
        "|---|---:|---:|---:|---:|---:|---:|",
        (
            f"| vector_only | {vector_summary['query_count']} | {vector_summary['retrieval_hit_rate']:.3f} | "
            f"{vector_summary['citation_correct_rate']:.3f} | {vector_summary['insufficient_correct_rate']:.3f} | "
            f"{vector_summary['citation_format_valid_rate']:.3f} | {vector_summary['avg_total_tokens']:.1f} |"
        ),
        (
            f"| hybrid | {hybrid_summary['query_count']} | {hybrid_summary['retrieval_hit_rate']:.3f} | "
            f"{hybrid_summary['citation_correct_rate']:.3f} | {hybrid_summary['insufficient_correct_rate']:.3f} | "
            f"{hybrid_summary['citation_format_valid_rate']:.3f} | {hybrid_summary['avg_total_tokens']:.1f} |"
        ),
        "",
        "## 指标变化（hybrid - vector_only）",
        "",
        f"- retrieval_hit_rate: {delta_hit:+.3f}",
        f"- citation_correct_rate: {delta_cite:+.3f}",
        f"- insufficient_correct_rate: {delta_ins:+.3f}",
        f"- citation_format_valid_rate: {delta_fmt:+.3f}",
        f"- avg_total_tokens: {delta_tok:+.1f}",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


def evaluate_case(
    *,
    args: argparse.Namespace,
    case: dict,
    mode: str,
    original_question: str,
    candidates: list[dict],
    qa_client,
    qa_api_key: str,
    qa_base_url: str,
    qa_model: str,
    system_prompt: str,
) -> dict:
    # 单样本评测：
    # 1) 选取最终 hits
    # 2) 调用带引用校验的问答
    # 3) 计算命中/引用正确/拒答正确等标志位
    answerable = bool(case.get("answerable"))
    expected_keywords = case.get("expected_source_keywords") or []
    if not isinstance(expected_keywords, list):
        expected_keywords = []
    expected_keywords = [str(x) for x in expected_keywords if str(x).strip()]

    hits = choose_hits(args, original_question, candidates)

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
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


def build_error_row(mode: str, case: dict, question: str, error_text: str) -> dict:
    # 当单样本多次重试仍失败时，降级为错误行而不是中断整次评测。
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
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def main() -> None:
    # 主流程：
    # - 构建向量索引与关键词统计
    # - 逐样本执行 vector_only / hybrid 双模式评测
    # - 聚合指标并输出报告
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
    # 提高请求超时阈值，降低长响应场景下的误判失败。
    qa_client.timeout = httpx.Timeout(args.qa_timeout_seconds)
    system_prompt = load_system_prompt(system_prompt_file)

    chunk_vectors = [embedding_vector(emb_client, emb_api_key, emb_base_url, embedding_model, chunk) for chunk in chunks]
    matrix = np.asarray(chunk_vectors, dtype=np.float32)
    matrix = l2_normalize(matrix)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)

    chunk_term_sets, idf = build_keyword_stats(chunks)

    vector_rows: list[dict] = []
    hybrid_rows: list[dict] = []

    for case in cases:
        question = str(case.get("question") or "").strip()

        vector_candidates = retrieve_hits(
            index=index,
            chunks=chunks,
            query=question,
            top_k=args.candidate_top_n,
            embedding_client=emb_client,
            api_key=emb_api_key,
            base_url=emb_base_url,
            embedding_model=embedding_model,
        )

        keyword_candidates = retrieve_keyword_hits(
            query=question,
            chunks=chunks,
            chunk_term_sets=chunk_term_sets,
            idf=idf,
            top_n=args.candidate_top_n,
        )

        hybrid_candidates = fuse_candidates_rrf(
            chunks=chunks,
            vector_hits=vector_candidates,
            keyword_hits=keyword_candidates,
            top_n=args.candidate_top_n,
            rrf_k=args.rrf_k,
            vector_weight=args.hybrid_vector_weight,
            keyword_weight=args.hybrid_keyword_weight,
        )

        # vector_only 路由：单样本失败时按配置重试，最终仍失败则记错误行。
        vector_row = None
        last_vector_error = ""
        for retry_idx in range(args.eval_retries + 1):
            try:
                vector_row = evaluate_case(
                    args=args,
                    case=case,
                    mode="vector_only",
                    original_question=question,
                    candidates=vector_candidates,
                    qa_client=qa_client,
                    qa_api_key=qa_api_key,
                    qa_base_url=qa_base_url,
                    qa_model=qa_model,
                    system_prompt=system_prompt,
                )
                break
            except Exception as exc:  # pragma: no cover
                last_vector_error = str(exc)
                if retry_idx < args.eval_retries and args.eval_retry_backoff > 0:
                    time.sleep(args.eval_retry_backoff)
        if vector_row is None:
            vector_row = build_error_row("vector_only", case, question, f"eval_error: {last_vector_error}")
        vector_row["keyword_candidates"] = keyword_candidates
        vector_row["vector_candidates"] = vector_candidates
        vector_rows.append(vector_row)
        append_jsonl(jsonl_file, vector_row)

        # hybrid 路由：与 vector_only 相同的重试/降级策略，确保公平对比。
        hybrid_row = None
        last_hybrid_error = ""
        for retry_idx in range(args.eval_retries + 1):
            try:
                hybrid_row = evaluate_case(
                    args=args,
                    case=case,
                    mode="hybrid",
                    original_question=question,
                    candidates=hybrid_candidates,
                    qa_client=qa_client,
                    qa_api_key=qa_api_key,
                    qa_base_url=qa_base_url,
                    qa_model=qa_model,
                    system_prompt=system_prompt,
                )
                break
            except Exception as exc:  # pragma: no cover
                last_hybrid_error = str(exc)
                if retry_idx < args.eval_retries and args.eval_retry_backoff > 0:
                    time.sleep(args.eval_retry_backoff)
        if hybrid_row is None:
            hybrid_row = build_error_row("hybrid", case, question, f"eval_error: {last_hybrid_error}")
        hybrid_row["keyword_candidates"] = keyword_candidates
        hybrid_row["vector_candidates"] = vector_candidates
        hybrid_row["hybrid_candidates"] = hybrid_candidates
        hybrid_rows.append(hybrid_row)
        append_jsonl(jsonl_file, hybrid_row)

    vector_summary = compute_mode_summary(vector_rows)
    hybrid_summary = compute_mode_summary(hybrid_rows)

    write_csv(csv_file, vector_summary, hybrid_summary)
    write_report(report_file, args, vector_summary, hybrid_summary)

    print("Done. Day18 hybrid retrieval comparison generated.")
    print(f"Report => {args.report}")
    print(f"CSV    => {args.csv}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()
