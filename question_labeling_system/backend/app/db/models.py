"""补录任务、预测、人工标签、训练反馈与审计的关系模型。"""  # 使用 MySQL 作为生产事实源。

from __future__ import annotations  # 延迟解析 ORM 关系类型。

from datetime import datetime  # 保存 UTC 时间戳。

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint  # 导入表字段和约束。
from sqlalchemy.orm import declarative_base, relationship  # 创建声明式基类和关系。


Base = declarative_base()  # 所有 ORM 表共享同一个元数据对象。
BIGINT_PRIMARY_KEY = BigInteger().with_variant(Integer, "sqlite")  # MySQL 使用 BIGINT，SQLite 测试使用可自增 INTEGER。


class QuestionRecord(Base):  # 保存进入补录系统的新题目及其任务状态。
    __tablename__ = "questions"  # 指定稳定表名。

    id = Column(BIGINT_PRIMARY_KEY, primary_key=True, autoincrement=True)  # 使用大整数主键支持百万级以上数据。
    external_id = Column(String(128), nullable=False, unique=True)  # 保存上游题库幂等 ID。
    subject = Column(String(32), nullable=False, index=True)  # 保存学科编码并建立过滤索引。
    stem = Column(Text, nullable=False)  # 保存题干原文。
    reference_answer = Column(Text, nullable=False)  # 保存标准答案原文。
    analysis = Column(Text, nullable=False, default="")  # 保存题目解析。
    source = Column(String(128), nullable=False, default="internal")  # 保存数据来源。
    source_uri = Column(String(2048), nullable=False, default="")  # 保存来源记录或公开数据集页面。
    license_name = Column(String(128), nullable=False, default="internal")  # 保存内部授权或公开许可证。
    latest_annotation_id = Column(BIGINT_PRIMARY_KEY, nullable=True, index=True)  # 指向最新人工标注，避免百万数据查询相关子查询。
    status = Column(String(32), nullable=False, default="pending", index=True)  # 保存 pending、in_review 或 completed。
    version = Column(Integer, nullable=False, default=1)  # 使用乐观锁防止两个人覆盖提交。
    claimed_by = Column(String(128), nullable=True, index=True)  # 保存当前领取任务的操作员 ID。
    claimed_until = Column(DateTime, nullable=True, index=True)  # 保存租约到期时间，避免永久锁任务。
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)  # 保存创建时间。
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)  # 保存最近更新时间。
    predictions = relationship("PredictionRunRecord", back_populates="question", cascade="all, delete-orphan")  # 关联全部预测版本。
    annotations = relationship("HumanAnnotationRecord", back_populates="question", cascade="all, delete-orphan")  # 关联人工提交历史。


class PredictionRunRecord(Base):  # 保存一次完整模型链运行快照。
    __tablename__ = "prediction_runs"  # 指定预测运行表名。

    id = Column(String(36), primary_key=True)  # 使用 UUID 字符串跨服务追踪。
    question_id = Column(BIGINT_PRIMARY_KEY, ForeignKey("questions.id"), nullable=False, index=True)  # 关联题目。
    model_version = Column(String(512), nullable=False)  # 保存监督模型、LLM Prompt 和融合策略版本。
    result_json = Column(JSON, nullable=False)  # 保存建议、证据、警告和来源分数快照。
    latency_ms = Column(Float, nullable=False)  # 保存端到端预测耗时。
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)  # 保存预测时间。
    question = relationship("QuestionRecord", back_populates="predictions")  # 建立反向题目关系。
    suggestions = relationship("LabelSuggestionRecord", back_populates="prediction", cascade="all, delete-orphan")  # 关联可查询建议明细。


class PredictionJobRecord(Base):  # 保存可由多个 Worker 安全领取的预测任务。
    __tablename__ = "prediction_jobs"  # 指定预测队列表名。
    __table_args__ = (UniqueConstraint("question_id", name="uq_prediction_job_question"),)  # 每个题目只保留一个当前预测任务。

    id = Column(String(36), primary_key=True)  # 使用 UUID 标识队列任务。
    question_id = Column(BIGINT_PRIMARY_KEY, ForeignKey("questions.id"), nullable=False, index=True)  # 关联待预测题目。
    status = Column(String(32), nullable=False, default="pending", index=True)  # 保存 pending、running、completed 或 failed。
    attempts = Column(Integer, nullable=False, default=0)  # 保存已开始尝试次数。
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)  # 保存退避后可再次领取时间。
    leased_by = Column(String(128), nullable=True, index=True)  # 保存当前 Worker ID。
    leased_until = Column(DateTime, nullable=True, index=True)  # 保存 Worker 租约到期时间。
    last_error = Column(String(512), nullable=False, default="")  # 保存脱敏错误摘要。
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)  # 保存任务创建时间。
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)  # 保存状态更新时间。


