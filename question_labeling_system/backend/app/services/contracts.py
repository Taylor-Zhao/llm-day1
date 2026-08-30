"""标注图依赖的端口协议；生产适配器与测试替身使用同一接口。"""  # 使用依赖倒置隔离模型、检索和业务流程。

from __future__ import annotations  # 允许在 Python 3.9 中书写现代类型注解。

from typing import Mapping, Protocol, Sequence  # Protocol 用于结构化依赖接口。

from app.domain.models import QuestionInput, ReasonerOutput, RetrievedEvidence, TagDefinition  # 导入领域契约。


class HistoricalLabelModel(Protocol):  # 定义由 100 万人工样本训练出的专用多标签模型接口。
    @property  # 将版本作为只读属性暴露给审计层。
    def version(self) -> str:  # 返回模型工件版本。
        ...  # 具体实现由线上模型适配器提供。

    def predict_scores(self, question: QuestionInput) -> Mapping[str, float]:  # 为受控标签返回 0 到 1 的分数。
        ...  # 测试可注入固定输出，生产可加载 sklearn 或远程 LLM 模型。


class EvidenceRetriever(Protocol):  # 定义混合 RAG 检索接口。
    def retrieve(self, question: QuestionInput, limit: int) -> Sequence[RetrievedEvidence]:  # 召回同学科人工标注样本。
        ...  # 生产实现组合 Dense、Sparse 与 RRF。


class LabelReasoner(Protocol):  # 定义 LangChain 结构化 LLM 推理接口。
    @property  # 暴露 Prompt/LLM 版本用于审计。
    def version(self) -> str:  # 返回推理器版本。
        ...  # 具体实现由 LangChain 适配器提供。

    def reason(self, question: QuestionInput, tags: Sequence[TagDefinition], evidence: Sequence[RetrievedEvidence]) -> ReasonerOutput:  # 基于题目、标签定义和证据判断。
        ...  # 实现必须只返回受控标签并给出解释。
