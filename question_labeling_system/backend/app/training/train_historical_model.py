"""从 MySQL 或 JSONL 流式训练字符 n-gram 多标签分类器。"""  # 该专用模型为在线 LLM+RAG 图提供低延迟先验。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import argparse  # 解析训练数据、批大小和输出路径。
import hashlib  # 使用稳定哈希划分训练集与留出集。
import json  # 读取 JSONL 并写模型清单。
import os  # 读取默认数据库环境变量。
import tempfile  # 在目标目录创建原子写入临时文件。
from dataclasses import dataclass  # 定义轻量训练样本。
from datetime import datetime, timezone  # 生成 UTC 模型版本。
from pathlib import Path  # 处理输入和工件路径。
from typing import Any, Callable, Dict, Iterable, Iterator, List, Sequence  # 标注流式数据和模型字典。

import numpy as np  # 构造标签数组和评测统计。
from sqlalchemy import text  # 使用参数化 SQL 流式查询最新人工标注。
from sklearn.feature_extraction.text import HashingVectorizer  # 无词表特征器适合百万数据增量训练。
from sklearn.linear_model import SGDClassifier  # 使用支持 partial_fit 的逻辑回归式分类器。

from app.adapters.historical_model import question_to_model_text  # 保证训练和在线推理文本格式一致。
from app.db.session import build_engine  # 复用生产数据库连接配置。
from app.domain.models import QuestionInput, Subject  # 使用同一题目校验模型。
from app.domain.taxonomy import TAG_DEFINITIONS  # 使用受控标签全集训练固定输出头。


@dataclass(frozen=True)  # 训练样本在批处理期间保持不可变。
class TrainingExample:  # 表示一个人工确认的多标签题目。
    external_id: str  # 保存稳定 ID，用于可复现数据划分。
    question: QuestionInput  # 保存题目、答案、解析和来源。
    labels: List[str]  # 保存人工最终标签编码。


def _parse_labels(value: Any) -> List[str]:  # 兼容数据库驱动返回 JSON 数组或字符串。
    if isinstance(value, list):  # SQLAlchemy JSON 字段通常直接返回列表。
        return [str(item) for item in value]  # 统一转换为字符串编码。
    if isinstance(value, str):  # 某些驱动或导出文件返回 JSON 文本。
        parsed = json.loads(value)  # 使用 JSON 解析而不是逗号切分。
        if isinstance(parsed, list):  # 校验 JSON 顶层类型。
            return [str(item) for item in parsed]  # 返回标签编码。
    raise ValueError("selected_tags must be a JSON array")  # 拒绝损坏训练数据。


def iter_jsonl_examples(path: Path, limit: int = 0) -> Iterator[TrainingExample]:  # 逐行读取本地或对象存储下载的训练快照。
    with path.open("r", encoding="utf-8") as stream:  # 以流式方式打开文件，避免一次加载百万行。
        for index, line in enumerate(stream):  # 按行遍历 JSONL。
            if limit > 0 and index >= limit:  # 支持开发环境限制样本数。
                return  # 达到上限后结束生成器。
            if not line.strip():  # 忽略空行。
                continue  # 继续读取下一条。
            payload = json.loads(line)  # 解析单条独立 JSON 对象。
            question = QuestionInput(external_id=str(payload["external_id"]), subject=Subject(str(payload["subject"])), stem=str(payload["stem"]), reference_answer=str(payload["reference_answer"]), analysis=str(payload.get("analysis", "")), source=str(payload.get("source", "training_snapshot")), source_uri=str(payload.get("source_uri", "")), license_name=str(payload.get("license_name", "internal")))  # 使用领域模型校验训练字段与来源授权。
            yield TrainingExample(external_id=question.external_id, question=question, labels=_parse_labels(payload["selected_tags"]))  # 惰性返回训练样本。


