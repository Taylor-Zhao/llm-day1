"""验证 LangGraph 标注主链在无数据库、无网络环境下的核心行为。"""  # 测试业务契约而非模型供应商。

from __future__ import annotations  # 兼容 Python 3.9 的类型注解。

import unittest  # 使用标准库测试框架减少环境依赖。
from typing import Mapping, Sequence  # 为测试替身补充清晰接口。

from app.domain.models import QuestionInput, ReasonedLabel, ReasonerOutput, RetrievedEvidence, Subject, TagDefinition  # 导入领域对象。
from app.services.labeling_workflow import QuestionLabelingWorkflow  # 导入待验证的核心工作流。


class FakeHistoricalModel:  # 模拟由百万人工样本训练出的多标签模型。
    version = "fixture-2026-08"  # 提供固定模型版本供断言。

    def predict_scores(self, question: QuestionInput) -> Mapping[str, float]:  # 返回可预测的标签分数。
        del question  # 明确该测试替身不依赖输入内容。
        return {"answer_not_unique": 0.91, "semantic_equivalence": 0.82, "unknown_tag": 1.0}  # 同时验证未知标签会被过滤。


class FakeRetriever:  # 模拟 Dense + Sparse + RRF 检索结果。
    def retrieve(self, question: QuestionInput, limit: int) -> Sequence[RetrievedEvidence]:  # 返回人工确认过的相似题。
        del question  # 明确测试替身不依赖题目内容。
        del limit  # 明确测试替身不依赖召回上限。
        return [RetrievedEvidence(evidence_id="ev-1", question_id="old-1", subject=Subject.CHINESE, stem="写出一个描写春天的词语。", reference_answer="春暖花开，其他合理答案也可。", human_labels=["answer_not_unique", "semantic_equivalence"], score=0.93, source="internal_question_bank")]  # 构造高质量人工证据。


class FakeReasoner:  # 模拟 LangChain 结构化 LLM 输出。
    version = "fake-llm-prompt-v1"  # 提供固定推理器版本。

    def reason(self, question: QuestionInput, tags: Sequence[TagDefinition], evidence: Sequence[RetrievedEvidence]) -> ReasonerOutput:  # 返回带证据解释的判断。
        del question  # 明确替身不读取题目。
        del tags  # 明确替身不读取标签定义。
        del evidence  # 明确替身不读取证据内容。
        return ReasonerOutput(labels=[ReasonedLabel(code="answer_not_unique", confidence=0.95, reason="参考答案明确说明合理答案均可。", evidence_ids=["ev-1"])])  # 构造结构化输出。


class LabelingWorkflowTests(unittest.TestCase):  # 覆盖主业务路径与降级路径。
    def test_model_rag_and_llm_select_non_unique_answer_for_review(self) -> None:  # 验证多信号能默认勾选答案不唯一。
        workflow = QuestionLabelingWorkflow(FakeHistoricalModel(), FakeRetriever(), FakeReasoner())  # 创建完全离线的真实 LangGraph 工作流。
        question = QuestionInput(external_id="new-1", subject=Subject.CHINESE, stem="请写出一个描写春天的成语：____。", reference_answer="春暖花开，合理答案均可。")  # 构造新补录题目。
        result = workflow.predict(question)  # 执行完整预测图。
        suggestions = {item.code: item for item in result.suggestions}  # 建立便于断言的编码映射。
        self.assertTrue(suggestions["answer_not_unique"].selected_by_default)  # 模型建议应默认勾选。
        self.assertIn("ev-1", suggestions["answer_not_unique"].evidence_ids)  # 建议必须能追溯到 RAG 证据。
        self.assertNotIn("unknown_tag", suggestions)  # 未注册标签不能进入业务页面。
        self.assertTrue(result.requires_human_review)  # 预测不能绕过人工提交。
        self.assertEqual(result.evidence[0].source, "internal_question_bank")  # 证据来源必须保留。

    def test_rag_and_llm_failure_degrades_to_rules_and_historical_model(self) -> None:  # 验证外部依赖故障时仍能补录。
        class BrokenRetriever:  # 定义故障检索器。
            def retrieve(self, question: QuestionInput, limit: int) -> Sequence[RetrievedEvidence]:  # 满足检索协议。
                del question, limit  # 明确未使用参数。
                raise TimeoutError("vector store unavailable")  # 模拟基础设施超时。

        class BrokenReasoner:  # 定义故障 LLM 推理器。
            version = "broken"  # 提供审计版本。

            def reason(self, question: QuestionInput, tags: Sequence[TagDefinition], evidence: Sequence[RetrievedEvidence]) -> ReasonerOutput:  # 满足推理协议。
                del question, tags, evidence  # 明确未使用参数。
                raise TimeoutError("llm unavailable")  # 模拟模型服务超时。

        workflow = QuestionLabelingWorkflow(FakeHistoricalModel(), BrokenRetriever(), BrokenReasoner())  # 创建带故障依赖的图。
        question = QuestionInput(external_id="new-2", subject=Subject.MATH, stem="计算 2+3=____。", reference_answer="5")  # 构造可由规则处理的数学题。
        result = workflow.predict(question)  # 执行降级路径。
        suggestions = {item.code: item for item in result.suggestions}  # 建立结果映射。
        self.assertTrue(suggestions["calculation_required"].selected_by_default)  # 规则标签仍应默认勾选。
        self.assertEqual(len(result.warnings), 2)  # 两个外部依赖故障都必须对人工可见。


if __name__ == "__main__":  # 允许直接执行当前测试文件。
    unittest.main()  # 启动 unittest 测试运行器。