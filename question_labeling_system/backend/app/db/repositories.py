"""实现任务领取、预测持久化、人工幂等提交与训练反馈查询。"""  # 所有写操作都在显式事务中完成。

from __future__ import annotations  # 延迟类型求值以兼容 Python 3.9。

import uuid  # 为预测运行生成跨系统追踪 ID。
from dataclasses import dataclass  # 使用轻量 DTO 脱离 ORM Session。
from datetime import datetime, timedelta  # 计算任务租约到期时间。
from typing import Any, Dict, List, Optional, Sequence  # 声明仓储输入输出类型。

from sqlalchemy import and_, or_, select  # 构建兼容 MySQL 与 SQLite 的查询。
from sqlalchemy.orm import Session, sessionmaker  # 使用同步 Session 与工厂。

from app.db.models import AuditEventRecord, HumanAnnotationRecord, LabelSuggestionRecord, PredictionJobRecord, PredictionRunRecord, QuestionRecord  # 导入持久化模型。
from app.domain.models import PredictionResult, QuestionInput, Subject  # 导入领域对象。
from app.domain.taxonomy import require_known_tag  # 提交前验证标签字典。


class ConcurrentUpdateError(RuntimeError):  # 表示人工页面版本已经过期。
    """另一位操作员已修改任务，客户端必须刷新后重试。"""  # 提供可映射为 HTTP 409 的领域错误。


@dataclass(frozen=True)  # 使用不可变 DTO 防止 API 层修改已读取任务。
class StoredTask:  # 表示脱离 Session 的补录任务快照。
    id: int  # 保存数据库主键。
    question: QuestionInput  # 保存题目领域对象。
    status: str  # 保存任务状态。
    version: int  # 保存乐观锁版本。
    claimed_by: Optional[str]  # 保存当前领取人。
    claimed_until: Optional[datetime]  # 保存租约到期时间。


@dataclass(frozen=True)  # 使用不可变 DTO 返回提交结果。
class StoredAnnotation:  # 表示人工反馈快照。
    id: int  # 保存标注记录主键。
    question_id: int  # 保存题目主键。
    selected_tags: List[str]  # 保存最终标签。
    added_tags: List[str]  # 保存新增标签。
    removed_tags: List[str]  # 保存移除标签。
    operator_id: str  # 保存提交人。
    created_at: datetime  # 保存提交时间。


@dataclass(frozen=True)  # 冻结队列任务快照，避免 Worker 在事务外修改 ORM。
class StoredPredictionJob:  # 表示 Worker 已领取的预测任务。
    id: str  # 保存预测任务 UUID。
    question_id: int  # 保存待预测题目 ID。
    attempts: int  # 保存当前尝试序号。
    leased_until: datetime  # 保存本次 Worker 租约到期时间。


def _to_task(record: QuestionRecord) -> StoredTask:  # 将 ORM 对象转换为稳定领域 DTO。
    question = QuestionInput(external_id=record.external_id, subject=Subject(record.subject), stem=record.stem, reference_answer=record.reference_answer, analysis=record.analysis, source=record.source, source_uri=record.source_uri, license_name=record.license_name)  # 重建经过校验且保留来源授权的题目对象。
    return StoredTask(id=int(record.id), question=question, status=record.status, version=record.version, claimed_by=record.claimed_by, claimed_until=record.claimed_until)  # 返回脱离 Session 的快照。


def _to_annotation(record: HumanAnnotationRecord) -> StoredAnnotation:  # 将人工 ORM 对象转换为 DTO。
    return StoredAnnotation(id=int(record.id), question_id=int(record.question_id), selected_tags=list(record.selected_tags), added_tags=list(record.added_tags), removed_tags=list(record.removed_tags), operator_id=record.operator_id, created_at=record.created_at)  # 复制 JSON 数组避免外部修改 ORM 状态。


