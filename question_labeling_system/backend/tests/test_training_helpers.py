"""验证训练划分、JSONL 读取和 Qdrant Point 映射的确定性。"""  # 不依赖真实模型服务或向量库。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import unittest  # 使用标准库测试框架。
from pathlib import Path  # 定位项目合成数据。

from app.training.index_rag import point_payload, stable_point_id  # 导入索引纯函数。
from app.training.train_historical_model import _is_evaluation_example, iter_jsonl_examples  # 导入训练纯函数和流式解析器。


class TrainingHelperTests(unittest.TestCase):  # 覆盖可复现训练和索引契约。
    def test_hash_split_and_point_id_are_stable(self) -> None:  # 相同业务 ID 必须始终进入同一划分和向量点。
        self.assertEqual(_is_evaluation_example("q-100", 10), _is_evaluation_example("q-100", 10))  # 验证留出划分稳定。
        self.assertEqual(stable_point_id("q-100"), stable_point_id("q-100"))  # 验证 Qdrant upsert ID 稳定。

    def test_sample_jsonl_maps_to_rag_payload(self) -> None:  # 验证训练数据可直接复用为 RAG 索引数据。
        path = Path(__file__).resolve().parents[2] / "data" / "sample_labeled_questions.jsonl"  # 定位项目内原创样本。
        first = next(iter_jsonl_examples(path))  # 流式读取第一条样本。
        payload = point_payload(first)  # 构造向量 payload。
        self.assertEqual(payload["subject"], "chinese")  # 学科字段应正确序列化。
        self.assertIn("answer_not_unique", payload["human_labels"])  # 人工标签必须进入证据 payload。
        self.assertEqual(payload["source"], "project_synthetic_seed")  # 数据来源必须保留。


if __name__ == "__main__":  # 允许直接运行测试。
    unittest.main()  # 启动测试运行器。