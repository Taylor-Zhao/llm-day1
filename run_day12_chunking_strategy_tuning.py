#!/usr/bin/env python3
"""Day 12：优化切分策略（chunk size、overlap）。

目标：
1. 网格遍历多组 chunk size / overlap 组合。
2. 对每组组合复用 Day11 的“带引用问答”链路做评估。
3. 汇总召回质量、回答合规率、信息不足率、token 成本等指标。
4. 自动给出“质量优先”和“成本优先”的推荐参数。

运行示例：
        ./.venv/bin/python run_day12_chunking_strategy_tuning.py \
            --embedding-model nomic-embed-text \
            --model qwen2.5:0.5b \
            --chunk-sizes 60,80,120,160 \
            --overlaps 10,20,40
"""

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from dotenv import load_dotenv

from chat_cli import build_client, load_system_prompt
from run_day8_chunking_experiment import (
    build_chunks,
    chunk_redundancy_ratio,
    lexical_cohesion,
    build_embedding_client,
    embedding_vector,
    load_text,
    parse_int_list,
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
    """将相对路径解析到项目目录下，避免不同 cwd 运行时找不到文件。"""
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    """解析 Day12 命令行参数。

    这里最重要的输入参数是：
    - chunk_sizes: 需要评估的 chunk size 候选集合。
    - overlaps: 需要评估的 overlap 候选集合。
    - max_attempts: 单个 query 在引用不合规时最多重试几次。
    """
    parser = argparse.ArgumentParser(description="Run Day12 chunking strategy tuning")
    parser.add_argument("--corpus-file", default="inputs/day8_corpus_backend_notes.txt", help="语料文件")
    parser.add_argument("--queries-file", default="inputs/day9_queries.txt", help="查询文件（每行一条）")
    parser.add_argument("--system-prompt", default="prompts/day11_kb_qa_with_citations_cn.txt", help="系统提示词")
    parser.add_argument("--chunk-sizes", default="60,80,120,160", help="chunk size 列表，逗号分隔")
    parser.add_argument("--overlaps", default="10,20,40", help="overlap 列表，逗号分隔")
    parser.add_argument("--top-k", type=int, default=3, help="每个 query 召回的 chunk 数")
    parser.add_argument("--embedding-model", default="", help="可选：覆盖 OPENAI_EMBEDDING_MODEL")
    parser.add_argument("--model", default="", help="问答模型（覆盖 OPENAI_MODEL）")
    parser.add_argument("--temperature", type=float, default=0.2, help="问答温度")
    parser.add_argument("--max-tokens", type=int, default=800, help="问答输出上限")
    parser.add_argument("--max-attempts", type=int, default=2, help="单 query 最多重试次数")
    parser.add_argument("--max-redundancy-for-cost", type=float, default=0.30, help="成本优先推荐允许的最大冗余率")
    parser.add_argument("--report", default="experiments/day12_chunking_strategy_tuning.md", help="Markdown 报告")
    parser.add_argument("--csv", default="experiments/day12_chunking_strategy_tuning.csv", help="CSV 汇总")
    parser.add_argument("--jsonl", default="logs/day12_chunking_strategy_tuning.jsonl", help="JSONL 明细")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict) -> None:
    """按 JSONL 格式追加一条明细记录。

    Day12 会把“每个参数组合下的每个 query 结果”都写进 JSONL，
    后续做离线分析、可视化或继续评测会很方便。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """按给定 chunk_size / overlap 对文本切分，返回字符串 chunk 列表。"""
    tokens = tokenize_words(text)
    chunks = build_chunks(tokens, chunk_size, overlap)
    return ["".join(chunk) for chunk in chunks]


def write_csv(path: Path, rows: list[dict]) -> None:
    """写出汇总 CSV，方便你用表格工具做横向比较。

    关键字段说明：
    - chunk_size: 当前评估组合的块大小。
    - overlap: 当前评估组合的块重叠量。
    - chunk_count: 该切分策略最终生成的 chunk 数量。
    - redundancy_ratio: 相邻块重复比例，越高说明冗余越大。
    - lexical_cohesion: 相邻块词汇连贯度，越高通常说明上下文衔接更好。
    - citation_valid_rate: 引用校验通过率，越高越好。
    - avg_attempt_count: 平均重试次数，越低越稳。
    - info_insufficient_rate: “当前信息不足”出现比例，越低越好。
    - avg_top1_score: 召回第一名 chunk 的平均相似度。
    - avg_total_tokens: 平均总 token 消耗，可近似看成本。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "chunk_size",
        "overlap",
        "chunk_count",
        "redundancy_ratio",
        "lexical_cohesion",
        "citation_valid_rate",
        "avg_attempt_count",
        "info_insufficient_rate",
        "avg_top1_score",
        "avg_total_tokens",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def score_quality(row: dict) -> tuple:
    """质量优先排序规则。

    排序优先级：
    1. 引用合规率高
    2. 信息不足率低
    3. top1 召回分高
    4. 平均重试次数低
    5. 词汇连贯度高
    """
    return (
        row["citation_valid_rate"],
        -row["info_insufficient_rate"],
        row["avg_top1_score"],
        -row["avg_attempt_count"],
        row["lexical_cohesion"],
    )


