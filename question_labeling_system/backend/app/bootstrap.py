"""装配 API 与 Worker 共用的数据库、模型、RAG 和应用服务依赖。"""  # 避免导入 Web 模块时产生重复初始化副作用。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

from dataclasses import dataclass  # 使用不可变容器聚合应用依赖。

from app.adapters.historical_model import BootstrapHistoricalModel, SklearnHistoricalLabelModel  # 导入监督模型适配器。
from app.adapters.llm_reasoner import DisabledLabelReasoner, LangChainLabelReasoner  # 导入 LLM 推理适配器。
from app.adapters.rag import InMemoryEvidenceRetriever, MySqlFullTextSearchBackend, QdrantDenseSearchBackend, ReciprocalRankFusionRetriever  # 导入本地与生产 RAG。
from app.core.config import Settings  # 导入已验证环境配置。
from app.core.metrics import ServiceMetrics  # 导入 Prometheus 指标收集器。
from app.db.models import Base  # 导入 ORM metadata 供本地建表。
from app.db.repositories import QuestionRepository  # 导入事务仓储。
from app.db.session import build_engine, build_session_factory  # 导入数据库工厂。
from app.seed_data import local_evidence  # 导入原创本地证据。
from app.services.application import LabelingApplicationService  # 导入用例服务。
from app.services.labeling_workflow import QuestionLabelingWorkflow  # 导入核心 LangGraph 工作流。


@dataclass(frozen=True)  # 冻结依赖容器防止请求修改全局对象。
class AppContainer:  # 聚合 API 与 Worker 需要的配置和服务。
    settings: Settings  # 保存不可变配置。
    service: LabelingApplicationService  # 保存线程安全的无状态用例服务。
    metrics: ServiceMetrics  # 保存应用独立 Prometheus Registry。


def build_container(settings: Settings) -> AppContainer:  # 根据环境装配生产或本地适配器。
    engine = build_engine(settings.database_url)  # 创建数据库连接池。
    if settings.environment in {"development", "test"}:  # 本地学习环境允许自动建表。
        Base.metadata.create_all(engine)  # 生产环境必须使用 Alembic 迁移而非 create_all。
    session_factory = build_session_factory(engine)  # 创建仓储 Session 工厂。
    repository = QuestionRepository(session_factory)  # 创建事务仓储。
    historical_model = SklearnHistoricalLabelModel(settings.historical_model_path) if settings.historical_model_mode == "artifact" else BootstrapHistoricalModel()  # 按配置选择训练工件或冷启动模型。
    if settings.rag_mode == "hybrid":  # 生产模式组合 MySQL Sparse 与 Qdrant Dense。
        from langchain_openai import OpenAIEmbeddings  # 延迟导入只在混合模式需要的向量模型。

        embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key, base_url=settings.openai_base_url)  # 创建与索引任务一致的 Embeddings 客户端。
        sparse = MySqlFullTextSearchBackend(session_factory)  # 使用 MySQL FULLTEXT 获取关键词结果。
        dense = QdrantDenseSearchBackend(settings.qdrant_url, settings.qdrant_collection, embeddings, settings.qdrant_api_key)  # 使用 Qdrant 获取语义结果。
        retriever = ReciprocalRankFusionRetriever(sparse, dense)  # 使用 RRF 融合不同分数空间。
    else:  # 本地模式使用少量原创合成样本。
        retriever = InMemoryEvidenceRetriever(local_evidence())  # 创建零外部依赖检索器。
    reasoner = LangChainLabelReasoner(settings.openai_api_key, settings.openai_base_url, settings.openai_model) if settings.llm_mode == "openai" else DisabledLabelReasoner()  # 按配置启用结构化 LLM 或显式降级。
    workflow = QuestionLabelingWorkflow(historical_model, retriever, reasoner, settings.selection_threshold, settings.rag_limit)  # 编译在线标注图。
    metrics = ServiceMetrics()  # 创建应用独立低基数指标集合。
    service = LabelingApplicationService(repository, workflow, settings.claim_lease_seconds, metrics)  # 创建带观测能力的用例服务。
    return AppContainer(settings=settings, service=service, metrics=metrics)  # 返回完整依赖容器。
