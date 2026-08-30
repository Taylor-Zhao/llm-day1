"""题目标注领域对象；所有 API、工作流和存储层都共享这些契约。"""  # 说明模块职责。

from __future__ import annotations  # 延迟解析类型注解，兼容项目使用的 Python 3.9。

from enum import Enum  # 使用枚举限制学科取值，避免任意字符串进入核心流程。
from typing import Dict, List  # 使用 Python 3.9 可稳定运行的集合类型注解。

from pydantic import BaseModel, ConfigDict, Field  # 使用 Pydantic 校验外部与内部结构化数据。


class Subject(str, Enum):  # str 混入使枚举可直接序列化为 JSON 字符串。
    CHINESE = "chinese"  # 语文学科编码。
    MATH = "math"  # 数学学科编码。
    ENGLISH = "english"  # 英语学科编码。


class QuestionInput(BaseModel):  # 定义进入预测图的标准题目结构。
    model_config = ConfigDict(str_strip_whitespace=True)  # 自动清理字符串首尾空白。

    external_id: str = Field(min_length=1, max_length=128)  # 保存上游题库的稳定业务 ID。
    subject: Subject  # 指明语文、数学或英语，以选择适用标签。
    stem: str = Field(min_length=1, max_length=20_000)  # 保存题干并限制异常大请求。
    reference_answer: str = Field(min_length=1, max_length=10_000)  # 保存标准答案或答案集合。
    analysis: str = Field(default="", max_length=20_000)  # 保存可选解析，为模型和 RAG 提供上下文。
    source: str = Field(default="internal", max_length=128)  # 记录数据来源以便审计与治理。
    source_uri: str = Field(default="", max_length=2_048)  # 保存内部记录地址或公开数据集页面以便追溯。
    license_name: str = Field(default="internal", max_length=128)  # 保存数据使用授权或公开许可证名称。


class TagDefinition(BaseModel):  # 定义标签字典中的一个可展示标签。
    model_config = ConfigDict(frozen=True)  # 标签定义在运行期间不可被意外修改。

    code: str  # 保存机器稳定编码，数据库和模型都使用该字段。
    name: str  # 保存业务人员看到的中文名称。
    description: str  # 解释何时应选择标签。
    subjects: List[Subject]  # 限制该标签适用的学科。
    high_risk: bool = False  # 标记需要业务人员重点复核的标签。


class RetrievedEvidence(BaseModel):  # 表示从私有题库或合规外部数据召回的相似样本。
    evidence_id: str  # 保存证据唯一 ID，支持引用追踪。
    question_id: str  # 保存原题 ID，便于跳回权威数据源。
    subject: Subject  # 保存证据学科，默认只召回同学科数据。
    stem: str  # 保存相似题题干摘要。
    reference_answer: str  # 保存相似题答案摘要。
    human_labels: List[str]  # 保存已由人工确认的标签编码。
    score: float = Field(ge=0.0, le=1.0)  # 保存归一化相似度。
    source: str  # 标识内部题库、公开数据集或其他来源。
    source_uri: str = ""  # 保存可追溯来源地址但不由模型修改。
    license_name: str = "internal"  # 保存该证据的数据使用授权。


class ReasonedLabel(BaseModel):  # 表示 LLM 对单个标签给出的结构化判断。
    code: str  # 保存标签编码。
    confidence: float = Field(ge=0.0, le=1.0)  # 保存模型置信度而非直接当成真实概率。
    reason: str = Field(min_length=1, max_length=1_000)  # 给人工复核人员展示简短理由。
    evidence_ids: List[str] = Field(default_factory=list)  # 关联支持判断的 RAG 证据。


class ReasonerOutput(BaseModel):  # 表示 LLM 一次结构化输出的完整结果。
    labels: List[ReasonedLabel] = Field(default_factory=list)  # 保存模型认为相关的标签列表。
    warnings: List[str] = Field(default_factory=list)  # 保存证据不足或答案冲突等警告。


class LabelSuggestion(BaseModel):  # 表示最终展示给 Vue 页面的一项标签建议。
    code: str  # 保存标签机器编码。
    name: str  # 保存标签中文名，减少前端二次关联错误。
    confidence: float = Field(ge=0.0, le=1.0)  # 保存融合后的置信度。
    selected_by_default: bool  # 指示补录页面是否默认勾选。
    high_risk: bool  # 提醒人工重点检查高风险标签。
    reason: str  # 汇总模型、规则与 RAG 的解释。
    source_scores: Dict[str, float] = Field(default_factory=dict)  # 展示每个信号源的分数，便于排错。
    evidence_ids: List[str] = Field(default_factory=list)  # 保存用于该标签的证据 ID。


class PredictionResult(BaseModel):  # 定义在线预测服务返回的完整契约。
    question: QuestionInput  # 回传规范化后的题目快照。
    suggestions: List[LabelSuggestion]  # 返回所有适用标签及默认选择状态。
    evidence: List[RetrievedEvidence]  # 返回 RAG 证据供人工核验。
    requires_human_review: bool = True  # 生产策略要求所有模型建议都经过人工提交。
    model_version: str  # 记录专用模型、Prompt 与融合策略版本。
    warnings: List[str] = Field(default_factory=list)  # 返回降级和不确定性提示。