class QuestionRepository:  # 封装题目标注系统的事务边界。
    def __init__(self, session_factory: sessionmaker) -> None:  # 注入 Session 工厂便于生产和测试切换数据库。
        self._session_factory = session_factory  # 保存 Session 工厂。

    def create_question(self, question: QuestionInput, actor_id: str, request_id: str) -> StoredTask:  # 幂等创建待补录题目。
        with self._session_factory() as session:  # 为本次写入创建短生命周期 Session。
            with session.begin():  # 开启原子事务。
                existing = session.execute(select(QuestionRecord).where(QuestionRecord.external_id == question.external_id)).scalar_one_or_none()  # 按业务 ID 检查重复。
                if existing is not None:  # 上游重试时直接返回原任务。
                    return _to_task(existing)  # 保持创建接口幂等。
                record = QuestionRecord(external_id=question.external_id, subject=question.subject.value, stem=question.stem, reference_answer=question.reference_answer, analysis=question.analysis, source=question.source, source_uri=question.source_uri, license_name=question.license_name, status="pending", version=1)  # 创建带来源和许可证的待补录记录。
                session.add(record)  # 将题目加入事务。
                session.flush()  # 获取数据库生成的主键。
                session.add(PredictionJobRecord(id=str(uuid.uuid4()), question_id=record.id, status="pending", attempts=0, available_at=datetime.utcnow(), last_error=""))  # 与题目同事务创建预测任务，避免消息丢失。
                self._add_audit(session, request_id, actor_id, "question.create", "question", str(record.id), {"external_id": question.external_id, "subject": question.subject.value})  # 记录脱敏审计事件。
                return _to_task(record)  # 返回新建任务快照。

    def get_task(self, question_id: int) -> Optional[StoredTask]:  # 按主键读取题目任务。
        with self._session_factory() as session:  # 创建只读 Session。
            record = session.get(QuestionRecord, question_id)  # 使用主键高效查询。
            return _to_task(record) if record is not None else None  # 返回 DTO 或空值。

    def claim_next(self, operator_id: str, request_id: str, lease_seconds: int, subject: Optional[Subject] = None) -> Optional[StoredTask]:  # 领取一个未完成任务并建立租约。
        now = datetime.utcnow()  # 使用 UTC 作为数据库时间基准。
        with self._session_factory() as session:  # 创建事务 Session。
            with session.begin():  # 保证查询与更新任务原子执行。
                conditions = [QuestionRecord.status.in_(["pending", "in_review"]), or_(QuestionRecord.claimed_until.is_(None), QuestionRecord.claimed_until < now, QuestionRecord.claimed_by == operator_id)]  # 只选择未领取、租约过期或本人已领取任务。
                if subject is not None:  # 可选按学科筛选队列。
                    conditions.append(QuestionRecord.subject == subject.value)  # 添加学科条件。
                statement = select(QuestionRecord).where(and_(*conditions)).order_by(QuestionRecord.created_at.asc(), QuestionRecord.id.asc()).limit(1)  # 按最早任务公平领取。
                if session.bind is not None and session.bind.dialect.name == "mysql":  # MySQL 8 支持跳过已锁行，便于多实例并发领取。
                    statement = statement.with_for_update(skip_locked=True)  # 使用行锁避免两人领取同一题。
                record = session.execute(statement).scalar_one_or_none()  # 执行领取查询。
                if record is None:  # 队列为空时返回空值。
                    return None  # 让 API 返回 204。
                record.status = "in_review"  # 标记任务处于人工复核中。
                record.claimed_by = operator_id  # 绑定当前操作员。
                record.claimed_until = now + timedelta(seconds=lease_seconds)  # 设置可自动释放的租约。
                record.version += 1  # 更新乐观锁版本。
                self._add_audit(session, request_id, operator_id, "question.claim", "question", str(record.id), {"claimed_until": record.claimed_until.isoformat()})  # 记录领取事件。
                return _to_task(record)  # 返回领取后的任务快照。

    def save_prediction(self, question_id: int, result: PredictionResult, latency_ms: float, actor_id: str, request_id: str) -> str:  # 保存完整预测及单标签明细。
        prediction_id = str(uuid.uuid4())  # 为本次模型链运行生成 UUID。
        with self._session_factory() as session:  # 创建写入 Session。
            with session.begin():  # 保证运行快照与标签明细同时提交。
                question = session.get(QuestionRecord, question_id)  # 确认题目存在。
                if question is None:  # 防止孤立预测记录。
                    raise KeyError(f"question not found: {question_id}")  # 返回清晰领域错误。
                run = PredictionRunRecord(id=prediction_id, question_id=question_id, model_version=result.model_version, result_json=result.model_dump(mode="json"), latency_ms=latency_ms)  # 创建预测运行快照。
                session.add(run)  # 加入预测运行。
                for suggestion in result.suggestions:  # 将每项标签写入可统计明细表。
                    session.add(LabelSuggestionRecord(prediction_id=prediction_id, question_id=question_id, tag_code=suggestion.code, confidence=suggestion.confidence, selected_by_default=suggestion.selected_by_default, high_risk=suggestion.high_risk, reason=suggestion.reason, source_scores=suggestion.source_scores, evidence_ids=suggestion.evidence_ids))  # 保存建议及证据来源。
                job = session.execute(select(PredictionJobRecord).where(PredictionJobRecord.question_id == question_id)).scalar_one_or_none()  # 查找题目对应预测任务。
                if job is not None:  # 同步预测或 Worker 成功时都完成队列任务。
                    job.status = "completed"  # 标记任务完成。
                    job.leased_by = None  # 清除 Worker 所有者。
                    job.leased_until = None  # 清除任务租约。
                    job.last_error = ""  # 清除旧错误摘要。
                self._add_audit(session, request_id, actor_id, "prediction.create", "question", str(question_id), {"prediction_id": prediction_id, "model_version": result.model_version, "latency_ms": latency_ms})  # 记录模型运行事件。
        return prediction_id  # 返回前端和人工提交使用的预测 ID。

    def claim_prediction_job(self, worker_id: str, lease_seconds: int, max_attempts: int) -> Optional[StoredPredictionJob]:  # 原子领取一个到期预测任务。
        now = datetime.utcnow()  # 使用 UTC 时间判断退避和租约。
        with self._session_factory() as session:  # 创建队列事务 Session。
            with session.begin():  # 保证选择和占用任务原子执行。
                statement = select(PredictionJobRecord).where(PredictionJobRecord.status.in_(["pending", "running"]), PredictionJobRecord.attempts < max_attempts, PredictionJobRecord.available_at <= now, or_(PredictionJobRecord.leased_until.is_(None), PredictionJobRecord.leased_until < now)).order_by(PredictionJobRecord.available_at.asc(), PredictionJobRecord.created_at.asc()).limit(1)  # 选择最早可运行且未耗尽预算的任务。
                if session.bind is not None and session.bind.dialect.name == "mysql":  # MySQL 8 支持跳过其他 Worker 已锁记录。
                    statement = statement.with_for_update(skip_locked=True)  # 防止重复领取。
                job = session.execute(statement).scalar_one_or_none()  # 执行队列查询。
                if job is None:  # 当前没有可用任务。
                    return None  # Worker 可等待后再次轮询。
                job.status = "running"  # 标记任务正在处理。
                job.attempts += 1  # 增加尝试计数。
                job.leased_by = worker_id  # 保存 Worker 身份。
                job.leased_until = now + timedelta(seconds=lease_seconds)  # 设置故障自动释放租约。
                return StoredPredictionJob(id=str(job.id), question_id=int(job.question_id), attempts=int(job.attempts), leased_until=job.leased_until)  # 返回脱离 Session 的任务快照。

    def fail_prediction_job(self, job_id: str, worker_id: str, error: Exception, max_attempts: int, request_id: str) -> None:  # 记录失败并按指数退避重试或终止。
        with self._session_factory() as session:  # 创建写入 Session。
            with session.begin():  # 原子更新任务和审计。
                job = session.get(PredictionJobRecord, job_id)  # 读取队列任务。
                if job is None:  # 防止未知任务更新。
                    raise KeyError(f"prediction job not found: {job_id}")  # 返回清晰错误。
                if job.leased_by != worker_id:  # 只有持有租约的 Worker 能更新任务。
                    raise PermissionError("prediction job lease is owned by another worker")  # 防止过期 Worker 覆盖新结果。
                job.last_error = f"{type(error).__name__}: {str(error)[:400]}"  # 保存限制长度的脱敏错误摘要。
                job.leased_by = None  # 释放 Worker 所有者。
                job.leased_until = None  # 清除租约。
                if job.attempts >= max_attempts:  # 判断是否耗尽重试预算。
                    job.status = "failed"  # 标记最终失败等待人工或运维处理。
                else:  # 仍有重试预算时重新排队。
                    job.status = "pending"  # 恢复待执行状态。
                    job.available_at = datetime.utcnow() + timedelta(seconds=min(300, 2 ** job.attempts))  # 使用有上限指数退避。
                self._add_audit(session, request_id, worker_id, "prediction.fail", "prediction_job", job_id, {"question_id": job.question_id, "attempts": job.attempts, "status": job.status, "error_type": type(error).__name__})  # 记录失败类型但不写完整敏感堆栈。

    def latest_prediction(self, question_id: int) -> Optional[Dict[str, Any]]:  # 读取某题最新预测快照。
        with self._session_factory() as session:  # 创建只读 Session。
            statement = select(PredictionRunRecord).where(PredictionRunRecord.question_id == question_id).order_by(PredictionRunRecord.created_at.desc()).limit(1)  # 按时间倒序查最新运行。
            record = session.execute(statement).scalar_one_or_none()  # 执行查询。
            if record is None:  # 尚未预测时返回空值。
                return None  # 由应用服务决定是否同步生成。
            return {"prediction_id": record.id, "model_version": record.model_version, "result": dict(record.result_json), "latency_ms": record.latency_ms, "created_at": record.created_at.isoformat()}  # 返回可 JSON 化快照。

    def submit_annotation(self, question_id: int, prediction_id: str, selected_tags: Sequence[str], operator_id: str, note: str, idempotency_key: str, expected_version: int, request_id: str) -> StoredAnnotation:  # 幂等保存人工最终标签。
        unique_tags = list(dict.fromkeys(selected_tags))  # 保留顺序并去除重复标签。
        with self._session_factory() as session:  # 创建事务 Session。
            with session.begin():  # 将校验、反馈和状态更新放在同一事务。
                existing = session.execute(select(HumanAnnotationRecord).where(HumanAnnotationRecord.idempotency_key == idempotency_key)).scalar_one_or_none()  # 检查客户端重试。
                if existing is not None:  # 相同幂等键已经成功提交。
                    return _to_annotation(existing)  # 返回原结果而不重复写入。
                question = session.get(QuestionRecord, question_id)  # 读取待提交题目。
                if question is None:  # 题目不存在时中止。
                    raise KeyError(f"question not found: {question_id}")  # 返回 404 可映射错误。
                if question.version != expected_version:  # 检查页面读取后的并发修改。
                    raise ConcurrentUpdateError(f"expected version {expected_version}, current version {question.version}")  # 要求客户端刷新。
                if question.claimed_by not in {None, operator_id}:  # 防止提交他人仍在租约中的任务。
                    raise PermissionError("question is claimed by another operator")  # 返回权限冲突。
                for code in unique_tags:  # 校验所有人工标签。
                    tag = require_known_tag(code)  # 确保标签存在于受控字典。
                    if Subject(question.subject) not in tag.subjects:  # 防止给数学题提交英语专属标签。
                        raise ValueError(f"tag {code} is not valid for subject {question.subject}")  # 返回可修正业务错误。
                prediction = session.get(PredictionRunRecord, prediction_id) if prediction_id else None  # 读取人工看到的预测版本。
                if prediction_id and (prediction is None or int(prediction.question_id) != question_id):  # 防止跨题引用预测。
                    raise ValueError("prediction_id does not belong to this question")  # 拒绝不一致提交。
                defaults = {item.tag_code for item in prediction.suggestions if item.selected_by_default} if prediction is not None else set()  # 获取模型默认标签用于反馈差异。
                final_tags = set(unique_tags)  # 建立人工最终标签集合。
                annotation = HumanAnnotationRecord(question_id=question_id, prediction_id=prediction_id or None, selected_tags=unique_tags, added_tags=sorted(final_tags - defaults), removed_tags=sorted(defaults - final_tags), operator_id=operator_id, note=note, model_version=prediction.model_version if prediction is not None else "", idempotency_key=idempotency_key)  # 创建可训练反馈记录。
                session.add(annotation)  # 将人工标注加入事务。
                question.status = "completed"  # 标记题目补录完成。
                question.claimed_by = None  # 释放领取人。
                question.claimed_until = None  # 清除租约。
                question.version += 1  # 增加版本防止旧页面再次提交。
                session.flush()  # 获取标注记录主键和时间。
                question.latest_annotation_id = annotation.id  # 在同一事务更新最新人工结论指针。
                self._add_audit(session, request_id, operator_id, "annotation.submit", "question", str(question_id), {"annotation_id": annotation.id, "prediction_id": prediction_id, "selected_tags": unique_tags, "added_tags": annotation.added_tags, "removed_tags": annotation.removed_tags})  # 保存反馈审计事件。
                return _to_annotation(annotation)  # 返回提交结果。

    @staticmethod  # 审计写入不依赖仓储实例状态。
    def _add_audit(session: Session, request_id: str, actor_id: str, action: str, resource_type: str, resource_id: str, payload: Dict[str, Any]) -> None:  # 在当前业务事务中追加审计事件。
        session.add(AuditEventRecord(request_id=request_id, actor_id=actor_id, action=action, resource_type=resource_type, resource_id=resource_id, payload=payload))  # 审计与业务写入同成同败。
