"""将人工确认题目批量写入 Qdrant Dense 索引。"""  # MySQL FULLTEXT 同时提供 Sparse 路由，在线通过 RRF 融合。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import argparse  # 解析数据源、模型、批大小和集合参数。
import json  # 输出适合流水线归档的执行摘要。
import os  # 读取数据库和模型环境变量。
import uuid  # 将业务字符串 ID 稳定映射为 Qdrant UUID。
from pathlib import Path  # 解析 JSONL 数据路径。
from typing import Any, Callable, Dict, Iterable, Iterator, List  # 标注批处理与 payload。

from langchain_openai import OpenAIEmbeddings  # 使用与在线检索一致的 Embedding 适配器。

from app.adapters.historical_model import question_to_model_text  # 保证索引文本字段稳定。
from app.training.train_historical_model import TrainingExample, iter_database_examples, iter_jsonl_examples  # 复用百万数据流式读取器。


POINT_NAMESPACE = uuid.UUID("8c569c2e-28ae-4db8-9a94-912bb921f114")  # 固定命名空间保证重复索引同一题得到相同 Point ID。


def stable_point_id(external_id: str) -> str:  # 将任意上游 ID 转成 Qdrant 接受的 UUID。
    return str(uuid.uuid5(POINT_NAMESPACE, external_id))  # 使用确定性 UUID5 支持幂等 upsert。


def point_payload(example: TrainingExample) -> Dict[str, Any]:  # 构造在线 Dense 检索需要的最小 payload。
    return {"question_id": example.external_id, "subject": example.question.subject.value, "stem": example.question.stem, "reference_answer": example.question.reference_answer, "human_labels": list(example.labels), "source": example.question.source, "source_uri": example.question.source_uri, "license_name": example.question.license_name}  # 保留来源、许可证和人工标签供 RAG 解释。


def _batches(values: Iterable[TrainingExample], batch_size: int) -> Iterator[List[TrainingExample]]:  # 将流式题目聚合为 Embedding 批次。
    batch: List[TrainingExample] = []  # 初始化当前批次。
    for value in values:  # 遍历数据源。
        batch.append(value)  # 加入当前批次。
        if len(batch) >= batch_size:  # 达到 API 批量上限时输出。
            yield batch  # 交给索引循环。
            batch = []  # 创建下一批新列表。
    if batch:  # 处理尾部不足批大小的数据。
        yield batch  # 避免丢失尾部样本。


def index_examples(example_factory: Callable[[], Iterable[TrainingExample]], qdrant_url: str, collection_name: str, embedding_model: OpenAIEmbeddings, qdrant_api_key: str = "", batch_size: int = 64, recreate: bool = False) -> Dict[str, Any]:  # 执行幂等批量 Dense 索引。
    if batch_size <= 0:  # 检查批处理配置。
        raise ValueError("batch_size must be positive")  # 阻止空循环或无限等待。
    from qdrant_client import QdrantClient  # type: ignore  # 仅索引任务需要 Qdrant SDK。
    from qdrant_client.models import Distance, PointStruct, VectorParams  # type: ignore  # 导入集合与点模型。

    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key or None, timeout=30.0)  # 创建管理和写入客户端。
    collection_names = {item.name for item in client.get_collections().collections}  # 查询现有集合避免依赖版本特定 exists API。
    if recreate and collection_name in collection_names:  # 仅在显式参数下允许破坏性重建。
        client.delete_collection(collection_name=collection_name)  # 删除旧索引但不修改 MySQL 事实数据。
        collection_names.remove(collection_name)  # 更新本地集合快照。
    indexed = 0  # 统计成功写入点数量。
    vector_size = 0  # 保存首批向量维度用于创建集合。
    for batch in _batches(example_factory(), batch_size):  # 流式处理全部人工题目。
        texts = [question_to_model_text(item.question) for item in batch]  # 构造稳定 Embedding 文本。
        vectors = embedding_model.embed_documents(texts)  # 使用 Provider 批量生成 Dense 向量。
        if len(vectors) != len(batch):  # 防止 Provider 返回数量不一致。
            raise RuntimeError("embedding response count does not match batch size")  # 中止发布不完整索引。
        if not vectors or not vectors[0]:  # 防止空向量创建无效集合。
            raise RuntimeError("embedding model returned an empty vector")  # 返回可观测错误。
        if not vector_size:  # 首批响应决定模型真实向量维度。
            vector_size = len(vectors[0])  # 保存维度。
            if collection_name not in collection_names:  # 集合不存在时按真实维度创建。
                client.create_collection(collection_name=collection_name, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))  # 使用余弦距离与在线查询一致。
                collection_names.add(collection_name)  # 更新本地集合快照。
        if any(len(vector) != vector_size for vector in vectors):  # 检查同批和跨批向量维度一致。
            raise RuntimeError("embedding vector dimensions changed during indexing")  # 防止混合模型污染集合。
        points = [PointStruct(id=stable_point_id(item.external_id), vector=vector, payload=point_payload(item)) for item, vector in zip(batch, vectors)]  # 构造幂等点与最小 payload。
        client.upsert(collection_name=collection_name, points=points, wait=True)  # 等待服务端确认后再推进批次。
        indexed += len(points)  # 累积成功数量。
    if indexed == 0:  # 空数据源不能发布可用索引。
        raise ValueError("no examples were available for RAG indexing")  # 提醒检查训练快照。
    return {"collection": collection_name, "indexed": indexed, "vector_size": vector_size}  # 返回流水线摘要。