def iter_database_examples(database_url: str, batch_size: int, limit: int = 0) -> Iterator[TrainingExample]:  # 从 MySQL 流式读取每题最新人工标注。
    engine = build_engine(database_url)  # 创建带断线检测的数据库连接池。
    sql = text("""SELECT q.external_id, q.subject, q.stem, q.reference_answer, q.analysis, q.source, q.source_uri, q.license_name, ha.selected_tags FROM questions q JOIN human_annotations ha ON ha.id = q.latest_annotation_id WHERE q.status = 'completed' ORDER BY q.id LIMIT :row_limit""")  # 通过最新标注指针流式查询人工标签与来源授权。
    row_limit = limit if limit > 0 else 2_147_483_647  # MySQL 参数化 LIMIT 需要具体上限值。
    try:  # 确保生成器结束后释放 Engine。
        with engine.connect().execution_options(stream_results=True) as connection:  # 启用服务端游标减少内存占用。
            result = connection.execute(sql, {"row_limit": row_limit})  # 执行只读参数化查询。
            while True:  # 分批拉取直到结果耗尽。
                rows = result.fetchmany(batch_size)  # 每次只保留一个训练批次。
                if not rows:  # 没有更多数据时结束。
                    break  # 跳出读取循环。
                for row in rows:  # 转换当前数据库批次。
                    mapping = row._mapping  # 使用 SQLAlchemy 1.4 稳定映射接口。
                    question = QuestionInput(external_id=str(mapping["external_id"]), subject=Subject(str(mapping["subject"])), stem=str(mapping["stem"]), reference_answer=str(mapping["reference_answer"]), analysis=str(mapping["analysis"] or ""), source=str(mapping["source"] or "internal"), source_uri=str(mapping["source_uri"] or ""), license_name=str(mapping["license_name"] or "internal"))  # 校验题目字段与来源授权。
                    yield TrainingExample(external_id=question.external_id, question=question, labels=_parse_labels(mapping["selected_tags"]))  # 惰性返回人工样本。
    finally:  # 无论正常结束或异常都清理连接池。
        engine.dispose()  # 释放数据库连接。


def _is_evaluation_example(external_id: str, percent: int) -> bool:  # 用稳定业务 ID 建立可复现留出集。
    digest = hashlib.sha256(external_id.encode("utf-8")).digest()  # 计算不受 Python 随机种子影响的哈希。
    bucket = int.from_bytes(digest[:4], "big") % 100  # 映射到 0 到 99 的桶。
    return bucket < percent  # 前 percent 个桶作为评测集。


def _batches(values: Iterable[TrainingExample], batch_size: int) -> Iterator[List[TrainingExample]]:  # 将流式样本聚合为有限内存批次。
    batch: List[TrainingExample] = []  # 初始化当前批次。
    for value in values:  # 遍历惰性数据源。
        batch.append(value)  # 加入当前批次。
        if len(batch) >= batch_size:  # 达到配置批大小时输出。
            yield batch  # 将完整批次交给训练循环。
            batch = []  # 创建新列表避免修改已输出批次。
    if batch:  # 输出不足一个批次的尾部样本。
        yield batch  # 保证不丢最后数据。


def _new_vectorizer(n_features: int) -> HashingVectorizer:  # 创建无需保存词表的中英文字符特征器。
    return HashingVectorizer(analyzer="char", ngram_range=(2, 5), n_features=n_features, alternate_sign=False, norm="l2", lowercase=True)  # 字符 n-gram 同时覆盖中文、英文拼写和数学符号。


def _new_classifiers(label_codes: Sequence[str]) -> Dict[str, SGDClassifier]:  # 为每个标签创建独立二分类器。
    return {code: SGDClassifier(loss="log_loss", penalty="l2", alpha=1e-5, random_state=42) for code in label_codes}  # 使用可输出概率且支持增量学习的 SGD。


