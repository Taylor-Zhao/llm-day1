"""从人工反馈计算多标签质量指标并执行发布门禁。"""  # 线上监控修改率，离线评测精确率、召回率和 F1。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import argparse  # 解析数据库、输出和门禁阈值。
import json  # 写入可由 CI 和仪表盘消费的报告。
import os  # 读取默认数据库环境变量。
from dataclasses import dataclass  # 定义脱离 ORM 的评测样本。
from pathlib import Path  # 处理评测报告输出路径。
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Sequence, Set  # 标注流式数据和指标结构。

from sqlalchemy import text  # 执行只读反馈查询。

from app.db.session import build_engine  # 复用数据库连接配置。
from app.domain.taxonomy import TAG_DEFINITIONS  # 使用受控标签全集生成逐标签指标。


@dataclass(frozen=True)  # 冻结样本防止评测中被修改。
class EvaluationExample:  # 表示一次模型预测对应的人工最终结果。
    annotation_id: int  # 保存人工标注 ID 以便追查。
    subject: str  # 保存学科分组。
    model_version: str  # 保存模型链版本。
    predicted_tags: Set[str]  # 保存模型默认选中标签集合。
    actual_tags: Set[str]  # 保存人工最终标签集合。


def _parse_json_list(value: Any) -> Set[str]:  # 兼容 JSON 字段的列表或字符串形态。
    if value is None:  # 空值代表空标签集合。
        return set()  # 返回空集合。
    if isinstance(value, list):  # SQLAlchemy JSON 通常直接返回列表。
        return {str(item) for item in value}  # 转成去重字符串集合。
    if isinstance(value, str):  # 部分驱动返回 JSON 文本。
        parsed = json.loads(value)  # 使用标准 JSON 解析。
        if isinstance(parsed, list):  # 校验顶层必须为数组。
            return {str(item) for item in parsed}  # 返回标签集合。
    raise ValueError("expected a JSON label array")  # 拒绝损坏数据。


def iter_feedback_examples(database_url: str, model_version: str = "", limit: int = 0) -> Iterator[EvaluationExample]:  # 从数据库流式生成评测样本。
    engine = build_engine(database_url)  # 创建带断线检测的 Engine。
    sql = text("""SELECT ha.id AS annotation_id, q.subject, ha.model_version, ha.selected_tags, ls.tag_code, ls.selected_by_default FROM human_annotations ha JOIN questions q ON q.id = ha.question_id LEFT JOIN label_suggestions ls ON ls.prediction_id = ha.prediction_id WHERE (:model_version = '' OR ha.model_version = :model_version) ORDER BY ha.id, ls.id""")  # 查询人工结果和当时模型建议明细。
    current_id: int | None = None  # 保存当前聚合的人工标注 ID。
    current_subject = ""  # 保存当前学科。
    current_version = ""  # 保存当前模型版本。
    current_actual: Set[str] = set()  # 保存当前人工标签。
    current_predicted: Set[str] = set()  # 保存当前默认标签。
    emitted = 0  # 统计已输出样本数。
    try:  # 确保迭代结束后释放 Engine。
        with engine.connect().execution_options(stream_results=True) as connection:  # 使用服务端游标减少内存。
            rows = connection.execute(sql, {"model_version": model_version}).mappings()  # 执行参数化查询。
            for row in rows:  # 按 annotation_id 和 suggestion_id 稳定遍历。
                row_id = int(row["annotation_id"])  # 读取当前人工标注 ID。
                if current_id is not None and row_id != current_id:  # 新标注出现时输出上一组。
                    yield EvaluationExample(current_id, current_subject, current_version, set(current_predicted), set(current_actual))  # 返回完整样本。
                    emitted += 1  # 增加输出计数。
                    if limit > 0 and emitted >= limit:  # 达到开发限制时结束生成器。
                        return  # 停止读取后续数据。
                    current_predicted.clear()  # 清理上一组模型标签。
                if row_id != current_id:  # 初始化新人工标注的公共字段。
                    current_id = row_id  # 保存新 ID。
                    current_subject = str(row["subject"])  # 保存学科。
                    current_version = str(row["model_version"] or "unknown")  # 保存模型版本。
                    current_actual = _parse_json_list(row["selected_tags"])  # 解析人工最终标签。
                if row["tag_code"] is not None and bool(row["selected_by_default"]):  # 只把默认选中建议作为模型预测集合。
                    current_predicted.add(str(row["tag_code"]))  # 保存预测标签。
            if current_id is not None and (limit <= 0 or emitted < limit):  # 输出最后一个聚合样本。
                yield EvaluationExample(current_id, current_subject, current_version, set(current_predicted), set(current_actual))  # 返回尾部样本。
    finally:  # 无论正常完成或异常都清理连接池。
        engine.dispose()  # 释放数据库资源。


def _classification_metrics(true_positive: int, false_positive: int, false_negative: int) -> Dict[str, float]:  # 从混淆计数计算精确率、召回率和 F1。
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0  # 计算精确率。
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0  # 计算召回率。
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0  # 计算调和平均 F1。
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6), "true_positive": float(true_positive), "false_positive": float(false_positive), "false_negative": float(false_negative)}  # 返回 JSON 兼容指标。


