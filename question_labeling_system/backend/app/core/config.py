"""从环境变量读取配置，并阻止不安全的生产启动。"""  # 将配置校验放在应用启动阶段。

from __future__ import annotations  # 允许 Python 3.9 使用延迟类型注解。

import os  # 从进程环境读取十二要素配置。
from dataclasses import dataclass  # 使用不可变数据类承载已验证配置。
from pathlib import Path  # 统一处理本地模型和数据库路径。
from typing import Tuple  # 使用显式元组类型兼容 Python 3.9。


def _as_bool(value: str, default: bool = False) -> bool:  # 将常见环境变量文本解析为布尔值。
    if not value:  # 未配置时使用调用方默认值。
        return default  # 返回安全默认值。
    normalized = value.strip().lower()  # 忽略首尾空格和大小写。
    if normalized in {"1", "true", "yes", "on"}:  # 识别真值集合。
        return True  # 返回布尔真。
    if normalized in {"0", "false", "no", "off"}:  # 识别假值集合。
        return False  # 返回布尔假。
    raise ValueError(f"invalid boolean value: {value}")  # 拒绝模糊配置，避免静默误启动。


def _as_csv(value: str) -> Tuple[str, ...]:  # 将逗号分隔环境变量转成稳定元组。
    return tuple(item.strip() for item in value.split(",") if item.strip())  # 过滤空元素并保留顺序。


