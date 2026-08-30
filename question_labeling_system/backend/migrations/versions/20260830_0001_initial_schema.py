"""创建题目补录、预测队列、人工反馈和审计表。"""  # 定义首个不可变生产数据库版本。

from __future__ import annotations  # 延迟类型求值以兼容 Python 3.9。

from alembic import op  # 使用 Alembic 操作数据库结构。
import sqlalchemy as sa  # 使用 SQLAlchemy 定义跨数据库字段。


revision = "20260830_0001"  # 定义当前迁移版本。
down_revision = None  # 首个版本没有父迁移。
branch_labels = None  # 当前不使用迁移分支。
depends_on = None  # 当前没有额外版本依赖。


def upgrade() -> None:  # 创建生产系统首版表结构。
    op.create_table("questions", sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True), sa.Column("external_id", sa.String(length=128), nullable=False), sa.Column("subject", sa.String(length=32), nullable=False), sa.Column("stem", sa.Text(), nullable=False), sa.Column("reference_answer", sa.Text(), nullable=False), sa.Column("analysis", sa.Text(), nullable=False, server_default=""), sa.Column("source", sa.String(length=128), nullable=False, server_default="internal"), sa.Column("source_uri", sa.String(length=2048), nullable=False, server_default=""), sa.Column("license_name", sa.String(length=128), nullable=False, server_default="internal"), sa.Column("latest_annotation_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True), sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"), sa.Column("version", sa.Integer(), nullable=False, server_default="1"), sa.Column("claimed_by", sa.String(length=128), nullable=True), sa.Column("claimed_until", sa.DateTime(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("external_id", name="uq_questions_external_id"))  # 创建题目、最新标注指针、来源许可和人工任务状态表。
    op.create_index("ix_questions_subject", "questions", ["subject"])  # 支持按学科过滤。
    op.create_index("ix_questions_status", "questions", ["status"])  # 支持按任务状态过滤。
    op.create_index("ix_questions_claimed_by", "questions", ["claimed_by"])  # 支持查看操作员当前任务。
    op.create_index("ix_questions_claimed_until", "questions", ["claimed_until"])  # 支持回收过期租约。
    op.create_index("ix_questions_created_at", "questions", ["created_at"])  # 支持 FIFO 排序。
    op.create_index("ix_questions_latest_annotation_id", "questions", ["latest_annotation_id"])  # 支持百万题量直接关联最新人工结论。
    op.create_index("ix_questions_claim_queue", "questions", ["status", "subject", "claimed_until", "created_at"])  # 优化领取队列查询。
    op.create_table("prediction_jobs", sa.Column("id", sa.String(length=36), primary_key=True), sa.Column("question_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("questions.id"), nullable=False), sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"), sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("available_at", sa.DateTime(), nullable=False), sa.Column("leased_by", sa.String(length=128), nullable=True), sa.Column("leased_until", sa.DateTime(), nullable=True), sa.Column("last_error", sa.String(length=512), nullable=False, server_default=""), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("question_id", name="uq_prediction_job_question"))  # 创建可靠预测队列表。
    op.create_index("ix_prediction_jobs_question_id", "prediction_jobs", ["question_id"])  # 支持按题目查询任务。
    op.create_index("ix_prediction_jobs_status", "prediction_jobs", ["status"])  # 支持队列状态过滤。
    op.create_index("ix_prediction_jobs_available_at", "prediction_jobs", ["available_at"])  # 支持退避调度。
    op.create_index("ix_prediction_jobs_leased_by", "prediction_jobs", ["leased_by"])  # 支持 Worker 诊断。
    op.create_index("ix_prediction_jobs_leased_until", "prediction_jobs", ["leased_until"])  # 支持租约回收。
    op.create_index("ix_prediction_jobs_created_at", "prediction_jobs", ["created_at"])  # 支持 FIFO 排序。
    op.create_index("ix_prediction_jobs_claim", "prediction_jobs", ["status", "available_at", "leased_until"])  # 优化 SKIP LOCKED 领取。
    op.create_table("prediction_runs", sa.Column("id", sa.String(length=36), primary_key=True), sa.Column("question_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("questions.id"), nullable=False), sa.Column("model_version", sa.String(length=512), nullable=False), sa.Column("result_json", sa.JSON(), nullable=False), sa.Column("latency_ms", sa.Float(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))  # 创建不可变预测运行快照。
    op.create_index("ix_prediction_runs_question_id", "prediction_runs", ["question_id"])  # 支持按题目查询预测。
    op.create_index("ix_prediction_runs_created_at", "prediction_runs", ["created_at"])  # 支持查最新预测。
    op.create_table("label_suggestions", sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True), sa.Column("prediction_id", sa.String(length=36), sa.ForeignKey("prediction_runs.id"), nullable=False), sa.Column("question_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("questions.id"), nullable=False), sa.Column("tag_code", sa.String(length=128), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("selected_by_default", sa.Boolean(), nullable=False), sa.Column("high_risk", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("reason", sa.Text(), nullable=False), sa.Column("source_scores", sa.JSON(), nullable=False), sa.Column("evidence_ids", sa.JSON(), nullable=False), sa.UniqueConstraint("prediction_id", "tag_code", name="uq_prediction_tag"))  # 创建单标签建议明细。
    op.create_index("ix_label_suggestions_prediction_id", "label_suggestions", ["prediction_id"])  # 支持按预测读取建议。
    op.create_index("ix_label_suggestions_question_id", "label_suggestions", ["question_id"])  # 支持按题目统计。
    op.create_index("ix_label_suggestions_tag_code", "label_suggestions", ["tag_code"])  # 支持逐标签评测。
    op.create_table("human_annotations", sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True), sa.Column("question_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("questions.id"), nullable=False), sa.Column("prediction_id", sa.String(length=36), sa.ForeignKey("prediction_runs.id"), nullable=True), sa.Column("selected_tags", sa.JSON(), nullable=False), sa.Column("added_tags", sa.JSON(), nullable=False), sa.Column("removed_tags", sa.JSON(), nullable=False), sa.Column("operator_id", sa.String(length=128), nullable=False), sa.Column("note", sa.Text(), nullable=False, server_default=""), sa.Column("model_version", sa.String(length=512), nullable=False, server_default=""), sa.Column("idempotency_key", sa.String(length=128), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("idempotency_key", name="uq_annotation_idempotency"))  # 创建人工标签和反馈表。
    op.create_index("ix_human_annotations_question_id", "human_annotations", ["question_id"])  # 支持按题目查询历史。
    op.create_index("ix_human_annotations_prediction_id", "human_annotations", ["prediction_id"])  # 支持模型反馈关联。
    op.create_index("ix_human_annotations_operator_id", "human_annotations", ["operator_id"])  # 支持人员审计。
    op.create_index("ix_human_annotations_created_at", "human_annotations", ["created_at"])  # 支持增量训练游标。
    op.create_index("ix_annotations_training", "human_annotations", ["question_id", "created_at"])  # 优化训练集抽取。
    op.create_table("audit_events", sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True), sa.Column("request_id", sa.String(length=64), nullable=False), sa.Column("actor_id", sa.String(length=128), nullable=False), sa.Column("action", sa.String(length=128), nullable=False), sa.Column("resource_type", sa.String(length=64), nullable=False), sa.Column("resource_id", sa.String(length=128), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))  # 创建业务审计表。
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"])  # 支持按请求追踪。
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])  # 支持按主体审计。
    op.create_index("ix_audit_events_action", "audit_events", ["action"])  # 支持按动作审计。
    op.create_index("ix_audit_events_resource_id", "audit_events", ["resource_id"])  # 支持按资源审计。
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])  # 支持时间范围查询。
    if op.get_bind().dialect.name == "mysql":  # 仅 MySQL 支持目标 FULLTEXT 语法。
        op.execute("ALTER TABLE questions ADD FULLTEXT INDEX ft_questions_content (stem, reference_answer, analysis) WITH PARSER ngram")  # 使用 MySQL ngram parser 支持中文语文题 Sparse 召回。


def downgrade() -> None:  # 按外键依赖逆序删除首版结构。
    if op.get_bind().dialect.name == "mysql":  # 仅 MySQL 存在 FULLTEXT 索引。
        op.execute("ALTER TABLE questions DROP INDEX ft_questions_content")  # 删除 Sparse RAG 索引。
    op.drop_table("audit_events")  # 删除审计表及其索引。
    op.drop_table("human_annotations")  # 删除人工反馈表。
    op.drop_table("label_suggestions")  # 删除标签建议表。
    op.drop_table("prediction_runs")  # 删除预测运行表。
    op.drop_table("prediction_jobs")  # 删除预测队列表。
    op.drop_table("questions")  # 最后删除题目表。