def train_model(example_factory: Callable[[], Iterable[TrainingExample]], output_path: Path, epochs: int = 2, batch_size: int = 2_048, evaluation_percent: int = 10, n_features: int = 2 ** 18, positive_weight: float = 3.0) -> Dict[str, Any]:  # 执行可复现流式训练并原子保存工件。
    if epochs <= 0 or batch_size <= 0 or n_features <= 0:  # 校验资源相关参数。
        raise ValueError("epochs, batch_size and n_features must be positive")  # 阻止无效训练配置。
    if not 1 <= evaluation_percent <= 40:  # 保留足够训练数据并确保存在留出集。
        raise ValueError("evaluation_percent must be between 1 and 40")  # 报告数据划分错误。
    label_codes = [item.code for item in TAG_DEFINITIONS]  # 固定训练输出标签顺序。
    label_set = set(label_codes)  # 建立未知标签检查集合。
    vectorizer = _new_vectorizer(n_features)  # 创建无状态特征器。
    classifiers = _new_classifiers(label_codes)  # 创建每标签分类器。
    classes = np.array([0, 1], dtype=np.int64)  # 明确二分类全集，允许首批没有正例。
    trained_examples = 0  # 统计实际训练样本数。
    unknown_labels: Dict[str, int] = {}  # 统计训练快照中的未知标签。
    for _epoch in range(epochs):  # 多次流式扫描提高收敛度。
        for batch in _batches((item for item in example_factory() if not _is_evaluation_example(item.external_id, evaluation_percent)), batch_size):  # 只训练非留出样本。
            texts = [question_to_model_text(item.question) for item in batch]  # 转换为训练和推理一致文本。
            matrix = vectorizer.transform(texts)  # 生成稀疏字符 n-gram 矩阵。
            for item in batch:  # 统计无法识别的旧标签以便治理。
                for code in item.labels:  # 遍历人工标签。
                    if code not in label_set:  # 发现已下线或拼写错误标签。
                        unknown_labels[code] = unknown_labels.get(code, 0) + 1  # 累积未知标签次数。
            for code, classifier in classifiers.items():  # 独立更新每个标签头。
                target = np.array([1 if code in item.labels else 0 for item in batch], dtype=np.int64)  # 构造当前标签的二值目标。
                sample_weight = np.where(target == 1, positive_weight, 1.0)  # 提高稀有正例权重缓解标签不平衡。
                classifier.partial_fit(matrix, target, classes=classes, sample_weight=sample_weight)  # 增量训练而不保存全部百万样本。
            trained_examples += len(batch)  # 累积跨 epoch 训练次数。
    if trained_examples == 0:  # 没有训练样本时拒绝生成无效工件。
        raise ValueError("no training examples after evaluation split")  # 提示检查数据源或划分比例。
    metrics = evaluate_model(example_factory, vectorizer, classifiers, label_codes, evaluation_percent)  # 使用固定留出集评估工件。
    version = datetime.now(timezone.utc).strftime("historical-char-ngram-%Y%m%dT%H%M%SZ")  # 生成可排序 UTC 版本。
    bundle = {"vectorizer": vectorizer, "classifiers": classifiers, "label_codes": label_codes, "version": version, "metadata": {"epochs": epochs, "batch_size": batch_size, "evaluation_percent": evaluation_percent, "n_features": n_features, "positive_weight": positive_weight, "trained_examples_with_repeats": trained_examples, "unknown_labels": unknown_labels, "metrics": metrics}}  # 创建自描述模型工件。
    try:  # 将可选序列化依赖限制在保存阶段。
        import joblib  # type: ignore  # sklearn 官方常用 joblib 保存工件。
    except ImportError as exc:  # 捕获部署训练镜像遗漏依赖。
        raise RuntimeError("joblib is required to save the model") from exc  # 返回可操作错误。
    output_path.parent.mkdir(parents=True, exist_ok=True)  # 确保模型目录存在。
    with tempfile.NamedTemporaryFile(dir=output_path.parent, prefix=output_path.name, suffix=".tmp", delete=False) as temporary:  # 在同一文件系统创建临时工件。
        temporary_path = Path(temporary.name)  # 保存临时路径。
    try:  # 确保失败时删除不完整文件。
        joblib.dump(bundle, temporary_path)  # 将完整模型写入临时文件。
        temporary_path.replace(output_path)  # 使用原子 rename 发布新工件。
    finally:  # 清理序列化失败后的临时文件。
        temporary_path.unlink(missing_ok=True)  # Python 3.9 支持 missing_ok，成功 rename 后不会报错。
    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")  # 将人可读指标与二进制工件并排保存。
    manifest_path.write_text(json.dumps({"version": version, **bundle["metadata"]}, ensure_ascii=False, indent=2), encoding="utf-8")  # 写入模型治理清单。
    return {"artifact": str(output_path), "manifest": str(manifest_path), "version": version, **bundle["metadata"]}  # 返回 CLI 可打印训练摘要。


