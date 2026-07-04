#!/usr/bin/env python3
"""Day 8：Embedding 入门与文本切分实验。"""

import argparse
import csv
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day 8 chunking experiment")
    parser.add_argument("--input-file", default="inputs/day8_corpus_backend_notes.txt", help="实验语料文件")
    parser.add_argument("--chunk-sizes", default="80,120,160", help="按词切分的 chunk size 列表")
    parser.add_argument("--overlaps", default="10,20,40", help="按词切分的 overlap 列表")
    parser.add_argument("--report", default="experiments/day8_chunking_experiment.md", help="Markdown 报告输出")
    parser.add_argument("--csv", default="experiments/day8_chunking_experiment.csv", help="CSV 汇总输出")
    parser.add_argument("--jsonl", default="logs/day8_chunking_experiment.jsonl", help="JSONL 明细输出")
    parser.add_argument("--enable-embedding-probe", action="store_true", help="启用 embedding 探针计算相邻块余弦相似度")
    parser.add_argument("--embedding-model", default="", help="可选：覆盖 OPENAI_EMBEDDING_MODEL")
    parser.add_argument(
        "--max-redundancy-for-cost",
        type=float,
        default=0.30,
        help="成本优先推荐允许的最大冗余率（默认 0.30）",
    )
    return parser.parse_args()


def parse_int_list(value: str) -> list[int]:
    items: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        parsed = int(token)
        if parsed <= 0:
            raise ValueError("all numeric args must be > 0")
        items.append(parsed)
    if not items:
        raise ValueError("list argument cannot be empty")
    return items


def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_text(encoding="utf-8")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def tokenize_words(text: str) -> list[str]:
    # 中英文混合场景，简单按连续字母数字和中文字符组切分。
    return re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text)


def build_chunks(tokens: list[str], chunk_size: int, overlap: int) -> list[list[str]]:
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    chunks: list[list[str]] = []
    step = chunk_size - overlap
    start = 0
    while start < len(tokens):
        chunk = tokens[start : start + chunk_size]
        if not chunk:
            break
        chunks.append(chunk)
        if start + chunk_size >= len(tokens):
            break
        start += step
    return chunks


def chunk_lengths(chunks: list[list[str]]) -> list[int]:
    return [len(chunk) for chunk in chunks]


def chunk_redundancy_ratio(chunks: list[list[str]]) -> float:
    if len(chunks) <= 1:
        return 0.0
    repeated = 0
    compared = 0
    for i in range(len(chunks) - 1):
        left = set(chunks[i])
        right = set(chunks[i + 1])
        if not left and not right:
            continue
        union = left | right
        inter = left & right
        if not union:
            continue
        repeated += len(inter)
        compared += len(union)
    return repeated / compared if compared else 0.0


def lexical_cohesion(chunks: list[list[str]]) -> float:
    if len(chunks) <= 1:
        return 1.0
    sims: list[float] = []
    for i in range(len(chunks) - 1):
        left = Counter(chunks[i])
        right = Counter(chunks[i + 1])
        sim = cosine_from_counter(left, right)
        sims.append(sim)
    return sum(sims) / len(sims) if sims else 0.0