@dataclass(frozen=True)  # 冻结配置，防止请求处理中被意外修改。
class Settings:  # 定义后端全部运行配置。
    environment: str  # 保存 development、test 或 production。
    database_url: str  # 保存 SQLAlchemy 数据库 URL。
    auth_disabled: bool  # 仅允许本地开发和测试关闭认证。
    jwt_public_key: str  # 保存 RS256 公钥文本或公钥文件路径。
    jwt_issuer: str  # 保存令牌签发者约束。
    jwt_audience: str  # 保存令牌受众约束。
    cors_origins: Tuple[str, ...]  # 保存允许访问 API 的前端源。
    historical_model_path: str  # 保存专用多标签模型工件路径。
    historical_model_mode: str  # 保存 artifact 或 bootstrap 模式。
    rag_mode: str  # 保存 hybrid 或 memory 模式。
    llm_mode: str  # 保存 openai 或 disabled 模式。
    openai_api_key: str  # 保存 OpenAI 兼容 API 密钥。
    openai_base_url: str  # 保存 OpenAI 兼容服务地址。
    openai_model: str  # 保存在线标签推理模型名称。
    embedding_model: str  # 保存 Dense RAG 的嵌入模型名称。
    qdrant_url: str  # 保存 Qdrant 服务地址。
    qdrant_api_key: str  # 保存可选 Qdrant API 密钥。
    qdrant_collection: str  # 保存题目向量集合名称。
    selection_threshold: float  # 保存默认勾选融合阈值。
    rag_limit: int  # 保存在线最大证据数量。
    claim_lease_seconds: int  # 保存人工任务领取租约时间。

    @classmethod  # 提供从环境构建配置的标准入口。
    def from_env(cls) -> "Settings":  # 读取环境并返回已验证配置。
        project_dir = Path(__file__).resolve().parents[3]  # 定位 question_labeling_system 项目根目录。
        default_db = f"sqlite:///{project_dir / 'data' / 'question_labeling.db'}"  # 为本地开发提供零依赖 SQLite。
        settings = cls(  # 构造所有字段，避免业务代码散落 os.getenv。
            environment=os.getenv("APP_ENV", "development").strip().lower(),  # 读取运行环境。
            database_url=os.getenv("DATABASE_URL", default_db).strip(),  # 读取数据库连接地址。
            auth_disabled=_as_bool(os.getenv("AUTH_DISABLED", "true"), default=True),  # 本地默认关闭认证但生产会阻止。
            jwt_public_key=os.getenv("JWT_PUBLIC_KEY", "").strip(),  # 读取 JWT 验签公钥。
            jwt_issuer=os.getenv("JWT_ISSUER", "question-labeling").strip(),  # 读取签发者。
            jwt_audience=os.getenv("JWT_AUDIENCE", "question-labeling-api").strip(),  # 读取受众。
            cors_origins=_as_csv(os.getenv("CORS_ORIGINS", "http://localhost:5173")),  # 读取前端白名单。
            historical_model_path=os.getenv("HISTORICAL_MODEL_PATH", str(project_dir / "artifacts" / "label_model.joblib")).strip(),  # 读取模型工件路径。
            historical_model_mode=os.getenv("HISTORICAL_MODEL_MODE", "bootstrap").strip().lower(),  # 本地允许冷启动模型。
            rag_mode=os.getenv("RAG_MODE", "memory").strip().lower(),  # 本地使用内存样本，生产使用混合检索。
            llm_mode=os.getenv("LLM_MODE", "disabled").strip().lower(),  # 本地无密钥时允许 LLM 降级。
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),  # 读取模型 API 密钥。
            openai_base_url=os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1").strip(),  # 默认兼容本地 Ollama。
            openai_model=os.getenv("OPENAI_MODEL", "qwen2.5:3b").strip(),  # 读取推理模型名称。
            embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", "nomic-embed-text").strip(),  # 读取向量模型名称。
            qdrant_url=os.getenv("QDRANT_URL", "http://qdrant:6333").strip(),  # 读取向量数据库地址。
            qdrant_api_key=os.getenv("QDRANT_API_KEY", "").strip(),  # 读取向量数据库密钥。
            qdrant_collection=os.getenv("QDRANT_COLLECTION", "labeled_questions_v1").strip(),  # 读取集合名称。
            selection_threshold=float(os.getenv("SELECTION_THRESHOLD", "0.58")),  # 解析融合阈值。
            rag_limit=int(os.getenv("RAG_LIMIT", "6")),  # 解析证据上限。
            claim_lease_seconds=int(os.getenv("CLAIM_LEASE_SECONDS", "900")),  # 解析任务租约秒数。
        )  # 完成配置构造。
        settings.validate()  # 在返回前执行跨字段安全校验。
        return settings  # 返回不可变配置。

    def validate(self) -> None:  # 校验范围以及生产环境必需项。
        if self.environment not in {"development", "test", "production"}:  # 限制环境名称。
            raise ValueError("APP_ENV must be development, test, or production")  # 报告环境配置错误。
        if not 0.0 < self.selection_threshold < 1.0:  # 检查默认选择阈值范围。
            raise ValueError("SELECTION_THRESHOLD must be between 0 and 1")  # 阻止无效阈值。
        if self.rag_limit <= 0 or self.claim_lease_seconds <= 0:  # 检查正整数配置。
            raise ValueError("RAG_LIMIT and CLAIM_LEASE_SECONDS must be positive")  # 阻止零值导致空召回或瞬时租约。
        if self.historical_model_mode not in {"artifact", "bootstrap"}:  # 限制专用模型模式。
            raise ValueError("HISTORICAL_MODEL_MODE must be artifact or bootstrap")  # 报告模型模式错误。
        if self.rag_mode not in {"hybrid", "memory"}:  # 限制检索模式。
            raise ValueError("RAG_MODE must be hybrid or memory")  # 报告检索模式错误。
        if self.llm_mode not in {"openai", "disabled"}:  # 限制 LLM 模式。
            raise ValueError("LLM_MODE must be openai or disabled")  # 报告 LLM 模式错误。
        if self.environment == "production":  # 对线上环境实施更严格的安全门禁。
            if self.auth_disabled:  # 生产环境禁止关闭认证。
                raise ValueError("AUTH_DISABLED must be false in production")  # 阻止无认证上线。
            if not self.jwt_public_key:  # 生产环境必须有验签公钥。
                raise ValueError("JWT_PUBLIC_KEY is required in production")  # 阻止无法验证身份的启动。
            if not self.database_url.startswith("mysql+"):  # 生产设计基于 MySQL 8 的事务与锁。
                raise ValueError("production DATABASE_URL must use a MySQL SQLAlchemy driver")  # 阻止误用本地 SQLite。
            if self.historical_model_mode != "artifact":  # 生产必须加载训练工件。
                raise ValueError("production requires HISTORICAL_MODEL_MODE=artifact")  # 阻止冷启动规则冒充训练模型。
            if self.rag_mode != "hybrid":  # 生产必须启用可扩展混合检索。
                raise ValueError("production requires RAG_MODE=hybrid")  # 阻止内存检索处理百万数据。
            if self.llm_mode != "openai" or not self.openai_api_key:  # 生产必须配置可调用的结构化 LLM。
                raise ValueError("production requires LLM_MODE=openai and OPENAI_API_KEY")  # 阻止静默关闭 LLM。
