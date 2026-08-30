"""验证数据库任务租约、预测快照、幂等提交和乐观锁。"""  # 使用内存 SQLite 快速验证事务语义。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import unittest  # 使用标准库测试运行器。

from app.db.models import Base, QuestionRecord  # 导入 ORM 元数据和题目表验证最新标注指针。
from app.db.repositories import ConcurrentUpdateError, QuestionRepository  # 导入仓储与并发错误。
from app.db.session import build_engine, build_session_factory  # 导入数据库构建函数。
from app.domain.models import LabelSuggestion, PredictionResult, QuestionInput, Subject  # 导入测试数据结构。


class QuestionRepositoryTests(unittest.TestCase):  # 覆盖补录数据的关键一致性要求。
    def setUp(self) -> None:  # 为每条测试创建独立内存数据库。
        self.engine = build_engine("sqlite://")  # 使用 StaticPool 保持同一个内存库。
        Base.metadata.create_all(self.engine)  # 创建全部表结构。
        self.session_factory = build_session_factory(self.engine)  # 保存 Session 工厂用于直接验证持久化状态。
        self.repository = QuestionRepository(self.session_factory)  # 创建待测试仓储。

    def tearDown(self) -> None:  # 测试后释放数据库资源。
        self.engine.dispose()  # 关闭连接池。

    def _question(self) -> QuestionInput:  # 构造可复用语文测试题。
        return QuestionInput(external_id="q-1", subject=Subject.CHINESE, stem="写一个表示春天的词：____。", reference_answer="合理即可", source="test")  # 返回合法题目。

    def _prediction(self) -> PredictionResult:  # 构造可提交的模型预测。
        suggestion = LabelSuggestion(code="answer_not_unique", name="答案不唯一", confidence=0.9, selected_by_default=True, high_risk=True, reason="测试", source_scores={"model": 0.9}, evidence_ids=[])  # 创建默认选中标签。
        return PredictionResult(question=self._question(), suggestions=[suggestion], evidence=[], model_version="test-v1")  # 返回预测结果。

    def test_create_is_idempotent_and_claim_uses_lease(self) -> None:  # 验证上游重试不会重复题目且领取会增加版本。
        first = self.repository.create_question(self._question(), "importer", "req-1")  # 首次创建题目。
        second = self.repository.create_question(self._question(), "importer", "req-2")  # 使用相同 external_id 重试。
        claimed = self.repository.claim_next("alice", "req-3", 900, Subject.CHINESE)  # 领取待办任务。
        self.assertEqual(first.id, second.id)  # 两次创建必须返回同一任务。
        self.assertIsNotNone(claimed)  # 队列应存在任务。
        assert claimed is not None  # 帮助类型检查器理解后续访问。
        self.assertEqual(claimed.claimed_by, "alice")  # 任务应绑定领取人。
        self.assertEqual(claimed.version, first.version + 1)  # 领取后版本必须增加。

    def test_prediction_and_annotation_are_auditable_and_idempotent(self) -> None:  # 验证模型建议可以转成人工训练反馈。
        task = self.repository.create_question(self._question(), "importer", "req-1")  # 创建待办题目。
        claimed = self.repository.claim_next("alice", "req-2", 900)  # 领取任务以获得当前版本。
        assert claimed is not None  # 确保测试前置条件成立。
        prediction_id = self.repository.save_prediction(task.id, self._prediction(), 12.5, "model-worker", "req-3")  # 保存模型预测快照。
        annotation = self.repository.submit_annotation(task.id, prediction_id, [], "alice", "模型误报", "idem-1", claimed.version, "req-4")  # 人工取消默认标签。
        retry = self.repository.submit_annotation(task.id, prediction_id, [], "alice", "模型误报", "idem-1", claimed.version, "req-5")  # 模拟网络重试。
        self.assertEqual(annotation.id, retry.id)  # 相同幂等键不能产生新记录。
        self.assertEqual(annotation.removed_tags, ["answer_not_unique"])  # 人工差异应作为训练反馈保存。
        self.assertEqual(self.repository.get_task(task.id).status, "completed")  # 提交后任务必须完成。
        with self.session_factory() as session:  # 直接读取题目行验证百万数据查询指针。
            record = session.get(QuestionRecord, task.id)  # 按主键读取题目。
            self.assertEqual(record.latest_annotation_id, annotation.id)  # 最新指针必须原子指向本次人工提交。

    def test_stale_version_is_rejected(self) -> None:  # 验证旧页面无法覆盖新版本。
        task = self.repository.create_question(self._question(), "importer", "req-1")  # 创建题目。
        claimed = self.repository.claim_next("alice", "req-2", 900)  # 领取并增加版本。
        assert claimed is not None  # 确保领取成功。
        prediction_id = self.repository.save_prediction(task.id, self._prediction(), 10.0, "worker", "req-3")  # 保存预测。
        with self.assertRaises(ConcurrentUpdateError):  # 期待版本冲突。
            self.repository.submit_annotation(task.id, prediction_id, ["answer_not_unique"], "alice", "", "idem-stale", task.version, "req-4")  # 使用领取前旧版本提交。

    def test_prediction_job_is_leased_and_completed_by_saved_prediction(self) -> None:  # 验证数据库队列领取和完成状态协作。
        task = self.repository.create_question(self._question(), "importer", "req-job-1")  # 创建题目和同事务预测任务。
        job = self.repository.claim_prediction_job("worker-a", 120, 3)  # 领取预测任务。
        self.assertIsNotNone(job)  # 新题必须产生可领取任务。
        assert job is not None  # 帮助类型检查器理解后续访问。
        self.assertEqual(job.question_id, task.id)  # 任务必须关联正确题目。
        self.repository.save_prediction(task.id, self._prediction(), 8.0, "worker-a", "req-job-2")  # 保存结果并完成任务。
        self.assertIsNone(self.repository.claim_prediction_job("worker-b", 120, 3))  # 已完成任务不能被其他 Worker 再次领取。


if __name__ == "__main__":  # 允许直接执行测试文件。
    unittest.main()  # 启动标准测试运行器。