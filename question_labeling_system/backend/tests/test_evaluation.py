"""验证多标签评测、人工修改率和逐学科指标。"""  # 使用纯函数避免数据库依赖。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import unittest  # 使用标准库测试框架。

from app.evaluation.evaluate_feedback import EvaluationExample, evaluate  # 导入评测样本和纯计算函数。


class FeedbackEvaluationTests(unittest.TestCase):  # 覆盖模型完全正确、误报和漏报。
    def test_multilabel_metrics_match_human_corrections(self) -> None:  # 验证指标与业务修改语义一致。
        report = evaluate([EvaluationExample(1, "math", "v1", {"calculation_required", "unit_required"}, {"calculation_required", "unit_required"}), EvaluationExample(2, "math", "v1", {"calculation_required", "unit_required"}, {"calculation_required"}), EvaluationExample(3, "english", "v1", {"spelling_sensitive"}, {"spelling_sensitive", "grammar_tense"})])  # 构造一条完全正确、一条误报和一条漏报。
        self.assertEqual(report["examples"], 3)  # 报告应覆盖三条样本。
        self.assertEqual(report["exact_match_rate"], round(1 / 3, 6))  # 仅一条完全匹配。
        self.assertEqual(report["human_change_rate"], round(2 / 3, 6))  # 两条需要人工修改。
        self.assertEqual(report["labels_removed_by_humans"], 1)  # 人工取消一个误报标签。
        self.assertEqual(report["labels_added_by_humans"], 1)  # 人工增加一个漏标标签。
        self.assertGreater(report["micro"]["f1"], 0.7)  # 总体标签 F1 应反映大部分标签正确。
        self.assertIn("english", report["per_subject"])  # 报告必须支持学科切片。

    def test_empty_evaluation_is_rejected(self) -> None:  # 空数据不能被误判为满分。
        with self.assertRaisesRegex(ValueError, "no feedback examples"):  # 期待清晰错误。
            evaluate([])  # 执行空评测。


if __name__ == "__main__":  # 允许直接运行测试。
    unittest.main()  # 启动测试运行器。