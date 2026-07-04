#!/usr/bin/env python3
"""Day 10：知识库问答 V1（仅召回，不重排）。

功能概览：
1. 读取语料并切分成 chunk。
2. 对 chunk 做向量化并构建 FAISS 索引。
3. 对每个 query 做 top-k 向量召回（仅召回，不做重排）。
4. 将召回片段拼接到提示词，调用 LLM 生成答案。
5. 输出 Markdown 报告和 JSONL 明细日志。

运行示例：
        ./.venv/bin/python run_day10_kb_qa_v1.py \
            --embedding-model nomic-embed-text \
            --model qwen2.5:0.5b
"""

import argparse
import json
import os
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

from chat_cli import build_client, chat_once, load_system_prompt, normalize_usage
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

# ============================================================
# 教学导读（先看这里，再看下面函数）
# ------------------------------------------------------------
# 你可以把本脚本理解成一个 6 步流水线：
#
# Step 1) 读配置
#   - 从 .env + CLI 拿到模型、路径、检索参数。
#
# Step 2) 切语料
#   - 把知识库长文本切成多个 chunk，作为检索最小单元。
#
# Step 3) 建索引
#   - 为每个 chunk 生成向量，L2 归一化后放进 FAISS IndexFlatIP。
#
# Step 4) 做召回
#   - 每个 query 向量化后，到 FAISS 搜 top-k 相关 chunk。
#
# Step 5) 生成答案
#   - 将 top-k chunk 拼成上下文，调用 chat 模型生成回答。
#
# Step 6) 落盘结果
#   - JSONL 保存结构化明细，Markdown 保存可读报告。
#
# 重要约束（Day10 V1）：
# - 只做“召回 -> 生成”，不做 rerank（重排）。
# ============================================================


