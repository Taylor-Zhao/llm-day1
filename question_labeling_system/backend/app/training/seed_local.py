"""将原创合成题目导入本地数据库并生成预标注建议。"""  # 仅用于开发学习，不作为生产训练数据。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import json  # 输出脚本执行摘要。
from pathlib import Path  # 定位项目内 JSONL 文件。

from app.bootstrap import build_container  # 复用 API 和 Worker 的依赖装配。
from app.core.config import Settings  # 读取本地数据库与模型模式。
from app.training.train_historical_model import iter_jsonl_examples  # 复用严格训练数据解析器。


def main() -> None:  # 执行幂等本地数据导入。
    settings = Settings.from_env()  # 读取并验证当前环境。
    if settings.environment == "production":  # 禁止把演示样本误写入生产。
        raise RuntimeError("seed_local cannot run in production")  # 使用明确门禁保护线上数据。
    container = build_container(settings)  # 创建本地数据库、RAG 和标注图。
    data_path = Path(__file__).resolve().parents[3] / "data" / "sample_labeled_questions.jsonl"  # 定位项目原创样本。
    imported = 0  # 统计处理题目数。
    predicted = 0  # 统计生成预测数。
    for example in iter_jsonl_examples(data_path):  # 流式读取每条题目。
        bundle = container.service.create_question(example.question, "local-seeder", f"seed-{example.external_id}", predict_now=True)  # 幂等创建并同步预标注。
        imported += 1  # 增加导入计数。
        predicted += int(bool(bundle.prediction_id))  # 增加有效预测计数。
    print(json.dumps({"database_url": settings.database_url, "imported": imported, "predicted": predicted}, ensure_ascii=False, indent=2))  # 输出适合终端查看的 JSON 摘要。


if __name__ == "__main__":  # 允许 `python -m app.training.seed_local` 运行。
    main()  # 启动本地种子导入。
