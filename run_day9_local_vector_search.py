#!/usr/bin/env python3
"""Day 9：搭建本地向量检索（FAISS）。"""

import argparse
import json
import os
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

from run_day8_chunking_experiment import (
    build_chunks,
    build_embedding_client,
    embedding_vector,
    load_text,
    tokenize_words,
    utc_now_iso,
)

try:
    import faiss  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "faiss is not installed. Run: ./.venv/bin/pip install faiss-cpu"
    ) from exc

PROJECT_DIR = Path(__file__).resolve().parent


def resolve_project_path(path_str: str) -> Path:
    # 统一把相对路径转换到项目目录下，避免在不同 cwd 运行时找不到文件。
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    # 所有参数都给默认值，保证首次运行“零配置可跑”。
    parser = argparse.ArgumentParser(description="Run Day9 local vector retrieval with FAISS")
    parser.add_argument("--corpus-file", default="inputs/day8_corpus_backend_notes.txt", help="语料文件")
    parser.add_argument("--queries-file", default="inputs/day9_queries.txt", help="查询列表文件（每行一条）")
    parser.add_argument("--chunk-size", type=int, default=80, help="切分 chunk size")
    parser.add_argument("--overlap", type=int, default=20, help="切分 overlap")
    parser.add_argument("--top-k", type=int, default=3, help="每个查询返回 top-k")
    parser.add_argument("--embedding-model", default="", help="可选：覆盖 OPENAI_EMBEDDING_MODEL")
    parser.add_argument("--index-file", default="data/day9_faiss.index", help="FAISS 索引文件")
    parser.add_argument("--meta-file", default="data/day9_faiss_meta.json", help="索引元数据文件")
    parser.add_argument("--report", default="experiments/day9_vector_search.md", help="Markdown 报告路径")
    parser.add_argument("--jsonl", default="logs/day9_vector_search.jsonl", help="JSONL 结果日志")
    return parser.parse_args()


def append_jsonl(path: Path, record: dict) -> None:
    # JSONL 适合按查询逐条追加，后续做评测/分析时方便处理。
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    # 把每个向量做 L2 归一化后，内积（IndexFlatIP）可近似余弦相似度。
    # 这样检索分数更稳定，也更符合语义检索直觉。
    # 例子：
    # - 输入 matrix 形状: [8, 768]（8 个 chunk，每个 768 维）
    # - 输出 matrix 形状: [8, 768]（仅做缩放，不改形状）
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return matrix / norms


def read_queries(path: Path) -> list[str]:
    # 查询文件按“每行一条 query”读取，忽略空行。
    lines = [line.strip() for line in load_text(path).splitlines()]
    return [line for line in lines if line]


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    # Day8 已验证过的切分方式：先分词，再按滑窗切块。
    # 这里直接复用，保证 Day8 -> Day9 参数能无缝衔接。
    # 例子：chunk_size=80, overlap=20 时，步长 step=60。
    # 第 1 块覆盖 token[0:80]，第 2 块覆盖 token[60:140]。
    # 两块重叠 token[60:80]，这样可减少“跨块断裂”。
    tokens = tokenize_words(text)
    chunks = build_chunks(tokens, chunk_size, overlap)
    return ["".join(chunk) for chunk in chunks]


