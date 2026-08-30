"""加载百万级人工标签训练的专用模型，并提供本地冷启动实现。"""  # 在线工作流只依赖统一 predict_scores 接口。

from __future__ import annotations  # 延迟类型求值以兼容 Python 3.9。

from pathlib import Path  # 处理模型工件路径。
from typing import Any, Dict  # 为工件和分数字典提供类型。

from app.domain.models import QuestionInput  # 导入标准题目结构。
from app.services.labeling_workflow import rule_based_scores  # 本地冷启动复用可解释规则。


def question_to_model_text(question: QuestionInput) -> str:  # 将结构化题目转换为训练与推理一致的文本。
    return "\n".join([f"学科：{question.subject.value}", f"题干：{question.stem}", f"答案：{question.reference_answer}", f"解析：{question.analysis}"])  # 保留字段边界减少模型混淆。


class BootstrapHistoricalModel:  # 提供首次部署和本地学习使用的低风险冷启动模型。
    version = "bootstrap-rules-v1"  # 明确标记非百万数据模型，防止线上误认。

    def predict_scores(self, question: QuestionInput) -> Dict[str, float]:  # 返回规则分数作为临时监督信号。
        return rule_based_scores(question)  # 复用可解释规则并由生产配置门禁禁止上线。


class SklearnHistoricalLabelModel:  # 加载字符 n-gram 增量多标签模型工件。
    def __init__(self, artifact_path: str) -> None:  # 在进程启动时加载一次工件。
        path = Path(artifact_path)  # 标准化模型路径。
        if not path.exists():  # 缺失工件时立即失败而不是静默降级。
            raise FileNotFoundError(f"historical model artifact not found: {path}")  # 给出可操作错误。
        try:  # 将可选训练依赖限制在模型适配器内部。
            import joblib  # type: ignore  # joblib 用于保存 sklearn 管道。
        except ImportError as exc:  # 捕获部署镜像遗漏依赖。
            raise RuntimeError("joblib is required for artifact model mode") from exc  # 提示安装生产依赖。
        bundle: Any = joblib.load(path)  # 从可信内部工件仓库加载模型；禁止加载用户上传文件。
        required = {"vectorizer", "classifiers", "label_codes", "version"}  # 定义工件最小契约。
        if not isinstance(bundle, dict) or not required.issubset(bundle):  # 校验工件形状。
            raise ValueError("invalid historical model artifact")  # 阻止不兼容或损坏工件上线。
        self._vectorizer = bundle["vectorizer"]  # 保存无状态 HashingVectorizer。
        self._classifiers = dict(bundle["classifiers"])  # 保存每个标签的二分类器。
        self._label_codes = list(bundle["label_codes"])  # 保存训练标签顺序。
        self._version = str(bundle["version"])  # 保存模型注册版本。

    @property  # 将模型版本作为只读属性暴露。
    def version(self) -> str:  # 返回模型工件版本。
        return self._version  # 用于预测快照和人工反馈对齐。

    def predict_scores(self, question: QuestionInput) -> Dict[str, float]:  # 为一个题目生成每个标签概率分数。
        matrix = self._vectorizer.transform([question_to_model_text(question)])  # 使用训练时同一特征器转换文本。
        scores: Dict[str, float] = {}  # 初始化标签分数字典。
        for code in self._label_codes:  # 遍历工件声明的标签集合。
            classifier = self._classifiers[code]  # 读取对应二分类器。
            probability = float(classifier.predict_proba(matrix)[0][1])  # 读取正类概率。
            scores[code] = probability  # 保存给融合图使用。
        return scores  # 返回完整多标签分数。
