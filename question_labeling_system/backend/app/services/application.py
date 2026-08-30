"""连接事务仓储与 LangGraph 的用例服务。"""  # 路由只做协议转换，核心用例集中在这里。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import time  # 记录模型链端到端延迟。
import uuid  # 为后台 Worker 的每次处理生成请求追踪 ID。
from dataclasses import dataclass  # 使用 DTO 返回任务和预测组合。
from typing import Any, Dict, Optional, Sequence  # 声明用例输入输出。

from app.core.metrics import ServiceMetrics  # 导入生产指标收集器。
from app.db.repositories import QuestionRepository, StoredAnnotation, StoredTask  # 导入事务仓储 DTO。
from app.domain.models import PredictionResult, QuestionInput, Subject  # 导入领域结构。
from app.services.labeling_workflow import QuestionLabelingWorkflow  # 导入核心 LangGraph 工作流。


@dataclass(frozen=True)  # 冻结任务视图避免路由意外修改。
class TaskBundle:  # 表示补录页面一次需要的完整数据。
    task: StoredTask  # 保存题目、状态、版本与租约。
    prediction_id: str  # 保存人工正在核对的预测 ID。
    prediction: PredictionResult  # 保存建议标签、证据和警告。


class LabelingApplicationService:  # 实现创建、领取、预测和人工提交用例。
    def __init__(self, repository: QuestionRepository, workflow: QuestionLabelingWorkflow, claim_lease_seconds: int, metrics: Optional[ServiceMetrics] = None) -> None:  # 注入仓储、图、租约策略和可选指标。
        self._repository = repository  # 保存事务仓储。
        self._workflow = workflow  # 保存编译后的 LangGraph 工作流。
        self._claim_lease_seconds = claim_lease_seconds  # 保存任务租约时长。
        self._metrics = metrics  # 测试可省略指标，生产装配时必须提供。

    def create_question(self, question: QuestionInput, actor_id: str, request_id: str, predict_now: bool = True) -> TaskBundle:  # 幂等导入题目并默认生成模型建议。
        task = self._repository.create_question(question, actor_id, request_id)  # 先持久化题目事实。
        if predict_now:  # 小批实时导入可以同步预测。
            return self.predict_task(task.id, actor_id, request_id)  # 执行并保存模型链结果。
        prediction = self._repository.latest_prediction(task.id)  # 异步模式可能已有 worker 结果。
        if prediction is None:  # 未预测时不能构造完整补录页面。
            placeholder = PredictionResult(question=task.question, suggestions=[], evidence=[], requires_human_review=True, model_version="pending", warnings=["预测任务等待后台处理"])  # 返回明确等待状态。
            return TaskBundle(task=task, prediction_id="", prediction=placeholder)  # 返回无预测 ID 的任务。
        return self._bundle_from_snapshot(task, prediction)  # 返回已有预测。

    def predict_task(self, question_id: int, actor_id: str, request_id: str, force: bool = False) -> TaskBundle:  # 为指定题目生成或复用预测。
        task = self._repository.get_task(question_id)  # 读取题目快照。
        if task is None:  # 题目不存在时中止。
            raise KeyError(f"question not found: {question_id}")  # 交由 API 映射为 404。
        existing = self._repository.latest_prediction(question_id)  # 查询最近预测。
        if existing is not None and not force:  # 默认复用预测，避免重复模型费用。
            return self._bundle_from_snapshot(task, existing)  # 返回已保存快照。
        started = time.perf_counter()  # 开始记录端到端耗时。
        try:  # 将失败预测计入可观测指标后继续传播异常。
            result = self._workflow.predict(task.question)  # 执行规则、RAG、监督模型、LLM 和融合图。
        except Exception:  # 捕获所有导致无可用结果的异常。
            if self._metrics is not None:  # 生产装配存在指标收集器。
                self._metrics.observe_prediction_failure()  # 增加失败计数。
            raise  # 保留原异常类型供队列或 API 错误策略处理。
        latency_seconds = time.perf_counter() - started  # 计算秒级耗时供 Prometheus Histogram 使用。
        latency_ms = latency_seconds * 1_000.0  # 转换为毫秒供数据库运行快照使用。
        if self._metrics is not None:  # 生产装配存在指标收集器。
            self._metrics.observe_prediction(latency_seconds, result.warnings)  # 记录成功、延迟和降级组件。
        prediction_id = self._repository.save_prediction(question_id, result, latency_ms, actor_id, request_id)  # 原子保存运行快照和标签明细。
        return TaskBundle(task=task, prediction_id=prediction_id, prediction=result)  # 返回页面完整数据。

    def claim_next(self, operator_id: str, request_id: str, subject: Optional[Subject]) -> Optional[TaskBundle]:  # 领取下一题并确保有预测建议。
        task = self._repository.claim_next(operator_id, request_id, self._claim_lease_seconds, subject)  # 使用数据库租约领取任务。
        if task is None:  # 队列为空时返回空值。
            return None  # API 将映射为 204。
        snapshot = self._repository.latest_prediction(task.id)  # 优先读取后台已生成预测。
        if snapshot is not None:  # 已有预测时避免重复推理。
            return self._bundle_from_snapshot(task, snapshot)  # 返回领取任务和预测。
        return self.predict_task(task.id, "model-worker", request_id)  # 后台尚未完成时同步兜底预测。

    def get_task(self, question_id: int) -> TaskBundle:  # 获取指定题目的页面快照。
        task = self._repository.get_task(question_id)  # 读取任务。
        if task is None:  # 题目不存在时中止。
            raise KeyError(f"question not found: {question_id}")  # 映射为 404。
        snapshot = self._repository.latest_prediction(question_id)  # 获取最新预测。
        if snapshot is None:  # 没有预测时返回明确错误，避免页面误认为全不选。
            raise LookupError(f"prediction not ready for question: {question_id}")  # 映射为 409 等待状态。
        return self._bundle_from_snapshot(task, snapshot)  # 返回完整快照。

    def submit(self, question_id: int, prediction_id: str, selected_tags: Sequence[str], operator_id: str, note: str, idempotency_key: str, expected_version: int, request_id: str) -> StoredAnnotation:  # 保存人工确认并产生再训练反馈。
        result = self._repository.submit_annotation(question_id, prediction_id, selected_tags, operator_id, note, idempotency_key, expected_version, request_id)  # 将事务语义委托给仓储。
        if self._metrics is not None:  # 生产装配存在指标收集器。
            self._metrics.observe_annotation(len(result.added_tags), len(result.removed_tags))  # 记录人工修改反馈。
        return result  # 返回已持久化标注。

    def process_next_prediction_job(self, worker_id: str, lease_seconds: int = 120, max_attempts: int = 3) -> bool:  # 领取并处理一个异步预测任务。
        job = self._repository.claim_prediction_job(worker_id, lease_seconds, max_attempts)  # 使用数据库锁和租约领取任务。
        if job is None:  # 当前队列没有可执行任务。
            return False  # 告诉 Worker 可以等待或退出一次性模式。
        request_id = str(uuid.uuid4())  # 为模型运行和审计生成独立追踪 ID。
        try:  # 执行完整 LangGraph 预测并由仓储标记任务完成。
            self.predict_task(job.question_id, worker_id, request_id, force=True)  # 强制生成新预测，避免旧快照让新 Job 空转。
        except Exception as exc:  # 捕获单个任务失败以保持 Worker 进程继续服务。
            self._repository.fail_prediction_job(job.id, worker_id, exc, max_attempts, request_id)  # 记录错误并按预算退避或终止。
            return True  # 表示本轮确实消费了一个任务。
        return True  # 表示任务成功完成。

    @staticmethod  # 快照转换不依赖实例字段。
    def _bundle_from_snapshot(task: StoredTask, snapshot: Dict[str, Any]) -> TaskBundle:  # 将 JSON 快照恢复为强类型结果。
        prediction = PredictionResult.model_validate(snapshot["result"])  # 重新校验数据库 JSON，及时发现版本不兼容。
        return TaskBundle(task=task, prediction_id=str(snapshot["prediction_id"]), prediction=prediction)  # 返回组合 DTO。