def parse_args() -> argparse.Namespace:  # 定义 RAG 索引命令参数。
    parser = argparse.ArgumentParser(description="Index labeled questions into Qdrant")  # 创建参数解析器。
    source = parser.add_mutually_exclusive_group(required=True)  # 强制选择数据库或快照。
    source.add_argument("--input-jsonl", help="规范化人工标签 JSONL 快照")  # 支持可复现离线索引。
    source.add_argument("--database-url", default=os.getenv("DATABASE_URL"), help="MySQL SQLAlchemy URL")  # 支持直接读取生产只读副本。
    parser.add_argument("--qdrant-url", default=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), help="Qdrant 地址")  # 读取向量库地址。
    parser.add_argument("--qdrant-api-key", default=os.getenv("QDRANT_API_KEY", ""), help="Qdrant API 密钥")  # 支持托管向量库认证。
    parser.add_argument("--collection", default=os.getenv("QDRANT_COLLECTION", "labeled_questions_v1"), help="目标集合")  # 推荐使用版本化集合名。
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""), help="OpenAI 兼容 API 密钥")  # 读取 Embedding 密钥。
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1"), help="OpenAI 兼容地址")  # 支持本地 Ollama 或模型网关。
    parser.add_argument("--embedding-model", default=os.getenv("OPENAI_EMBEDDING_MODEL", "nomic-embed-text"), help="Embedding 模型")  # 固定索引模型版本。
    parser.add_argument("--batch-size", type=int, default=64, help="Embedding 与 upsert 批大小")  # 控制 API 与内存压力。
    parser.add_argument("--limit", type=int, default=0, help="开发模式样本上限，0 表示全部")  # 支持小规模验证。
    parser.add_argument("--recreate", action="store_true", help="删除并重建同名集合，生产使用前需审批")  # 将破坏性行为设为显式开关。
    return parser.parse_args()  # 返回解析参数。


def main() -> None:  # 执行索引任务。
    args = parse_args()  # 读取 CLI 参数。
    if not args.api_key:  # Embedding 服务必须有明确认证值，本地 Ollama 可传 ollama。
        raise ValueError("OPENAI_API_KEY or --api-key is required")  # 防止匿名请求意外发送到公网。
    if args.input_jsonl:  # 构造可重复读取 JSONL 的工厂。
        path = Path(args.input_jsonl).resolve()  # 解析数据快照路径。
        factory = lambda: iter_jsonl_examples(path, args.limit)  # 每次任务重新打开文件。
    else:  # 构造 MySQL 流式工厂。
        factory = lambda: iter_database_examples(str(args.database_url), args.batch_size, args.limit)  # 从只读库分批读取。
    embeddings = OpenAIEmbeddings(model=args.embedding_model, api_key=args.api_key, base_url=args.base_url)  # 创建 LangChain Embedding 适配器。
    summary = index_examples(factory, args.qdrant_url, args.collection, embeddings, args.qdrant_api_key, args.batch_size, args.recreate)  # 执行索引。
    print(json.dumps(summary, ensure_ascii=False, indent=2))  # 输出可归档 JSON 摘要。


if __name__ == "__main__":  # 允许 `python -m app.training.index_rag` 启动。
    main()  # 执行索引任务。
