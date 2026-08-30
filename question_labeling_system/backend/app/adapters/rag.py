"""实现本地样本检索以及 MySQL Sparse + Qdrant Dense 的 RRF 混合 RAG。"""  # 在线图通过 EvidenceRetriever 协议使用本模块。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import math  # 使用对数归一化 MySQL FULLTEXT 分数。
import re  # 提取中英文检索词。
from dataclasses import dataclass  # 定义统一检索命中对象。
from typing import Any, Dict, List, Protocol, Sequence  # 定义后端协议和返回结构。

from sqlalchemy import text  # 执行 MySQL MATCH AGAINST 查询。
from sqlalchemy.orm import sessionmaker  # 创建短生命周期稀疏检索 Session。

from app.domain.models import QuestionInput, RetrievedEvidence, Subject  # 导入标准题目与证据结构。


@dataclass(frozen=True)  # 检索命中在融合期间不可修改。
class SearchHit:  # 统一 Sparse 和 Dense 后端返回格式。
    question_id: str  # 保存历史题 ID。
    subject: Subject  # 保存学科以验证过滤条件。
    stem: str  # 保存历史题干。
    reference_answer: str  # 保存历史答案。
    human_labels: List[str]  # 保存人工标签。
    raw_score: float  # 保存当前检索后端原始分数。
    source: str  # 保存数据来源。
    source_uri: str = ""  # 保存内部记录或公开数据集地址。
    license_name: str = "internal"  # 保存数据许可证或内部授权。


class SearchBackend(Protocol):  # 定义单路检索器接口。
    def search(self, question: QuestionInput, limit: int) -> Sequence[SearchHit]:  # 返回按相关性降序排列的结果。
        ...  # 具体实现可以是 MySQL、Qdrant 或测试替身。


def _terms(text_value: str) -> set[str]:  # 为本地开发提取简单中英文 term。
    return set(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text_value.lower()))  # 英文按词、中文按字切分。


class InMemoryEvidenceRetriever:  # 本地开发使用的小规模相似题检索器。
    def __init__(self, evidence: Sequence[RetrievedEvidence]) -> None:  # 注入合成或测试样本。
        self._evidence = list(evidence)  # 复制输入避免外部修改。

    def retrieve(self, question: QuestionInput, limit: int) -> Sequence[RetrievedEvidence]:  # 按词集合 Jaccard 相似度召回。
        query_terms = _terms(question.stem + " " + question.reference_answer)  # 将题干和答案共同作为检索信号。
        scored: List[RetrievedEvidence] = []  # 初始化重评分结果。
        for item in self._evidence:  # 遍历仅用于本地的小样本集合。
            if item.subject != question.subject:  # 严格限制同学科召回。
                continue  # 跳过跨学科证据。
            candidate_terms = _terms(item.stem + " " + item.reference_answer)  # 提取候选 term。
            union = query_terms | candidate_terms  # 计算 Jaccard 分母集合。
            overlap = query_terms & candidate_terms  # 计算 Jaccard 分子集合。
            lexical_score = len(overlap) / len(union) if union else 0.0  # 计算 0 到 1 的相似度。
            scored.append(item.model_copy(update={"score": lexical_score}))  # 创建带本次查询分数的新证据对象。
        scored.sort(key=lambda item: item.score, reverse=True)  # 按相关性降序排列。
        return scored[:limit]  # 返回限制数量的证据。


class ReciprocalRankFusionRetriever:  # 将 Sparse 与 Dense 排名融合为稳定证据列表。
    def __init__(self, sparse: SearchBackend, dense: SearchBackend, rrf_k: int = 60, sparse_weight: float = 0.45, dense_weight: float = 0.55) -> None:  # 注入两路后端和权重。
        if rrf_k <= 0:  # 检查 RRF 平滑常数。
            raise ValueError("rrf_k must be positive")  # 防止无效除数。
        self._sparse = sparse  # 保存关键词检索后端。
        self._dense = dense  # 保存语义检索后端。
        self._rrf_k = rrf_k  # 保存排名平滑常数。
        self._weights = {"sparse": sparse_weight, "dense": dense_weight}  # 保存可版本化路由权重。

    def retrieve(self, question: QuestionInput, limit: int) -> Sequence[RetrievedEvidence]:  # 并行可扩展的两路召回与 RRF 融合入口。
        candidate_limit = max(limit * 3, 20)  # 扩大候选池后再融合，避免过早截断。
        routes = {"sparse": list(self._sparse.search(question, candidate_limit)), "dense": list(self._dense.search(question, candidate_limit))}  # 当前同步实现依次调用，API 可进一步放在线程池并行。
        scores: Dict[str, float] = {}  # 保存每个题目的 RRF 累积分数。
        hits: Dict[str, SearchHit] = {}  # 保存题目最新元数据。
        evidence_routes: Dict[str, List[str]] = {}  # 保存命中来源便于审计。
        for route_name, route_hits in routes.items():  # 遍历两条召回路由。
            for rank, hit in enumerate(route_hits, start=1):  # 只使用排名，不直接混合不同分数空间。
                hits[hit.question_id] = hit  # 按题目 ID 去重候选。
                scores[hit.question_id] = scores.get(hit.question_id, 0.0) + self._weights[route_name] / (self._rrf_k + rank)  # 累加加权 RRF 分数。
                evidence_routes.setdefault(hit.question_id, []).append(route_name)  # 记录该题在哪些路由命中。
        max_possible = sum(self._weights.values()) / (self._rrf_k + 1)  # 计算两路都排名第一的理论最大分数。
        ranked_ids = sorted(scores, key=scores.get, reverse=True)[:limit]  # 按融合分数选择最终证据。
        result: List[RetrievedEvidence] = []  # 初始化领域证据列表。
        for question_id in ranked_ids:  # 转换每个融合命中。
            hit = hits[question_id]  # 读取历史题内容。
            normalized_score = scores[question_id] / max_possible if max_possible else 0.0  # 将 RRF 分数归一化到 0 到 1。
            route_suffix = "+".join(sorted(set(evidence_routes[question_id])))  # 生成可读召回来源。
            result.append(RetrievedEvidence(evidence_id=f"rag:{question_id}", question_id=question_id, subject=hit.subject, stem=hit.stem, reference_answer=hit.reference_answer, human_labels=hit.human_labels, score=min(1.0, normalized_score), source=f"{hit.source}:{route_suffix}", source_uri=hit.source_uri, license_name=hit.license_name))  # 创建带人工标签和授权信息的可追溯证据。
        return result  # 返回融合结果。


