"""验证 Dense/Sparse RRF 融合不会直接混加不同分数空间。"""  # 使用固定排名测试混合召回。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import unittest  # 使用标准库测试框架。
from typing import Sequence  # 标注测试后端返回类型。

from app.adapters.rag import ReciprocalRankFusionRetriever, SearchHit  # 导入融合实现和命中结构。
from app.domain.models import QuestionInput, Subject  # 导入题目结构。


def _hit(question_id: str, raw_score: float) -> SearchHit:  # 构造固定历史题命中。
    return SearchHit(question_id=question_id, subject=Subject.MATH, stem=f"题目 {question_id}", reference_answer="42", human_labels=["calculation_required"], raw_score=raw_score, source="fixture")  # 返回测试命中。


class FakeBackend:  # 按预设排名返回命中。
    def __init__(self, hits: Sequence[SearchHit]) -> None:  # 注入结果列表。
        self._hits = list(hits)  # 复制输入确保稳定。

    def search(self, question: QuestionInput, limit: int) -> Sequence[SearchHit]:  # 满足 SearchBackend 协议。
        del question  # 测试不依赖查询内容。
        return self._hits[:limit]  # 按预设顺序返回。


class ReciprocalRankFusionTests(unittest.TestCase):  # 覆盖双路共识与去重行为。
    def test_document_present_in_both_routes_ranks_first(self) -> None:  # 两路都命中的文档应优先。
        sparse = FakeBackend([_hit("shared", 100.0), _hit("sparse-only", 99.0)])  # Sparse 原始分数故意与 Dense 不同尺度。
        dense = FakeBackend([_hit("dense-only", 0.99), _hit("shared", 0.40)])  # Dense 将 shared 排第二。
        retriever = ReciprocalRankFusionRetriever(sparse, dense)  # 创建真实 RRF 融合器。
        question = QuestionInput(external_id="new", subject=Subject.MATH, stem="计算题", reference_answer="42")  # 构造数学题。
        result = retriever.retrieve(question, 3)  # 执行融合召回。
        self.assertEqual(result[0].question_id, "shared")  # 双路共识应排名第一。
        self.assertEqual(len({item.question_id for item in result}), len(result))  # 同一题只能出现一次。
        self.assertIn("dense+sparse", result[0].source)  # 证据来源应保留两条路由。


if __name__ == "__main__":  # 允许直接运行测试。
    unittest.main()  # 启动测试运行器。