def evaluate_model(example_factory: Callable[[], Iterable[TrainingExample]], vectorizer: HashingVectorizer, classifiers: Dict[str, SGDClassifier], label_codes: Sequence[str], evaluation_percent: int, threshold: float = 0.5) -> Dict[str, float]:  # 计算多标签留出集指标。
    true_positive = false_positive = false_negative = 0  # 初始化微平均计数。
    exact_matches = evaluated = 0  # 初始化集合完全匹配统计。
    per_label_f1: List[float] = []  # 保存每标签 F1 用于宏平均。
    label_counts: Dict[str, List[int]] = {code: [0, 0, 0] for code in label_codes}  # 每个标签保存 TP、FP、FN。
    for batch in _batches((item for item in example_factory() if _is_evaluation_example(item.external_id, evaluation_percent)), 2_048):  # 只扫描固定留出桶。
        matrix = vectorizer.transform([question_to_model_text(item.question) for item in batch])  # 转换评测文本。
        predicted_by_code = {code: classifiers[code].predict_proba(matrix)[:, 1] >= threshold for code in label_codes}  # 批量计算每标签预测。
        for row_index, item in enumerate(batch):  # 逐样本比较标签集合。
            expected = set(item.labels) & set(label_codes)  # 忽略未知历史标签。
            predicted = {code for code in label_codes if bool(predicted_by_code[code][row_index])}  # 构造模型预测集合。
            exact_matches += int(expected == predicted)  # 统计完全匹配。
            evaluated += 1  # 增加评测样本数。
            for code in label_codes:  # 更新每标签混淆计数。
                is_expected = code in expected  # 判断人工标签。
                is_predicted = code in predicted  # 判断模型标签。
                if is_expected and is_predicted:  # 判断真正例。
                    true_positive += 1  # 更新微平均真正例。
                    label_counts[code][0] += 1  # 更新标签真正例。
                elif not is_expected and is_predicted:  # 判断假正例。
                    false_positive += 1  # 更新微平均假正例。
                    label_counts[code][1] += 1  # 更新标签假正例。
                elif is_expected and not is_predicted:  # 判断假负例。
                    false_negative += 1  # 更新微平均假负例。
                    label_counts[code][2] += 1  # 更新标签假负例。
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0  # 计算微平均精确率。
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0  # 计算微平均召回率。
    micro_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0  # 计算微平均 F1。
    for true_pos, false_pos, false_neg in label_counts.values():  # 计算每标签 F1。
        label_precision = true_pos / (true_pos + false_pos) if true_pos + false_pos else 0.0  # 计算标签精确率。
        label_recall = true_pos / (true_pos + false_neg) if true_pos + false_neg else 0.0  # 计算标签召回率。
        per_label_f1.append(2 * label_precision * label_recall / (label_precision + label_recall) if label_precision + label_recall else 0.0)  # 保存标签 F1。
    return {"evaluation_examples": float(evaluated), "micro_precision": round(precision, 6), "micro_recall": round(recall, 6), "micro_f1": round(micro_f1, 6), "macro_f1": round(float(np.mean(per_label_f1)), 6), "exact_match_rate": round(exact_matches / evaluated, 6) if evaluated else 0.0}  # 返回可用于模型门禁的指标。


def parse_args() -> argparse.Namespace:  # 定义训练 CLI。
    parser = argparse.ArgumentParser(description="Train the historical multi-label question model")  # 创建参数解析器。
    source = parser.add_mutually_exclusive_group(required=True)  # 强制选择一个训练数据源。
    source.add_argument("--input-jsonl", help="本地或对象存储下载的人工标签快照")  # 支持可复现离线训练。
    source.add_argument("--database-url", default=os.getenv("DATABASE_URL"), help="MySQL SQLAlchemy URL")  # 支持直接流式读取数据库。
    parser.add_argument("--output", default="../artifacts/label_model.joblib", help="模型工件输出路径")  # 设置默认工件位置。
    parser.add_argument("--epochs", type=int, default=2, help="流式扫描轮数")  # 控制训练深度。
    parser.add_argument("--batch-size", type=int, default=2_048, help="训练和数据库读取批大小")  # 控制峰值内存。
    parser.add_argument("--evaluation-percent", type=int, default=10, help="稳定哈希留出比例")  # 控制评测集比例。
    parser.add_argument("--limit", type=int, default=0, help="开发模式最多读取样本数，0 表示全部")  # 支持快速冒烟。
    return parser.parse_args()  # 返回参数对象。


def main() -> None:  # 执行训练命令。
    args = parse_args()  # 读取 CLI 参数。
    if args.input_jsonl:  # 根据数据源创建可重复扫描工厂。
        input_path = Path(args.input_jsonl).resolve()  # 解析 JSONL 绝对路径。
        factory = lambda: iter_jsonl_examples(input_path, args.limit)  # 每个 epoch 重新打开文件。
    else:  # 使用数据库流式数据源。
        factory = lambda: iter_database_examples(str(args.database_url), args.batch_size, args.limit)  # 每个 epoch 重新执行流式查询。
    summary = train_model(factory, Path(args.output).resolve(), epochs=args.epochs, batch_size=args.batch_size, evaluation_percent=args.evaluation_percent)  # 训练并发布工件。
    print(json.dumps(summary, ensure_ascii=False, indent=2))  # 输出适合 CI 归档的 JSON 摘要。


if __name__ == "__main__":  # 允许 `python -m app.training.train_historical_model` 运行。
    main()  # 启动训练流程。