class LabelSuggestionRecord(Base):  # 保存预测运行中的单标签建议，便于统计准确率。
    __tablename__ = "label_suggestions"  # 指定建议表名。
    __table_args__ = (UniqueConstraint("prediction_id", "tag_code", name="uq_prediction_tag"),)  # 防止同一运行重复标签。

    id = Column(BIGINT_PRIMARY_KEY, primary_key=True, autoincrement=True)  # 使用自增明细主键。
    prediction_id = Column(String(36), ForeignKey("prediction_runs.id"), nullable=False, index=True)  # 关联预测运行。
    question_id = Column(BIGINT_PRIMARY_KEY, ForeignKey("questions.id"), nullable=False, index=True)  # 冗余题目 ID 加速反馈统计。
    tag_code = Column(String(128), nullable=False, index=True)  # 保存受控标签编码。
    confidence = Column(Float, nullable=False)  # 保存融合分数。
    selected_by_default = Column(Boolean, nullable=False)  # 保存页面初始是否勾选。
    high_risk = Column(Boolean, nullable=False, default=False)  # 保存当时标签风险属性。
    reason = Column(Text, nullable=False)  # 保存给人工的解释。
    source_scores = Column(JSON, nullable=False)  # 保存各信号源分数。
    evidence_ids = Column(JSON, nullable=False)  # 保存 RAG 证据 ID。
    prediction = relationship("PredictionRunRecord", back_populates="suggestions")  # 建立反向预测关系。


class HumanAnnotationRecord(Base):  # 保存人工最终标签与模型反馈。
    __tablename__ = "human_annotations"  # 指定人工标注表名。
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_annotation_idempotency"),)  # 保证客户端重试不会重复提交。

    id = Column(BIGINT_PRIMARY_KEY, primary_key=True, autoincrement=True)  # 使用自增主键。
    question_id = Column(BIGINT_PRIMARY_KEY, ForeignKey("questions.id"), nullable=False, index=True)  # 关联题目。
    prediction_id = Column(String(36), ForeignKey("prediction_runs.id"), nullable=True, index=True)  # 关联人工看到的预测版本。
    selected_tags = Column(JSON, nullable=False)  # 保存人工最终确认标签编码数组。
    added_tags = Column(JSON, nullable=False)  # 保存人工相对模型新增的标签，用于误漏标分析。
    removed_tags = Column(JSON, nullable=False)  # 保存人工取消的模型标签，用于误报分析。
    operator_id = Column(String(128), nullable=False, index=True)  # 保存已认证操作员 ID。
    note = Column(Text, nullable=False, default="")  # 保存可选复核备注。
    model_version = Column(String(512), nullable=False, default="")  # 固化产生建议的模型链版本。
    idempotency_key = Column(String(128), nullable=False)  # 保存客户端生成的提交幂等键。
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)  # 保存人工提交时间。
    question = relationship("QuestionRecord", back_populates="annotations")  # 建立反向题目关系。


class AuditEventRecord(Base):  # 保存不可依赖应用日志替代的业务审计事件。
    __tablename__ = "audit_events"  # 指定审计表名。

    id = Column(BIGINT_PRIMARY_KEY, primary_key=True, autoincrement=True)  # 使用自增事件 ID。
    request_id = Column(String(64), nullable=False, index=True)  # 保存一次 API 请求追踪 ID。
    actor_id = Column(String(128), nullable=False, index=True)  # 保存操作主体。
    action = Column(String(128), nullable=False, index=True)  # 保存 create、claim、predict 或 submit 等动作。
    resource_type = Column(String(64), nullable=False)  # 保存资源类型。
    resource_id = Column(String(128), nullable=False, index=True)  # 保存资源 ID。
    payload = Column(JSON, nullable=False)  # 保存脱敏后的动作上下文。
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)  # 保存事件时间。


Index("ix_questions_claim_queue", QuestionRecord.status, QuestionRecord.subject, QuestionRecord.claimed_until, QuestionRecord.created_at)  # 优化按学科领取待办任务。
Index("ix_annotations_training", HumanAnnotationRecord.question_id, HumanAnnotationRecord.created_at)  # 优化训练集增量抽取。
Index("ix_prediction_jobs_claim", PredictionJobRecord.status, PredictionJobRecord.available_at, PredictionJobRecord.leased_until)  # 优化多 Worker 领取预测任务。