def score_cost(row: dict) -> tuple:
    """成本优先排序规则。

    排序优先级：
    1. 先保证引用合规率
    2. 再尽量降低 token 成本
    3. chunk 数量更少更好
    4. 冗余更低更好
    5. 在成本接近时优先更高召回分
    """
    return (
        row["citation_valid_rate"],
        -row["avg_total_tokens"],
        -row["chunk_count"],
        -row["redundancy_ratio"],
        row["avg_top1_score"],
    )


def choose_recommendations(rows: list[dict], max_redundancy_for_cost: float) -> Tuple[Optional[dict], Optional[dict]]:
    """从全部评估结果中选出两类推荐。

    返回：
    - quality_best: 质量优先推荐
    - cost_best: 成本优先推荐

    成本优先会先用 `max_redundancy_for_cost` 过滤高冗余组合，
    避免为了省 token 却引入明显重复上下文。
    """
    if not rows:
        return None, None
    quality_best = sorted(rows, key=score_quality, reverse=True)[0]
    cost_candidates = [row for row in rows if row["redundancy_ratio"] <= max_redundancy_for_cost]
    if cost_candidates:
        cost_best = sorted(cost_candidates, key=score_cost, reverse=True)[0]
    else:
        cost_best = sorted(rows, key=score_cost, reverse=True)[0]
    return quality_best, cost_best