def evaluate(examples: Iterable[EvaluationExample]) -> Dict[str, Any]:  # 计算总体、逐标签和逐学科指标。
    label_codes = [item.code for item in TAG_DEFINITIONS]  # 获取固定标签顺序。
    counts: Dict[str, List[int]] = {code: [0, 0, 0] for code in label_codes}  # 每标签保存 TP、FP、FN。
    subject_counts: Dict[str, List[int]] = {}  # 每学科保存 TP、FP、FN。
    micro = [0, 0, 0]  # 保存总体 TP、FP、FN。
    total = exact = changed = added = removed = 0  # 初始化样本级统计。
    versions: Dict[str, int] = {}  # 统计报告涵盖的模型版本。
    for example in examples:  # 流式遍历人工反馈。
        total += 1  # 增加样本数。
        versions[example.model_version] = versions.get(example.model_version, 0) + 1  # 累积版本分布。
        true_positive_tags = example.predicted_tags & example.actual_tags  # 计算正确建议。
        false_positive_tags = example.predicted_tags - example.actual_tags  # 计算人工移除标签。
        false_negative_tags = example.actual_tags - example.predicted_tags  # 计算人工新增标签。
        exact += int(example.predicted_tags == example.actual_tags)  # 统计标签集合完全一致。
        changed += int(bool(false_positive_tags or false_negative_tags))  # 统计需要人工修改的题目。
        added += len(false_negative_tags)  # 统计模型漏标总数。
        removed += len(false_positive_tags)  # 统计模型误报总数。
        micro[0] += len(true_positive_tags)  # 更新总体 TP。
        micro[1] += len(false_positive_tags)  # 更新总体 FP。
        micro[2] += len(false_negative_tags)  # 更新总体 FN。
        subject = subject_counts.setdefault(example.subject, [0, 0, 0])  # 获取学科混淆计数。
        subject[0] += len(true_positive_tags)  # 更新学科 TP。
        subject[1] += len(false_positive_tags)  # 更新学科 FP。
        subject[2] += len(false_negative_tags)  # 更新学科 FN。
        for code in true_positive_tags:  # 更新每标签 TP。
            if code in counts:  # 忽略已退出标签字典的历史编码。
                counts[code][0] += 1  # 增加真正例。
        for code in false_positive_tags:  # 更新每标签 FP。
            if code in counts:  # 忽略已退出标签字典的历史编码。
                counts[code][1] += 1  # 增加假正例。
        for code in false_negative_tags:  # 更新每标签 FN。
            if code in counts:  # 忽略已退出标签字典的历史编码。
                counts[code][2] += 1  # 增加假负例。
    if total == 0:  # 空评测集不能产生有效门禁结论。
        raise ValueError("no feedback examples matched the evaluation query")  # 提醒检查数据与版本筛选。
    per_label = {code: _classification_metrics(*values) for code, values in counts.items()}  # 计算逐标签指标。
    macro_f1 = sum(item["f1"] for item in per_label.values()) / len(per_label)  # 对受控标签做宏平均。
    per_subject = {subject: _classification_metrics(*values) for subject, values in subject_counts.items()}  # 计算逐学科指标。
    return {"examples": total, "versions": versions, "micro": _classification_metrics(*micro), "macro_f1": round(macro_f1, 6), "exact_match_rate": round(exact / total, 6), "human_change_rate": round(changed / total, 6), "labels_added_by_humans": added, "labels_removed_by_humans": removed, "per_subject": per_subject, "per_label": per_label}  # 返回完整报告。


def parse_args() -> argparse.Namespace:  # 定义评测命令参数。
    parser = argparse.ArgumentParser(description="Evaluate model defaults against human annotations")  # 创建参数解析器。
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "sqlite:///../data/question_labeling.db"), help="SQLAlchemy database URL")  # 读取反馈事实库。
    parser.add_argument("--model-version", default="", help="仅评测指定模型链版本，空表示全部")  # 支持候选版本门禁。
    parser.add_argument("--limit", type=int, default=0, help="开发模式样本上限")  # 支持快速检查。
    parser.add_argument("--output", default="../artifacts/evaluation_report.json", help="评测报告路径")  # 设置流水线产物位置。
    parser.add_argument("--min-micro-f1", type=float, default=0.75, help="发布门禁最低 micro F1")  # 设置总体标签质量门禁。
    parser.add_argument("--min-exact-match", type=float, default=0.55, help="发布门禁最低完全匹配率")  # 设置题目级质量门禁。
    parser.add_argument("--max-human-change-rate", type=float, default=0.45, help="发布门禁最高人工修改率")  # 设置业务效率门禁。
    return parser.parse_args()  # 返回参数对象。


def main() -> None:  # 执行评测和发布门禁。
    args = parse_args()  # 读取 CLI 参数。
    report = evaluate(iter_feedback_examples(args.database_url, args.model_version, args.limit))  # 流式计算报告。
    gates = {"micro_f1": report["micro"]["f1"] >= args.min_micro_f1, "exact_match_rate": report["exact_match_rate"] >= args.min_exact_match, "human_change_rate": report["human_change_rate"] <= args.max_human_change_rate}  # 计算三项发布门禁。
    report["gates"] = gates  # 将门禁明细加入报告。
    report["release_decision"] = "GO" if all(gates.values()) else "NO-GO"  # 只有全部通过才允许发布。
    output = Path(args.output).resolve()  # 解析报告绝对路径。
    output.parent.mkdir(parents=True, exist_ok=True)  # 确保工件目录存在。
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")  # 写入结构化评测结果。
    print(json.dumps(report, ensure_ascii=False, indent=2))  # 同时输出到 CI 日志。
    if report["release_decision"] != "GO":  # 门禁失败时返回非零退出码。
        raise SystemExit(2)  # 阻止部署流水线继续发布。


if __name__ == "__main__":  # 允许 `python -m app.evaluation.evaluate_feedback` 运行。
    main()  # 启动评测流程。
