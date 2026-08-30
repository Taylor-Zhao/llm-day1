"""将人工最终标签导出为可用于 LoRA/QLoRA 的指令微调 JSONL。"""  # 对接 llm-day1 Day29-Day32 的训练与固定评测流程。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import argparse  # 解析数据库、输出和样本限制。
import hashlib  # 使用稳定哈希划分训练集和评测集。
import json  # 写入标准 JSONL 指令样本。
import os  # 读取数据库环境变量。
from pathlib import Path  # 处理输出目录。
from typing import Any, Dict, Iterator  # 标注流式数据库结果和输出结构。

from sqlalchemy import text  # 执行参数化只读查询。

from app.db.session import build_engine  # 复用数据库连接配置。
from app.domain.models import QuestionInput, Subject  # 使用领域模型校验题目字段。
from app.domain.taxonomy import require_known_tag  # 将标签编码转换为稳定定义。


SYSTEM_INSTRUCTION = "你是中小学填空题标签分类器。只能从标签字典选择，输出 JSON：{labels:[{code,reason}],warnings:[]}。"  # 定义训练与推理一致的系统任务。


def _labels(value: Any) -> list[str]:  # 兼容 JSON 列表或文本。
    if isinstance(value, list):  # 数据库 JSON 字段通常返回列表。
        return [str(item) for item in value]  # 统一转换编码。
    if isinstance(value, str):  # 某些驱动返回 JSON 文本。
        parsed = json.loads(value)  # 使用标准解析器读取。
        if isinstance(parsed, list):  # 校验顶层数组。
            return [str(item) for item in parsed]  # 返回标签编码。
    raise ValueError("selected_tags must be a JSON array")  # 拒绝损坏反馈。


def iter_sft_rows(database_url: str, limit: int = 0) -> Iterator[Dict[str, Any]]:  # 流式构造指令微调样本。
    engine = build_engine(database_url)  # 创建数据库 Engine。
    row_limit = limit if limit > 0 else 2_147_483_647  # 为参数化 LIMIT 提供具体值。
    sql = text("""SELECT q.external_id, q.subject, q.stem, q.reference_answer, q.analysis, q.source, q.source_uri, q.license_name, ha.selected_tags FROM questions q JOIN human_annotations ha ON ha.id = q.latest_annotation_id WHERE q.status = 'completed' ORDER BY q.id LIMIT :row_limit""")  # 通过最新标注指针读取最终结论并保留来源授权。
    try:  # 确保生成器退出时释放连接。
        with engine.connect().execution_options(stream_results=True) as connection:  # 使用服务端游标。
            rows = connection.execute(sql, {"row_limit": row_limit}).mappings()  # 执行只读查询。
            for row in rows:  # 逐题转换避免百万数据占满内存。
                question = QuestionInput(external_id=str(row["external_id"]), subject=Subject(str(row["subject"])), stem=str(row["stem"]), reference_answer=str(row["reference_answer"]), analysis=str(row["analysis"] or ""), source=str(row["source"] or "internal"), source_uri=str(row["source_uri"] or ""), license_name=str(row["license_name"] or "internal"))  # 校验题目结构与数据许可。
                selected = _labels(row["selected_tags"])  # 读取人工最终标签。
                labels = [{"code": code, "reason": require_known_tag(code).description} for code in selected]  # 用稳定标签定义生成监督输出。
                user_text = f"学科：{question.subject.value}\n题干：{question.stem}\n参考答案：{question.reference_answer}\n解析：{question.analysis or '无'}"  # 构造与线上 Prompt 对齐的用户消息。
                yield {"id": question.external_id, "messages": [{"role": "system", "content": SYSTEM_INSTRUCTION}, {"role": "user", "content": user_text}, {"role": "assistant", "content": json.dumps({"labels": labels, "warnings": []}, ensure_ascii=False)}], "metadata": {"subject": question.subject.value, "source": question.source, "source_uri": question.source_uri, "license_name": question.license_name}}  # 返回带来源授权的 OpenAI 风格消息数据。
    finally:  # 无论完成或异常都释放连接池。
        engine.dispose()  # 关闭数据库 Engine。


def is_eval_row(external_id: str, evaluation_percent: int) -> bool:  # 使用稳定 ID 划分固定评测集。
    digest = hashlib.sha256(external_id.encode("utf-8")).digest()  # 计算确定性哈希。
    return int.from_bytes(digest[:4], "big") % 100 < evaluation_percent  # 将固定桶划为评测集。


def export_dataset(rows: Iterator[Dict[str, Any]], train_path: Path, eval_path: Path, evaluation_percent: int) -> Dict[str, int]:  # 将流式样本写到训练与评测 JSONL。
    if not 1 <= evaluation_percent <= 40:  # 校验留出比例。
        raise ValueError("evaluation_percent must be between 1 and 40")  # 防止评测为空或训练数据过少。
    train_path.parent.mkdir(parents=True, exist_ok=True)  # 创建训练文件目录。
    eval_path.parent.mkdir(parents=True, exist_ok=True)  # 创建评测文件目录。
    counts = {"train": 0, "eval": 0}  # 初始化输出计数。
    with train_path.open("w", encoding="utf-8") as train_stream, eval_path.open("w", encoding="utf-8") as eval_stream:  # 同时打开两个输出文件。
        for row in rows:  # 逐条写入避免聚合百万样本。
            split = "eval" if is_eval_row(str(row["id"]), evaluation_percent) else "train"  # 根据稳定哈希选择目标文件。
            target = eval_stream if split == "eval" else train_stream  # 获取输出流。
            target.write(json.dumps(row, ensure_ascii=False) + "\n")  # 写入单行 JSON。
            counts[split] += 1  # 增加分区计数。
    if counts["train"] == 0 or counts["eval"] == 0:  # 两个分区都必须存在样本。
        raise ValueError("train and eval splits must both contain examples")  # 阻止发布不可评测数据集。
    return counts  # 返回导出摘要。


def parse_args() -> argparse.Namespace:  # 定义导出命令参数。
    parser = argparse.ArgumentParser(description="Export reviewed questions as SFT JSONL")  # 创建参数解析器。
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "sqlite:///../data/question_labeling.db"), help="SQLAlchemy database URL")  # 读取人工事实库。
    parser.add_argument("--train-output", default="../artifacts/sft_train.jsonl", help="训练集输出")  # 设置训练文件。
    parser.add_argument("--eval-output", default="../artifacts/sft_eval.jsonl", help="固定评测集输出")  # 设置评测文件。
    parser.add_argument("--evaluation-percent", type=int, default=10, help="稳定哈希评测比例")  # 设置留出比例。
    parser.add_argument("--limit", type=int, default=0, help="开发模式样本上限")  # 支持小规模验证。
    return parser.parse_args()  # 返回参数对象。


def main() -> None:  # 执行 SFT 数据导出。
    args = parse_args()  # 读取 CLI 参数。
    rows = iter_sft_rows(args.database_url, args.limit)  # 创建流式样本迭代器。
    counts = export_dataset(rows, Path(args.train_output).resolve(), Path(args.eval_output).resolve(), args.evaluation_percent)  # 写入固定数据分区。
    print(json.dumps(counts, ensure_ascii=False, indent=2))  # 输出流水线摘要。


if __name__ == "__main__":  # 允许模块命令直接执行。
    main()  # 启动导出流程。