def cosine_from_counter(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a.keys()) & set(b.keys())
    dot = sum(a[key] * b[key] for key in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_embedding_client() -> tuple[str, str, httpx.Client]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = (os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1").rstrip("/")
    is_local_compatible = base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost")
    if not api_key and is_local_compatible:
        api_key = "ollama"
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")
    proxy = os.getenv("OPENAI_HTTP_PROXY", "").strip()
    client = httpx.Client(proxy=proxy or None, timeout=60.0)
    return api_key, base_url, client


def embedding_vector(client: httpx.Client, api_key: str, base_url: str, model: str, text: str) -> list[float]:
    url = f"{base_url}/embeddings"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "input": text}
    resp = client.post(url, headers=headers, json=payload)
    # 某些 Ollama 版本不支持 OpenAI 兼容的 /v1/embeddings。
    # 回退顺序：/api/embeddings -> /api/embed
    if resp.status_code == 404 and (
        base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost")
    ):
        root_url = base_url[:-3] if base_url.endswith("/v1") else base_url

        # 1) Ollama 旧/常见路由：/api/embeddings
        native_url_1 = root_url + "/api/embeddings"
        native_payload_1 = {"model": model, "prompt": text}
        native_resp_1 = client.post(native_url_1, headers={"Content-Type": "application/json"}, json=native_payload_1)
        if native_resp_1.status_code < 400:
            native_data_1 = native_resp_1.json()
            native_embedding_1 = native_data_1.get("embedding")
            if isinstance(native_embedding_1, list):
                return [float(x) for x in native_embedding_1]

        # 2) Ollama 新路由：/api/embed
        native_url_2 = root_url + "/api/embed"
        native_payload_2 = {"model": model, "input": text}
        native_resp_2 = client.post(native_url_2, headers={"Content-Type": "application/json"}, json=native_payload_2)
        if native_resp_2.status_code < 400:
            native_data_2 = native_resp_2.json()
            embeddings = native_data_2.get("embeddings")
            if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
                return [float(x) for x in embeddings[0]]

        raise RuntimeError(
            "embedding endpoint fallback failed: "
            f"/api/embeddings => HTTP {native_resp_1.status_code}, "
            f"/api/embed => HTTP {native_resp_2.status_code}"
        )

    if resp.status_code >= 400:
        raise RuntimeError(f"embedding HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    vectors = data.get("data") or []
    if not vectors:
        raise RuntimeError("embedding response missing data")
    embedding = vectors[0].get("embedding")
    if not isinstance(embedding, list):
        raise RuntimeError("embedding format invalid")
    return [float(x) for x in embedding]


def cosine_dense(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def embedding_probe(chunks: list[list[str]], model: str) -> float:
    if len(chunks) <= 1:
        return 1.0
    api_key, base_url, client = build_embedding_client()
    texts = ["".join(chunk) for chunk in chunks]
    vectors: list[list[float]] = []
    for text in texts:
        vectors.append(embedding_vector(client, api_key, base_url, model, text))
    sims: list[float] = []
    for i in range(len(vectors) - 1):
        sims.append(cosine_dense(vectors[i], vectors[i + 1]))
    return sum(sims) / len(sims) if sims else 0.0


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "chunk_size",
        "overlap",
        "chunk_count",
        "avg_chunk_len",
        "min_chunk_len",
        "max_chunk_len",
        "redundancy_ratio",
        "lexical_cohesion",
        "embedding_adjacent_cosine",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def write_report(path: Path, args: argparse.Namespace, rows: list[dict], text_len: int, token_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 8 文本切分实验报告（自动生成）",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 输入文件：{args.input_file}",
        f"- 字符数：{text_len}",
        f"- 词元数（近似）：{token_count}",
        f"- chunk_size 组合：{args.chunk_sizes}",
        f"- overlap 组合：{args.overlaps}",
        f"- embedding 探针：{'开启' if args.enable_embedding_probe else '关闭'}",
        f"- 成本优先冗余阈值：{args.max_redundancy_for_cost:.2f}",
        "",
        "## 汇总表",
        "",
        "| chunk_size | overlap | chunks | avg_len | redundancy | lexical_cohesion | embedding_adjacent_cosine |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for row in rows:
        emb = row["embedding_adjacent_cosine"]
        emb_text = f"{emb:.3f}" if emb is not None else "N/A"
        lines.append(
            f"| {row['chunk_size']} | {row['overlap']} | {row['chunk_count']} | {row['avg_chunk_len']:.1f} | "
            f"{row['redundancy_ratio']:.3f} | {row['lexical_cohesion']:.3f} | {emb_text} |"
        )

    # 自动推荐规则：
    # 1) 召回优先：优先高语义连续（embedding_adjacent_cosine），其次 lexical_cohesion。
    # 2) 成本优先：先过滤 redundancy_ratio，再在候选中选连续性最好。
    def continuity_score(item: dict) -> float:
        emb = item.get("embedding_adjacent_cosine")
        if isinstance(emb, (int, float)):
            return float(emb)
        return float(item.get("lexical_cohesion", 0.0))

    recall_ranked = sorted(
        rows,
        key=lambda r: (
            continuity_score(r),
            r["lexical_cohesion"],
            -r["redundancy_ratio"],
        ),
        reverse=True,
    )
    recall_best = recall_ranked[0] if recall_ranked else None

    cost_candidates = [row for row in rows if row["redundancy_ratio"] <= args.max_redundancy_for_cost]
    if cost_candidates:
        cost_ranked = sorted(
            cost_candidates,
            key=lambda r: (
                continuity_score(r),
                r["lexical_cohesion"],
                -r["chunk_count"],
            ),
            reverse=True,
        )
        cost_best = cost_ranked[0]
    else:
        cost_ranked = sorted(rows, key=lambda r: r["redundancy_ratio"])
        cost_best = cost_ranked[0] if cost_ranked else None

    lines.append("")
    lines.append("## 自动推荐参数")
    lines.append("")
    if recall_best:
        lines.append(
            f"- 召回优先：chunk_size={recall_best['chunk_size']} overlap={recall_best['overlap']} "
            f"（continuity={continuity_score(recall_best):.3f}, redundancy={recall_best['redundancy_ratio']:.3f}）"
        )
    if cost_best:
        lines.append(
            f"- 成本优先：chunk_size={cost_best['chunk_size']} overlap={cost_best['overlap']} "
            f"（continuity={continuity_score(cost_best):.3f}, redundancy={cost_best['redundancy_ratio']:.3f}）"
        )
    if recall_best and cost_best:
        lines.append("")
        lines.append(
            "建议：先用“成本优先”做默认线上参数，再用“召回优先”做 A/B 对照，"
            "通过 Day9 的 Recall@K 和延迟结果最终定版。"
        )
    if not recall_best and not cost_best:
        lines.append("无有效结果。")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    input_file = resolve_project_path(args.input_file)
    report_file = resolve_project_path(args.report)
    csv_file = resolve_project_path(args.csv)
    jsonl_file = resolve_project_path(args.jsonl)

    text = load_text(input_file)
    tokens = tokenize_words(text)
    chunk_sizes = parse_int_list(args.chunk_sizes)
    overlaps = parse_int_list(args.overlaps)

    embedding_model = args.embedding_model.strip() or (os.getenv("OPENAI_EMBEDDING_MODEL") or "").strip() or "text-embedding-3-small"

    rows: list[dict] = []
    for chunk_size in chunk_sizes:
        for overlap in overlaps:
            if overlap >= chunk_size:
                continue
            chunks = build_chunks(tokens, chunk_size, overlap)
            if not chunks:
                continue
            lengths = chunk_lengths(chunks)
            row = {
                "timestamp_utc": utc_now_iso(),
                "chunk_size": chunk_size,
                "overlap": overlap,
                "chunk_count": len(chunks),
                "avg_chunk_len": sum(lengths) / len(lengths),
                "min_chunk_len": min(lengths),
                "max_chunk_len": max(lengths),
                "redundancy_ratio": chunk_redundancy_ratio(chunks),
                "lexical_cohesion": lexical_cohesion(chunks),
                "embedding_adjacent_cosine": None,
            }

            if args.enable_embedding_probe:
                try:
                    row["embedding_adjacent_cosine"] = embedding_probe(chunks, embedding_model)
                except Exception as exc:
                    row["embedding_adjacent_cosine"] = None
                    row["embedding_probe_error"] = str(exc)

            rows.append(row)
            append_jsonl(jsonl_file, row)

    write_csv(csv_file, rows)
    write_report(report_file, args, rows, len(text), len(tokens))

    print("Done. Day8 chunking experiment generated.")
    print(f"Report => {args.report}")
    print(f"CSV    => {args.csv}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()