def write_report(path: Path, args: argparse.Namespace, rows: list[dict], quality_best: Optional[dict], cost_best: Optional[dict]) -> None:
    """写出 Markdown 报告，给人直接阅读和比较。

    报告包含：
    - 输入参数
    - 各参数组合汇总表
    - 自动推荐结果
    - 指标解释
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 12 切分策略优化报告（自动生成）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 语料文件：{args.corpus_file}",
        f"- 查询文件：{args.queries_file}",
        f"- chunk_sizes：{args.chunk_sizes}",
        f"- overlaps：{args.overlaps}",
        f"- top-k：{args.top_k}",
        f"- embedding 模型：{args.embedding_model}",
        f"- QA 模型：{args.model}",
        f"- max_attempts：{args.max_attempts}",
        "",
        "## 汇总表",
        "",
        "| chunk_size | overlap | chunks | redundancy | cohesion | citation_valid_rate | avg_attempts | info_insufficient_rate | avg_top1_score | avg_total_tokens |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for row in rows:
        lines.append(
            f"| {row['chunk_size']} | {row['overlap']} | {row['chunk_count']} | {row['redundancy_ratio']:.3f} | "
            f"{row['lexical_cohesion']:.3f} | {row['citation_valid_rate']:.3f} | {row['avg_attempt_count']:.2f} | "
            f"{row['info_insufficient_rate']:.3f} | {row['avg_top1_score']:.3f} | {row['avg_total_tokens']:.1f} |"
        )

    lines.append("")
    lines.append("## 自动推荐")
    lines.append("")
    if quality_best:
        lines.append(
            f"- 质量优先：chunk_size={quality_best['chunk_size']} overlap={quality_best['overlap']} "
            f"（citation_valid_rate={quality_best['citation_valid_rate']:.3f}, avg_top1_score={quality_best['avg_top1_score']:.3f}）"
        )
    if cost_best:
        lines.append(
            f"- 成本优先：chunk_size={cost_best['chunk_size']} overlap={cost_best['overlap']} "
            f"（avg_total_tokens={cost_best['avg_total_tokens']:.1f}, redundancy={cost_best['redundancy_ratio']:.3f}）"
        )
    lines.append("")
    lines.append("## 指标解释")
    lines.append("")
    lines.append("- citation_valid_rate：带引用答案通过格式与原文校验的比例。")
    lines.append("- avg_attempt_count：模型平均需要几轮才能输出合规答案。越低越稳。")
    lines.append("- info_insufficient_rate：回答中出现“当前信息不足”的比例。越低通常说明切分更有利于回答。")
    lines.append("- avg_top1_score：向量召回第一名 chunk 的平均相似度。")
    lines.append("- avg_total_tokens：问答总 token 平均值，可近似反映成本。")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Day12 主流程。

    端到端步骤：
    1. 读取参数、语料和查询。
    2. 对每组 chunk_size / overlap 建索引。
    3. 用 Day11 的问答+引用校验链路评估所有 query。
    4. 聚合每组参数的统计指标。
    5. 排序并给出推荐，输出 Markdown / CSV / JSONL。
    """
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    chunk_sizes = parse_int_list(args.chunk_sizes)
    overlaps = parse_int_list(args.overlaps)
    if args.top_k <= 0:
        raise ValueError("top-k must be > 0")
    if args.max_attempts <= 0:
        raise ValueError("max-attempts must be > 0")

    corpus_file = resolve_project_path(args.corpus_file)
    queries_file = resolve_project_path(args.queries_file)
    system_prompt_file = resolve_project_path(args.system_prompt)
    report_file = resolve_project_path(args.report)
    csv_file = resolve_project_path(args.csv)
    jsonl_file = resolve_project_path(args.jsonl)

    corpus_text = load_text(corpus_file)
    queries = read_queries(queries_file)

    embedding_model = args.embedding_model.strip() or (os.getenv("OPENAI_EMBEDDING_MODEL") or "").strip() or "nomic-embed-text"
    qa_model = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
    args.embedding_model = embedding_model
    args.model = qa_model

    emb_api_key, emb_base_url, emb_client = build_embedding_client()
    system_prompt = load_system_prompt(system_prompt_file)
    qa_api_key, qa_base_url, qa_client = build_client()

    rows: list[dict] = []
    # 先把整份语料分词一次，后面不同参数组合重复复用，减少重复工作。
    base_tokens = tokenize_words(corpus_text)
    for chunk_size in chunk_sizes:
        for overlap in overlaps:
            if overlap >= chunk_size:
                continue

            # 当前参数组合对应的一组 chunk。
            token_chunks = build_chunks(base_tokens, chunk_size, overlap)
            chunks = ["".join(chunk) for chunk in token_chunks]
            if not chunks:
                continue

            # 为当前参数组合建立独立向量索引，确保评估结果只反映当前切分策略。
            chunk_vectors = [embedding_vector(emb_client, emb_api_key, emb_base_url, embedding_model, chunk) for chunk in chunks]
            matrix = np.asarray(chunk_vectors, dtype=np.float32)
            matrix = l2_normalize(matrix)
            index = faiss.IndexFlatIP(matrix.shape[1])
            index.add(matrix)

            # 下面这些统计量最终会汇总成该参数组合的一行评估结果。
            citation_valid_count = 0
            info_insufficient_count = 0
            attempt_total = 0
            top1_total = 0.0
            token_total = 0.0

            for query in queries:
                # 1) 先做向量召回，拿到当前 query 的 top-k 候选 chunk。
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
                # 2) 再复用 Day11 的“回答必须附原文引用”逻辑做回答与校验。
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

                # 字段说明：
                # - citation_valid: 当前这条 query 的回答是否通过“引用格式 + 原文连续子串”校验。
                # - citations: 模型输出中解析出的引用列表，每项通常包含 chunk_id 和 quote。
                # - attempt_count: 为得到当前结果一共尝试了几轮，越小越稳。
                # - validation_error: 如果最终仍不合规，这里记录最后一次失败原因。
                citation_valid_count += 1 if citation_valid else 0
                # info_insufficient_count 统计“当前信息不足”出现次数，
                # 后面会换算为 info_insufficient_rate。
                info_insufficient_count += 1 if "当前信息不足" in answer else 0
                attempt_total += attempt_count
                token_total += float(usage.get("total_tokens") or 0)
                top1_total += float(hits[0]["score"]) if hits else 0.0

                # 单条 query 明细输出字段说明：
                # - chunk_size / overlap: 当前评估的是哪组切分参数。
                # - hits: 当前 query 的召回结果。
                # - answer: 模型生成的最终答案。
                # - citations: 从 answer 中提取到的引用来源。
                # - citation_valid: 当前答案是否通过引用校验。
                # - validation_error: 若失败，记录失败原因；成功时通常为空字符串。
                # - attempt_count: 当前 query 的重试轮数。
                # - total_tokens: 当前 query 的总 token 消耗。
                append_jsonl(
                    jsonl_file,
                    {
                        "timestamp_utc": utc_now_iso(),
                        "chunk_size": chunk_size,
                        "overlap": overlap,
                        "query": query,
                        "hits": hits,
                        "answer": answer,
                        "citations": citations,
                        "citation_valid": citation_valid,
                        "validation_error": validation_error,
                        "attempt_count": attempt_count,
                        "total_tokens": usage.get("total_tokens"),
                    },
                )

            query_count = len(queries) or 1
            # 聚合后的汇总字段说明：
            # - citation_valid_rate = citation_valid_count / query_count
            #   表示当前参数组合下，多少比例的 query 最终输出了合规引用。
            # - avg_attempt_count = attempt_total / query_count
            #   表示平均每条 query 需要几轮才产出最终结果。
            # - info_insufficient_rate = info_insufficient_count / query_count
            #   表示有多少比例的 query 被模型判断为“当前信息不足”。
            # - avg_top1_score = top1_total / query_count
            #   表示第一名召回结果的平均相似度，越高通常说明切分更利于召回。
            # - avg_total_tokens = token_total / query_count
            #   表示平均 token 成本，越低通常越省钱。
            row = {
                "chunk_size": chunk_size,
                "overlap": overlap,
                "chunk_count": len(chunks),
                "redundancy_ratio": chunk_redundancy_ratio(token_chunks),
                "lexical_cohesion": lexical_cohesion(token_chunks),
                "citation_valid_rate": citation_valid_count / query_count,
                "avg_attempt_count": attempt_total / query_count,
                "info_insufficient_rate": info_insufficient_count / query_count,
                "avg_top1_score": top1_total / query_count,
                "avg_total_tokens": token_total / query_count,
            }
            rows.append(row)

    rows.sort(key=score_quality, reverse=True)
    quality_best, cost_best = choose_recommendations(rows, args.max_redundancy_for_cost)
    write_csv(csv_file, rows)
    write_report(report_file, args, rows, quality_best, cost_best)

    print("Done. Day12 chunking strategy tuning generated.")
    print(f"Report => {args.report}")
    print(f"CSV    => {args.csv}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()