class MySqlFullTextSearchBackend:  # 使用 MySQL 8 FULLTEXT/BM25 风格相关度实现 Sparse 召回。
    def __init__(self, session_factory: sessionmaker) -> None:  # 注入数据库 Session 工厂。
        self._session_factory = session_factory  # 保存连接入口。

    def search(self, question: QuestionInput, limit: int) -> Sequence[SearchHit]:  # 执行同学科全文检索。
        query = f"{question.stem} {question.reference_answer}"[:4_000]  # 限制检索文本长度以保护数据库。
        sql = text("""SELECT q.id, q.subject, q.stem, q.reference_answer, q.source, q.source_uri, q.license_name, ha.selected_tags, MATCH(q.stem, q.reference_answer, q.analysis) AGAINST (:query IN NATURAL LANGUAGE MODE) AS relevance FROM questions q JOIN human_annotations ha ON ha.id = q.latest_annotation_id WHERE q.subject = :subject AND q.status = 'completed' AND MATCH(q.stem, q.reference_answer, q.analysis) AGAINST (:query IN NATURAL LANGUAGE MODE) ORDER BY relevance DESC LIMIT :limit""")  # 通过最新标注指针执行 FULLTEXT 检索并保留来源许可。
        with self._session_factory() as session:  # 创建只读 Session。
            rows = session.execute(sql, {"query": query, "subject": question.subject.value, "limit": limit}).mappings().all()  # 执行参数化查询避免 SQL 注入。
        return [SearchHit(question_id=str(row["id"]), subject=Subject(row["subject"]), stem=str(row["stem"]), reference_answer=str(row["reference_answer"]), human_labels=list(row["selected_tags"] or []), raw_score=1.0 - math.exp(-max(0.0, float(row["relevance"] or 0.0))), source=str(row["source"]), source_uri=str(row["source_uri"] or ""), license_name=str(row["license_name"] or "internal")) for row in rows]  # 将不定范围分数压缩到 0 到 1 并保留授权信息。


class QdrantDenseSearchBackend:  # 使用 OpenAI 兼容 Embedding 与 Qdrant 实现 Dense 召回。
    def __init__(self, qdrant_url: str, collection_name: str, embedding_model: Any, api_key: str = "") -> None:  # 注入向量库与 LangChain Embeddings。
        try:  # 将可选 Qdrant 依赖限制在生产混合模式。
            from qdrant_client import QdrantClient  # type: ignore  # 导入 Qdrant 客户端。
        except ImportError as exc:  # 捕获镜像遗漏生产依赖。
            raise RuntimeError("qdrant-client is required for hybrid RAG mode") from exc  # 返回明确安装提示。
        self._client = QdrantClient(url=qdrant_url, api_key=api_key or None, timeout=10.0)  # 创建带超时的客户端。
        self._collection_name = collection_name  # 保存集合名称。
        self._embedding_model = embedding_model  # 保存 LangChain Embeddings 适配器。

    def search(self, question: QuestionInput, limit: int) -> Sequence[SearchHit]:  # 向量化题目并按学科过滤召回。
        from qdrant_client.models import FieldCondition, Filter, MatchValue  # type: ignore  # 延迟导入查询过滤模型。

        vector = self._embedding_model.embed_query(f"{question.stem}\n{question.reference_answer}")  # 生成题目 Dense 向量。
        points = self._client.search(collection_name=self._collection_name, query_vector=vector, query_filter=Filter(must=[FieldCondition(key="subject", match=MatchValue(value=question.subject.value))]), limit=limit, with_payload=True)  # 执行同学科向量检索并返回 payload。
        hits: List[SearchHit] = []  # 初始化统一命中列表。
        for point in points:  # 转换 Qdrant ScoredPoint。
            payload = dict(point.payload or {})  # 复制 payload 防止空值。
            hits.append(SearchHit(question_id=str(payload.get("question_id", point.id)), subject=Subject(str(payload["subject"])), stem=str(payload["stem"]), reference_answer=str(payload["reference_answer"]), human_labels=list(payload.get("human_labels") or []), raw_score=max(0.0, min(1.0, float(point.score))), source=str(payload.get("source", "internal_question_bank")), source_uri=str(payload.get("source_uri", "")), license_name=str(payload.get("license_name", "internal"))))  # 转成带来源授权的领域统一结构。
        return hits  # 返回按 Qdrant 分数排序的命中。
