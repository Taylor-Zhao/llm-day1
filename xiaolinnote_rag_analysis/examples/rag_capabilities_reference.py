"""Xiaolinnote RAG 专题的高级能力离线参考实现。

该模块不替换 Day8-Day21 学习脚本，而是集中演示它们尚未覆盖的能力：
语义/父子/句子窗口切块、BM25、RRF、质量门控、评估、增量索引、
图遍历，以及可注入的 CRAG 与 Agentic RAG 控制流。
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import math
import re
from typing import Callable, Iterable, Mapping, Sequence


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_+.-]+|[\u4e00-\u9fff]")
SENTENCE_PATTERN = re.compile(r"[^。！？!?\n]+[。！？!?]?", re.MULTILINE)


def tokenize(text: str) -> list[str]:
    """提供一个无第三方依赖的中英文检索分词基线。"""
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


def split_sentences(text: str) -> list[str]:
    """按中英文句末符和换行切句，保留句末标点。"""
    return [item.strip() for item in SENTENCE_PATTERN.findall(text) if item.strip()]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    document_id: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ParentChildIndex:
    parents: Mapping[str, Chunk]
    children: tuple[Chunk, ...]

    def expand(self, child_ids: Iterable[str]) -> list[Chunk]:
        """将精细命中的子块还原为去重后的父块。"""
        result: list[Chunk] = []
        seen: set[str] = set()
        children_by_id = {chunk.chunk_id: chunk for chunk in self.children}
        for child_id in child_ids:
            child = children_by_id[child_id]
            parent_id = str(child.metadata["parent_id"])
            if parent_id not in seen:
                result.append(self.parents[parent_id])
                seen.add(parent_id)
        return result


def semantic_chunks(document_id: str, text: str, max_tokens: int) -> list[Chunk]:
    """以完整句子为最小单元装箱，避免固定窗口从句中截断。"""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")

    chunks: list[Chunk] = []
    current: list[str] = []
    current_size = 0
    for sentence in split_sentences(text):
        sentence_size = max(1, len(tokenize(sentence)))
        if current and current_size + sentence_size > max_tokens:
            chunk_id = f"{document_id}:semantic:{len(chunks)}"
            chunks.append(Chunk(chunk_id, "".join(current), document_id))
            current = []
            current_size = 0
        current.append(sentence)
        current_size += sentence_size
    if current:
        chunk_id = f"{document_id}:semantic:{len(chunks)}"
        chunks.append(Chunk(chunk_id, "".join(current), document_id))
    return chunks


def build_parent_child_index(
    document_id: str,
    text: str,
    parent_tokens: int = 120,
    child_tokens: int = 40,
) -> ParentChildIndex:
    """父块保存完整上下文，子块只承担精准检索。"""
    if child_tokens > parent_tokens:
        raise ValueError("child_tokens cannot exceed parent_tokens")

    parents: dict[str, Chunk] = {}
    children: list[Chunk] = []
    for parent_number, parent in enumerate(semantic_chunks(document_id, text, parent_tokens)):
        parent_id = f"{document_id}:parent:{parent_number}"
        stored_parent = Chunk(parent_id, parent.text, document_id)
        parents[parent_id] = stored_parent
        for child_number, child in enumerate(
            semantic_chunks(f"{document_id}:p{parent_number}", parent.text, child_tokens)
        ):
            child_id = f"{parent_id}:child:{child_number}"
            children.append(
                Chunk(child_id, child.text, document_id, {"parent_id": parent_id})
            )
    return ParentChildIndex(parents, tuple(children))


def build_sentence_windows(document_id: str, text: str, window_size: int = 1) -> list[Chunk]:
    """每个句子用于建索引，metadata 中保存生成时使用的上下文窗口。"""
    if window_size < 0:
        raise ValueError("window_size cannot be negative")
    sentences = split_sentences(text)
    result: list[Chunk] = []
    for index, sentence in enumerate(sentences):
        start = max(0, index - window_size)
        end = min(len(sentences), index + window_size + 1)
        result.append(
            Chunk(
                f"{document_id}:sentence:{index}",
                sentence,
                document_id,
                {"window_text": "".join(sentences[start:end])},
            )
        )
    return result


class BM25Index:
    """适合教学和小语料离线测试的 BM25 实现。"""

    def __init__(
        self,
        chunks: Sequence[Chunk],
        k1: float = 1.5,
        b: float = 0.75,
        tokenizer: Callable[[str], list[str]] = tokenize,
    ) -> None:
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b
        self.tokenizer = tokenizer
        self.term_frequencies = [Counter(tokenizer(chunk.text)) for chunk in chunks]
        self.document_lengths = [sum(counts.values()) for counts in self.term_frequencies]
        self.average_length = (
            sum(self.document_lengths) / len(self.document_lengths) if chunks else 0.0
        )
        self.document_frequencies: Counter[str] = Counter()
        for counts in self.term_frequencies:
            self.document_frequencies.update(counts.keys())

    def search(self, query: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
        if top_k <= 0 or not self.chunks:
            return []
        query_terms = self.tokenizer(query)
        scored: list[tuple[Chunk, float]] = []
        for chunk, counts, document_length in zip(
            self.chunks, self.term_frequencies, self.document_lengths
        ):
            score = 0.0
            for term in query_terms:
                frequency = counts[term]
                if frequency == 0:
                    continue
                document_frequency = self.document_frequencies[term]
                inverse_document_frequency = math.log(
                    1 + (len(self.chunks) - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                normalization = 1 - self.b + self.b * document_length / self.average_length
                score += inverse_document_frequency * (
                    frequency * (self.k1 + 1)
                    / (frequency + self.k1 * normalization)
                )
            if score > 0:
                scored.append((chunk, score))
        return sorted(scored, key=lambda item: (-item[1], item[0].chunk_id))[:top_k]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], k: int = 60
) -> list[tuple[str, float]]:
    """仅融合排名，避免直接混加不可比的向量分和 BM25 分。"""
    if k < 0:
        raise ValueError("k cannot be negative")
    scores: defaultdict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class RetrievalDecision(str, Enum):
    LOCAL = "local"
    MIXED = "mixed"
    FALLBACK = "fallback"


def retrieval_gate(
    scores: Sequence[float], low_threshold: float, high_threshold: float
) -> RetrievalDecision:
    """用最佳重排分实现 CRAG 的相关、模糊、不相关三级路由。"""
    if low_threshold > high_threshold:
        raise ValueError("low_threshold cannot exceed high_threshold")
    best_score = max(scores, default=0.0)
    if best_score >= high_threshold:
        return RetrievalDecision.LOCAL
    if best_score >= low_threshold:
        return RetrievalDecision.MIXED
    return RetrievalDecision.FALLBACK


@dataclass(frozen=True)
class Claim:
    text: str
    source_ids: tuple[str, ...]


def validate_claim_sources(
    claims: Sequence[Claim], chunks: Mapping[str, Chunk]
) -> list[str]:
    """检查来源存在且声明词项能在引用原文中找到，返回错误列表。"""
    errors: list[str] = []
    for claim in claims:
        missing = [source_id for source_id in claim.source_ids if source_id not in chunks]
        if missing:
            errors.append(f"missing sources for {claim.text}: {', '.join(missing)}")
            continue
        evidence_tokens = set(
            tokenize(" ".join(chunks[source_id].text for source_id in claim.source_ids))
        )
        claim_tokens = set(tokenize(claim.text))
        coverage = (
            len(claim_tokens.intersection(evidence_tokens)) / len(claim_tokens)
            if claim_tokens
            else 0.0
        )
        if coverage < 0.5:
            errors.append(f"unsupported claim: {claim.text}")
    return errors


def hit_rate_at_k(rankings: Sequence[Sequence[str]], relevant: Sequence[set[str]], k: int) -> float:
    if len(rankings) != len(relevant):
        raise ValueError("rankings and relevant must have equal length")
    if not rankings:
        return 0.0
    hits = sum(bool(set(ranking[:k]).intersection(gold)) for ranking, gold in zip(rankings, relevant))
    return hits / len(rankings)


def mean_reciprocal_rank(rankings: Sequence[Sequence[str]], relevant: Sequence[set[str]]) -> float:
    if len(rankings) != len(relevant):
        raise ValueError("rankings and relevant must have equal length")
    if not rankings:
        return 0.0
    reciprocal_ranks: list[float] = []
    for ranking, gold in zip(rankings, relevant):
        reciprocal_ranks.append(
            next((1.0 / rank for rank, item in enumerate(ranking, start=1) if item in gold), 0.0)
        )
    return sum(reciprocal_ranks) / len(reciprocal_ranks)


class IncrementalIndexManifest:
    """跟踪文档内容 hash、chunk 归属和蓝绿版本切换。"""

    def __init__(self) -> None:
        self._versions: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = defaultdict(dict)
        self.active_version: str | None = None

    @staticmethod
    def content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def needs_update(self, version: str, document_id: str, content: str) -> bool:
        current = self._versions[version].get(document_id)
        return current is None or current[0] != self.content_hash(content)

    def record(self, version: str, document_id: str, content: str, chunk_ids: Sequence[str]) -> None:
        self._versions[version][document_id] = (self.content_hash(content), tuple(chunk_ids))

    def delete(self, version: str, document_id: str) -> tuple[str, ...]:
        previous = self._versions[version].pop(document_id, None)
        return previous[1] if previous else ()

    def activate(self, version: str) -> None:
        if version not in self._versions:
            raise KeyError(f"unknown version: {version}")
        self.active_version = version


@dataclass(frozen=True)
class Edge:
    source: str
    relation: str
    target: str


class KnowledgeGraph:
    """演示入口实体定位后的有界多跳关系遍历。"""

    def __init__(self, edges: Iterable[Edge]) -> None:
        self.adjacency: defaultdict[str, list[Edge]] = defaultdict(list)
        for edge in edges:
            self.adjacency[edge.source].append(edge)

    def traverse(
        self,
        start: str,
        max_hops: int,
        relations: set[str] | None = None,
    ) -> list[Edge]:
        if max_hops < 0:
            raise ValueError("max_hops cannot be negative")
        queue = deque([(start, 0)])
        visited_nodes = {start}
        result: list[Edge] = []
        while queue:
            node, hops = queue.popleft()
            if hops >= max_hops:
                continue
            for edge in self.adjacency[node]:
                if relations is not None and edge.relation not in relations:
                    continue
                result.append(edge)
                if edge.target not in visited_nodes:
                    visited_nodes.add(edge.target)
                    queue.append((edge.target, hops + 1))
        return result


class CorrectiveRAG:
    """通过依赖注入演示 CRAG，不绑定真实向量库或搜索 API。"""

    def __init__(
        self,
        local_retriever: Callable[[str], Sequence[tuple[Chunk, float]]],
        fallback_retriever: Callable[[str], Sequence[tuple[Chunk, float]]],
        low_threshold: float = 0.3,
        high_threshold: float = 0.6,
    ) -> None:
        self.local_retriever = local_retriever
        self.fallback_retriever = fallback_retriever
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold

    def retrieve(self, query: str) -> tuple[RetrievalDecision, list[Chunk]]:
        local = list(self.local_retriever(query))
        decision = retrieval_gate(
            [score for _, score in local], self.low_threshold, self.high_threshold
        )
        if decision == RetrievalDecision.LOCAL:
            return decision, [chunk for chunk, _ in local]
        fallback = list(self.fallback_retriever(query))
        if decision == RetrievalDecision.FALLBACK:
            return decision, [chunk for chunk, _ in fallback]
        merged_ids = [chunk.chunk_id for chunk, _ in local]
        merged_ids.extend(chunk.chunk_id for chunk, _ in fallback)
        chunks = {chunk.chunk_id: chunk for chunk, _ in [*local, *fallback]}
        fused = reciprocal_rank_fusion(
            [[chunk.chunk_id for chunk, _ in local], [chunk.chunk_id for chunk, _ in fallback]]
        )
        return decision, [chunks[chunk_id] for chunk_id, _ in fused if chunk_id in merged_ids]


@dataclass(frozen=True)
class AgenticRAGResult:
    queries: tuple[str, ...]
    chunks: tuple[Chunk, ...]
    completed: bool


def run_agentic_rag(
    initial_query: str,
    retriever: Callable[[str], Sequence[Chunk]],
    planner: Callable[[str, Sequence[Chunk], int], str | None],
    max_iterations: int = 3,
) -> AgenticRAGResult:
    """运行有最大轮数和查询去重保护的多轮动态检索。"""
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    query = initial_query
    queries: list[str] = []
    chunks: dict[str, Chunk] = {}
    for iteration in range(max_iterations):
        if query in queries:
            return AgenticRAGResult(tuple(queries), tuple(chunks.values()), False)
        queries.append(query)
        for chunk in retriever(query):
            chunks[chunk.chunk_id] = chunk
        next_query = planner(initial_query, list(chunks.values()), iteration)
        if next_query is None:
            return AgenticRAGResult(tuple(queries), tuple(chunks.values()), True)
        query = next_query
    return AgenticRAGResult(tuple(queries), tuple(chunks.values()), False)


def run_demo() -> None:
    text = "退款申请需要订单号。企业用户由专属客服处理。处理时间不超过两个工作日。"
    index = build_parent_child_index("policy", text, parent_tokens=30, child_tokens=12)
    bm25 = BM25Index(index.children)
    hits = bm25.search("企业用户 客服")
    print("BM25 hits:", [(chunk.chunk_id, round(score, 4)) for chunk, score in hits])
    print("Expanded parents:", [chunk.text for chunk in index.expand(chunk.chunk_id for chunk, _ in hits)])


if __name__ == "__main__":
    run_demo()