def resolve_project_path(path_str: str) -> Path:
    """将路径解析为项目内绝对路径。

    设计目的：
    - 允许脚本在不同 cwd 下运行，避免相对路径失效。

    参数：
    - path_str: 用户传入的路径（相对或绝对）。

    返回：
    - Path: 绝对路径对象。

    示例：
    - 输入 `inputs/day9_queries.txt` -> `<PROJECT_DIR>/inputs/day9_queries.txt`
    - 输入 `/tmp/a.txt` -> `/tmp/a.txt`（保持不变）
    """
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    """定义并解析命令行参数。

    返回：
    - argparse.Namespace: 包含所有 CLI 参数。

    关键参数说明：
    - --chunk-size/--overlap: 控制切分粒度与上下文重叠。
    - --top-k: 每个问题召回的候选片段数量。
    - --embedding-model/--model: 分别用于召回与问答。

    示例：
    - python run_day10_kb_qa_v1.py --top-k 5 --temperature 0.1
    """
    parser = argparse.ArgumentParser(description="Run Day10 KB QA V1 (retrieve only, no rerank)")
    parser.add_argument("--corpus-file", default="inputs/day8_corpus_backend_notes.txt", help="语料文件")
    parser.add_argument("--queries-file", default="inputs/day9_queries.txt", help="查询文件（每行一条）")
    parser.add_argument("--system-prompt", default="prompts/day10_kb_qa_v1_cn.txt", help="系统提示词")
    parser.add_argument("--chunk-size", type=int, default=80, help="切分 chunk size")
    parser.add_argument("--overlap", type=int, default=20, help="切分 overlap")
    parser.add_argument("--top-k", type=int, default=3, help="每个 query 召回的 chunk 数")
    parser.add_argument("--embedding-model", default="", help="可选：覆盖 OPENAI_EMBEDDING_MODEL")
    parser.add_argument("--model", default="", help="问答模型（覆盖 OPENAI_MODEL）")
    parser.add_argument("--temperature", type=float, default=0.2, help="问答温度")
    parser.add_argument("--max-tokens", type=int, default=700, help="问答输出上限")
    parser.add_argument("--report", default="experiments/day10_kb_qa_v1.md", help="Markdown 报告")
    parser.add_argument("--jsonl", default="logs/day10_kb_qa_v1.jsonl", help="JSONL 明细")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict) -> None:
    """向 JSONL 文件追加一条记录。

    说明：
    - JSONL（每行一个 JSON 对象）便于流式写入和后续批处理分析。
    - 函数会自动创建父目录。

    示例输出行：
    {"query":"...","answer":"...","total_tokens":123}
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """将语料文本切分为可用于检索的 chunk 列表。

    实现方式：
    - 先调用 `tokenize_words` 分词。
    - 再调用 `build_chunks` 按滑窗切分。
    - 最后把 token 列表拼接回字符串 chunk。

    参数：
    - text: 原始语料。
    - chunk_size: 每个 chunk 的 token 数上限。
    - overlap: 相邻 chunk 之间重叠 token 数。

    返回：
    - list[str]: 切分后的 chunk 文本列表。

    示例：
    - chunk_size=80, overlap=20 -> 步长 step=60。
    """
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
    """对单个 query 执行向量召回，返回 top-k 命中结果。

    流程：
    1. query -> embedding 向量。
    2. 转成 `np.float32` 矩阵并做 L2 归一化。
    3. 使用 `IndexFlatIP.search` 检索 top-k。
    4. 组装 rank/score/chunk_id/text 结果。

    返回字段说明：
    - rank: 召回名次（从 1 开始）。
    - score: 相似度分数（归一化后可视作余弦相似度近似）。
    - chunk_id: 命中的 chunk 索引。
    - text: 命中的 chunk 文本。

    示例返回：
    [
        {"rank": 1, "score": 0.71, "chunk_id": 5, "text": "..."},
        {"rank": 2, "score": 0.66, "chunk_id": 2, "text": "..."}
    ]
    """
    q_vec = embedding_vector(embedding_client, api_key, base_url, embedding_model, query)
    # FAISS 期望二维矩阵输入：[batch, dim]，单条 query 时 batch=1。
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


def build_user_text(query: str, hits: list[dict]) -> str:
    """构造问答阶段的 user prompt 文本。

    目的：
    - 将 query 与召回片段拼接为一个完整输入。
    - 强化“仅基于召回片段回答”的约束。

    参数：
    - query: 用户问题。
    - hits: 召回结果列表（包含 chunk_id 与 text）。

    返回：
    - str: 发送给 LLM 的 user_text。

    示例结构：
    用户问题：如何排查超时？

    以下是召回片段（按相似度降序）：
    [chunk-2] ...
    [chunk-5] ...
    """
    blocks = []
    for hit in hits:
        blocks.append(f"[chunk-{hit['chunk_id']}] {hit['text']}")
    context = "\n\n".join(blocks)
    return (
        f"用户问题：{query}\n\n"
        "以下是召回片段（按相似度降序）：\n"
        f"{context}\n\n"
        "请仅基于这些片段回答。"
    )


def write_report(path: Path, args: argparse.Namespace, rows: list[dict]) -> None:
    """写入可读的 Markdown 实验报告。

    报告内容：
    - 运行参数（语料、查询、chunk 配置、模型配置）。
    - 每个 query 的召回结果与最终答案。

    参数：
    - path: 报告输出路径。
    - args: 命令行参数对象，用于落盘实验配置。
    - rows: 每条 query 的完整结果记录。

    示例输出文件：
    - experiments/day10_kb_qa_v1.md
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 10 知识库问答 V1 报告（仅召回，不重排）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 语料文件：{args.corpus_file}",
        f"- 查询文件：{args.queries_file}",
        f"- chunk_size / overlap：{args.chunk_size} / {args.overlap}",
        f"- top-k：{args.top_k}",
        f"- embedding 模型：{args.embedding_model}",
        f"- QA 模型：{args.model}",
        "",
    ]

    for row in rows:
        lines.append(f"## Query: {row['query']}")
        lines.append("")
        lines.append("### Retrieved Chunks")
        lines.append("")
        for hit in row["hits"]:
            lines.append(
                f"- rank={hit['rank']}, score={hit['score']:.4f}, chunk_id={hit['chunk_id']}"
            )
            lines.append(f"  - text: {hit['text'][:220]}")
        lines.append("")
        lines.append("### Answer")
        lines.append("")
        lines.append(row["answer"])
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """脚本主流程：完成 Day10 的检索增强问答（召回版）。

    端到端步骤：
    1. 读取环境变量和命令行参数。
    2. 加载语料并切分 chunk。
    3. 建立向量索引（FAISS）。
    4. 对每个 query 执行：召回 -> 组 prompt -> 生成答案。
    5. 落盘 JSONL 与 Markdown。

    注意：
    - V1 明确不做 rerank。
    - 该函数只编排流程，核心逻辑拆分在上方辅助函数中。
    """
    # ========================================================
    # 常见问题排查（新手友好）
    # --------------------------------------------------------
    # 1) 报错：faiss is not installed
    #    解决：./.venv/bin/pip install faiss-cpu
    #
    # 2) 报错：OPENAI_API_KEY is missing
    #    解决：检查 .env 是否设置 OPENAI_API_KEY。
    #    说明：如果你用本地 Ollama 且走兼容接口，通常可用占位 key。
    #
    # 3) 报错：overlap must be smaller than chunk_size
    #    解决：保证 overlap < chunk_size（例如 20 < 80）。
    #
    # 4) 报错：no chunks generated
    #    解决：检查语料文件路径、内容是否为空、编码是否正常。
    #
    # 5) 输出“当前信息不足”较多
    #    排查方向：提高 top-k、调 chunk_size/overlap、补充语料覆盖范围。
    # ========================================================

    # 允许从项目根目录 `.env` 读取 OPENAI_* 配置，减少命令行参数负担。
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    if args.overlap >= args.chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    if args.top_k <= 0:
        raise ValueError("top-k must be > 0")

    corpus_file = resolve_project_path(args.corpus_file)
    queries_file = resolve_project_path(args.queries_file)
    system_prompt_file = resolve_project_path(args.system_prompt)
    report_file = resolve_project_path(args.report)
    jsonl_file = resolve_project_path(args.jsonl)

    # 读取并切分知识库语料，得到可检索 chunk 集合。
    corpus_text = load_text(corpus_file)
    chunks = chunk_text(corpus_text, args.chunk_size, args.overlap)
    if not chunks:
        raise RuntimeError("no chunks generated")

    # 模型优先级：CLI 参数 > 环境变量 > 默认值。
    embedding_model = args.embedding_model.strip() or (os.getenv("OPENAI_EMBEDDING_MODEL") or "").strip() or "nomic-embed-text"
    qa_model = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
    args.embedding_model = embedding_model
    args.model = qa_model

    # 为所有 chunk 生成向量；后续将一次性构建索引。
    emb_api_key, emb_base_url, emb_client = build_embedding_client()
    chunk_vectors = [embedding_vector(emb_client, emb_api_key, emb_base_url, embedding_model, chunk) for chunk in chunks]

    # 归一化后使用内积索引，可近似余弦相似度检索。
    matrix = np.asarray(chunk_vectors, dtype=np.float32)
    matrix = l2_normalize(matrix)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)

    # 构建问答客户端，并加载系统提示词模板。
    system_prompt = load_system_prompt(system_prompt_file)
    qa_api_key, qa_base_url, qa_client = build_client()

    queries = read_queries(queries_file)
    rows: list[dict] = []
    for query in queries:
        # V1 只用向量召回结果，不做任何二次重排。
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

        # 将召回片段注入上下文后发给大模型生成答案。
        user_text = build_user_text(query, hits)
        answer, usage = chat_once(
            client=qa_client,
            api_key=qa_api_key,
            base_url=qa_base_url,
            model=qa_model,
            system_prompt=system_prompt,
            user_text=user_text,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )

        # 单条 query 的结构化结果，既用于报告也用于离线分析。
        row = {
            "timestamp_utc": utc_now_iso(),
            "query": query,
            "hits": hits,
            "answer": answer,
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

    print("Done. Day10 KB QA V1 generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()