def write_report(path: Path, args: argparse.Namespace, chunks: list[str], rows: list[dict]) -> None:
    # 输出人类可读报告，方便快速查看“某个 query 命中了哪些 chunk”。
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 9 本地向量检索报告（FAISS）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 语料文件：{args.corpus_file}",
        f"- 查询文件：{args.queries_file}",
        f"- chunk_size / overlap：{args.chunk_size} / {args.overlap}",
        f"- chunk 数：{len(chunks)}",
        f"- top-k：{args.top_k}",
        f"- embedding 模型：{args.embedding_model}",
        "",
        "## 检索结果",
        "",
    ]

    for row in rows:
        lines.append(f"### Query: {row['query']}")
        lines.append("")
        for hit in row["hits"]:
            lines.append(
                f"- rank={hit['rank']}, score={hit['score']:.4f}, chunk_id={hit['chunk_id']}"
            )
            lines.append(f"  - text: {hit['text']}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    # 从 .env 读取 OPENAI_BASE_URL / OPENAI_EMBEDDING_MODEL 等配置。
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    if args.overlap >= args.chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    if args.top_k <= 0:
        raise ValueError("top-k must be > 0")

    corpus_file = resolve_project_path(args.corpus_file)
    queries_file = resolve_project_path(args.queries_file)
    index_file = resolve_project_path(args.index_file)
    meta_file = resolve_project_path(args.meta_file)
    report_file = resolve_project_path(args.report)
    jsonl_file = resolve_project_path(args.jsonl)

    # 1) 读入语料并按 chunk 参数切分。
    corpus_text = load_text(corpus_file)
    chunks = chunk_text(corpus_text, args.chunk_size, args.overlap)
    if not chunks:
        raise RuntimeError("no chunks generated")

    # 优先级：命令行参数 > 环境变量 > 兜底模型。
    embedding_model = args.embedding_model.strip() or (os.getenv("OPENAI_EMBEDDING_MODEL") or "").strip() or "nomic-embed-text"
    args.embedding_model = embedding_model

    # 2) 构建 embedding client，并把每个 chunk 转成向量。
    api_key, base_url, client = build_embedding_client()

    chunk_vectors: list[list[float]] = []
    for chunk in chunks:
        chunk_vectors.append(embedding_vector(client, api_key, base_url, embedding_model, chunk))

    # 3) 向量矩阵化 + 归一化。
    # matrix 形状约为 [chunk_count, embedding_dim]。
    # 例子：如果有 8 个 chunk，embedding 维度 768，则 matrix 为 [8, 768]。
    matrix = np.asarray(chunk_vectors, dtype=np.float32)
    matrix = l2_normalize(matrix)

    # 4) 用 FAISS 建立本地内积索引（归一化后等价于余弦检索）。
    # IndexFlatIP 是“精确检索”索引：不做近似，速度适合小中型语料演示。
    dimension = matrix.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(matrix)

    # 5) 落盘索引与元数据（便于复用，不必每次重建）。
    index_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_file))
    meta_file.write_text(
        json.dumps(
            {
                "created_at_utc": utc_now_iso(),
                "embedding_model": embedding_model,
                "chunk_size": args.chunk_size,
                "overlap": args.overlap,
                "chunks": chunks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 6) 对每条 query 执行向量检索，返回 top-k chunk。
    queries = read_queries(queries_file)
    rows: list[dict] = []
    for query in queries:
        # query -> embedding 向量 -> 归一化
        # q_matrix 形状固定是 [1, embedding_dim]，因为一次只检索一条 query。
        q_vec = embedding_vector(client, api_key, base_url, embedding_model, query)
        q_matrix = np.asarray([q_vec], dtype=np.float32)
        q_matrix = l2_normalize(q_matrix)

        # search 返回两个矩阵：scores 和 ids，维度都是 [1, top_k]
        # 例子（top_k=3）：
        # - scores[0] = [0.71, 0.66, 0.62]（越大越相关）
        # - ids[0]    = [5, 6, 2]（命中的 chunk 下标）
        scores, ids = index.search(q_matrix, args.top_k)
        hits: list[dict] = []
        for rank, (score, chunk_id) in enumerate(zip(scores[0], ids[0]), start=1):
            if chunk_id < 0 or chunk_id >= len(chunks):
                continue
            hits.append(
                {
                    "rank": rank,
                    "score": float(score),
                    "chunk_id": int(chunk_id),
                    "text": chunks[chunk_id][:200],
                }
            )

        # 每条 query 的结果做结构化保存，便于 Day10 直接复用做“检索后生成”。
        row = {
            "timestamp_utc": utc_now_iso(),
            "query": query,
            "hits": hits,
            "top_k": args.top_k,
            "embedding_model": embedding_model,
        }
        # row 的用途：
        # - experiments/day9_vector_search.md：给人读的可视化结果
        # - logs/day9_vector_search.jsonl：给脚本做后续评测/统计
        rows.append(row)
        append_jsonl(jsonl_file, row)

    # 7) 生成人类可读报告。
    write_report(report_file, args, chunks, rows)

    print("Done. Day9 local vector retrieval generated.")
    print(f"Index  => {args.index_file}")
    print(f"Meta   => {args.meta_file}")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()