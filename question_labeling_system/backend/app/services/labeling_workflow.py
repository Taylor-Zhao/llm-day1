"""使用 LangGraph 编排规则、历史模型、RAG 和 LLM 的在线预标注流程。"""  # 这是系统自动打标的核心控制面。

from __future__ import annotations  # 延迟类型求值以兼容 Python 3.9。

import math  # 使用指数函数把 RAG 排名转换为衰减分数。
import re  # 使用正则识别题干中的空格和数学符号。
from typing import Dict, List, Mapping, Sequence, TypedDict, cast  # 定义图状态与集合契约。

from langgraph.graph import END, START, StateGraph  # 使用真实 LangGraph 构建显式节点和边。

from app.domain.models import LabelSuggestion, PredictionResult, QuestionInput, ReasonerOutput, RetrievedEvidence  # 导入领域对象。
from app.domain.taxonomy import tags_for_subject  # 按学科过滤可用标签。
from app.services.contracts import EvidenceRetriever, HistoricalLabelModel, LabelReasoner  # 导入可替换依赖端口。


class LabelingState(TypedDict, total=False):  # 定义贯穿 LangGraph 的可序列化状态。
    question: QuestionInput  # 保存规范化题目。
    normalized_text: str  # 保存用于模型与检索的稳定文本。
    rule_scores: Dict[str, float]  # 保存确定性规则信号。
    historical_scores: Dict[str, float]  # 保存百万历史数据模型分数。
    evidence: List[RetrievedEvidence]  # 保存混合 RAG 召回证据。
    reasoner_output: ReasonerOutput  # 保存 LangChain LLM 结构化判断。
    suggestions: List[LabelSuggestion]  # 保存融合后的页面展示结果。
    warnings: List[str]  # 保存降级与数据质量警告。


def _count_blanks(stem: str) -> int:  # 统计常见中文、英文和下划线填空符号。
    patterns = [r"_{2,}", r"（\s*）", r"\(\s*\)", r"\[\s*\]"]  # 定义不会匹配普通括号内容的空格模式。
    return sum(len(re.findall(pattern, stem)) for pattern in patterns)  # 汇总所有模式命中数。


def rule_based_scores(question: QuestionInput) -> Dict[str, float]:  # 提取可解释且稳定的标签规则信号。
    stem = question.stem.lower()  # 统一大小写以便匹配英文提示词。
    answer = question.reference_answer.lower()  # 统一答案大小写以便匹配开放答案描述。
    scores: Dict[str, float] = {}  # 初始化只保存命中规则的稀疏分数字典。
    blank_count = _count_blanks(question.stem)  # 统计题目空位数。
    if blank_count >= 2:  # 两个以上空位通常对应多空题。
        scores["multiple_blanks"] = 0.98  # 给出高置信规则分数。
        scores["blank_order_matters"] = 0.72  # 默认提示人工检查答案顺序关系。
    open_markers = ("答案不唯一", "合理即可", "示例", "均可", "或", "any reasonable")  # 定义开放答案标志词。
    if any(marker in answer for marker in open_markers):  # 判断参考答案是否明确允许多种答案。
        scores["answer_not_unique"] = 0.96  # 标记答案不唯一。
    context_markers = ("根据短文", "根据材料", "结合上下文", "看图", "according to", "dialogue")  # 定义上下文依赖提示词。
    if any(marker in stem for marker in context_markers):  # 判断题目是否依赖外部材料。
        scores["context_dependent"] = 0.90  # 标记上下文依赖。
    if question.subject.value == "math":  # 仅对数学题应用数学规则。
        if re.search(r"[+\-×÷*/=]|计算|求出|多少", question.stem):  # 检测运算符或计算动词。
            scores["calculation_required"] = 0.88  # 标记需要计算。
        if re.search(r"单位|厘米|米|千克|克|元|角|分|秒|小时|%", question.stem + question.reference_answer):  # 检测常见单位。
            scores["unit_required"] = 0.87  # 标记单位要求并交由人工最终确认。
        if re.search(r"[=+\-*/^]|方程|公式|表达式", question.reference_answer):  # 检测公式或表达式答案。
            scores["formula_required"] = 0.82  # 标记公式型答案。
            scores["equivalent_expression"] = 0.68  # 提醒可能存在数学等价表达。
    if question.subject.value == "english":  # 仅对英语题应用英语规则。
        scores["spelling_sensitive"] = 0.74  # 英语填空通常默认要求正确拼写。
        if re.search(r"正确形式|proper form|tense|时态|动词", stem):  # 检测时态或动词变化提示。
            scores["grammar_tense"] = 0.86  # 标记时态考点。
        if re.search(r"词性|适当形式|correct form|形容词|副词|名词", stem):  # 检测词性变化提示。
            scores["grammar_part_of_speech"] = 0.84  # 标记词性变化。
    if question.subject.value == "chinese" and re.search(r"原文|默写|诗句|名句", question.stem):  # 检测语文原文填空。
        scores["quotation_required"] = 0.94  # 标记必须引用原文。
        scores["exact_match_required"] = 0.84  # 原文默写通常需要精确匹配。
    return scores  # 返回可供融合与解释的规则信号。


def _validate_scores(scores: Mapping[str, float], allowed_codes: Sequence[str], source: str) -> Dict[str, float]:  # 清洗外部模型分数。
    allowed = set(allowed_codes)  # 建立允许标签集合，阻止模型创造新标签。
    clean: Dict[str, float] = {}  # 初始化合法分数字典。
    for code, raw_score in scores.items():  # 遍历模型或规则输出。
        if code not in allowed:  # 忽略不适用于当前学科或未知的标签。
            continue  # 跳过不受控标签。
        score = float(raw_score)  # 将 numpy 等数值统一转换为 Python float。
        if math.isnan(score) or math.isinf(score):  # 拒绝不可序列化且无业务意义的数值。
            raise ValueError(f"{source} returned invalid score for {code}")  # 暴露模型工件错误。
        clean[code] = min(1.0, max(0.0, score))  # 将轻微越界值裁剪到合法范围。
    return clean  # 返回已校验分数。


class QuestionLabelingWorkflow:  # 封装可复用的编译图与融合策略。
    def __init__(self, historical_model: HistoricalLabelModel, retriever: EvidenceRetriever, reasoner: LabelReasoner, selection_threshold: float = 0.58, rag_limit: int = 6) -> None:  # 注入线上依赖与策略参数。
        if not 0.0 < selection_threshold < 1.0:  # 防止配置导致全选或全不选。
            raise ValueError("selection_threshold must be between 0 and 1")  # 提前报告错误配置。
        if rag_limit <= 0:  # 确保至少允许召回一条证据。
            raise ValueError("rag_limit must be positive")  # 提前报告错误配置。
        self.historical_model = historical_model  # 保存历史数据模型适配器。
        self.retriever = retriever  # 保存混合检索适配器。
        self.reasoner = reasoner  # 保存 LangChain LLM 推理适配器。
        self.selection_threshold = selection_threshold  # 保存默认勾选阈值。
        self.rag_limit = rag_limit  # 保存最大证据数量。
        builder = StateGraph(LabelingState)  # 创建 LangGraph 状态图构建器。
        builder.add_node("normalize", self._normalize)  # 注册输入规范化节点。
        builder.add_node("rules", self._rules)  # 注册确定性规则节点。
        builder.add_node("retrieve", self._retrieve)  # 注册 RAG 召回节点。
        builder.add_node("historical_model", self._historical_model)  # 注册百万数据训练模型节点。
        builder.add_node("reason", self._reason)  # 注册 LangChain LLM 推理节点。
        builder.add_node("ensemble", self._ensemble)  # 注册多信号融合节点。
        builder.add_edge(START, "normalize")  # 设置工作流入口。
        builder.add_edge("normalize", "rules")  # 规范化后先执行低成本规则。
        builder.add_edge("rules", "retrieve")  # 再召回人工确认过的相似题。
        builder.add_edge("retrieve", "historical_model")  # 再执行专用监督模型。
        builder.add_edge("historical_model", "reason")  # 最后让 LLM 结合证据推理。
        builder.add_edge("reason", "ensemble")  # 将所有信号送入融合策略。
        builder.add_edge("ensemble", END)  # 融合完成后结束图执行。
        self.graph = builder.compile()  # 编译成可调用的 LangGraph Runnable。

    def _normalize(self, state: LabelingState) -> Dict[str, object]:  # 构建稳定的检索和模型文本。
        question = state["question"]  # 读取已经过 Pydantic 校验的题目。
        normalized = "\n".join(part for part in [question.stem.strip(), question.reference_answer.strip(), question.analysis.strip()] if part)  # 拼接非空字段。
        return {"normalized_text": normalized, "warnings": []}  # 写回规范文本并初始化警告列表。

    def _rules(self, state: LabelingState) -> Dict[str, object]:  # 运行确定性规则特征。
        return {"rule_scores": rule_based_scores(state["question"])}  # 将规则分数写回图状态。

    def _retrieve(self, state: LabelingState) -> Dict[str, object]:  # 从内部和合规外部知识库召回相似题。
        try:  # RAG 不可用时允许受控降级，但必须留下警告。
            evidence = list(self.retriever.retrieve(state["question"], self.rag_limit))  # 执行混合召回并物化结果。
            return {"evidence": evidence}  # 将证据写回图状态。
        except Exception as exc:  # 捕获检索基础设施故障以保留人工补录能力。
            warnings = list(state.get("warnings", []))  # 复制现有警告，避免原地修改状态。
            warnings.append(f"RAG 检索降级：{type(exc).__name__}")  # 只暴露错误类型，避免泄漏敏感细节。
            return {"evidence": [], "warnings": warnings}  # 以空证据继续执行专用模型和规则。

    def _historical_model(self, state: LabelingState) -> Dict[str, object]:  # 调用由历史人工标签训练的多标签模型。
        allowed = [tag.code for tag in tags_for_subject(state["question"].subject)]  # 获取当前学科允许标签。
        scores = self.historical_model.predict_scores(state["question"])  # 获取模型原始分数。
        return {"historical_scores": _validate_scores(scores, allowed, "historical_model")}  # 校验并写回状态。

    def _reason(self, state: LabelingState) -> Dict[str, object]:  # 让 LLM 基于题目、标签定义和 RAG 证据给出解释。
        tags = tags_for_subject(state["question"].subject)  # 获取当前学科标签合同。
        try:  # LLM 不可用时保留规则、历史模型和 RAG 的降级结果。
            output = self.reasoner.reason(state["question"], tags, state.get("evidence", []))  # 执行结构化推理。
            return {"reasoner_output": output}  # 将 LLM 输出写回图状态。
        except Exception as exc:  # 捕获模型超时、限流或结构化解析失败。
            warnings = list(state.get("warnings", []))  # 复制当前警告。
            warnings.append(f"LLM 推理降级：{type(exc).__name__}")  # 记录可观测但不泄密的降级原因。
            return {"reasoner_output": ReasonerOutput(), "warnings": warnings}  # 使用空输出继续融合。

    def _ensemble(self, state: LabelingState) -> Dict[str, object]:  # 融合规则、监督模型、RAG 与 LLM 信号。
        question = state["question"]  # 读取当前题目。
        tags = tags_for_subject(question.subject)  # 获取页面需要展示的所有适用标签。
        reasoned = {item.code: item for item in state.get("reasoner_output", ReasonerOutput()).labels if any(tag.code == item.code for tag in tags)}  # 过滤 LLM 幻觉标签。
        rag_scores: Dict[str, float] = {}  # 初始化按标签聚合的 RAG 分数。
        rag_evidence_ids: Dict[str, List[str]] = {}  # 初始化每个标签对应证据列表。
        for rank, evidence in enumerate(state.get("evidence", []), start=1):  # 按召回排名遍历人工样本。
            rank_weight = evidence.score * (1.0 / math.log2(rank + 1.0))  # 同时考虑相似度与排名衰减。
            for code in evidence.human_labels:  # 遍历相似题经过人工确认的标签。
                rag_scores[code] = max(rag_scores.get(code, 0.0), rank_weight)  # 使用最强证据，避免重复样本放大分数。
                rag_evidence_ids.setdefault(code, []).append(evidence.evidence_id)  # 保存可追溯证据 ID。
        suggestions: List[LabelSuggestion] = []  # 初始化完整标签建议列表。
        for tag in tags:  # 为每个适用标签计算融合分数，即使未选中也返回前端。
            sources: Dict[str, float] = {}  # 初始化当前标签的信号源分数。
            if tag.code in state.get("rule_scores", {}):  # 仅在规则命中时加入权重。
                sources["rule"] = state["rule_scores"][tag.code]  # 保存规则分数。
            if tag.code in state.get("historical_scores", {}):  # 仅在监督模型返回时加入权重。
                sources["historical_model"] = state["historical_scores"][tag.code]  # 保存监督模型分数。
            if tag.code in rag_scores:  # 仅在相似人工题支持时加入权重。
                sources["rag"] = min(1.0, rag_scores[tag.code])  # 保存裁剪后的 RAG 分数。
            if tag.code in reasoned:  # 仅在 LLM 显式判断时加入权重。
                sources["llm"] = reasoned[tag.code].confidence  # 保存 LLM 置信度。
            weights = {"rule": 0.15, "historical_model": 0.40, "rag": 0.20, "llm": 0.25}  # 定义可版本化的融合权重。
            denominator = sum(weights[source] for source in sources)  # 只按实际可用信号归一化，支持降级。
            confidence = sum(score * weights[source] for source, score in sources.items()) / denominator if denominator else 0.0  # 计算加权分数。
            reason = reasoned[tag.code].reason if tag.code in reasoned else "由历史模型、规则和相似题综合判断；请人工确认。"  # 优先展示 LLM 解释。
            evidence_ids = list(dict.fromkeys((reasoned[tag.code].evidence_ids if tag.code in reasoned else []) + rag_evidence_ids.get(tag.code, [])))  # 合并并去重引用。
            suggestions.append(LabelSuggestion(code=tag.code, name=tag.name, confidence=round(confidence, 4), selected_by_default=confidence >= self.selection_threshold, high_risk=tag.high_risk, reason=reason, source_scores={key: round(value, 4) for key, value in sources.items()}, evidence_ids=evidence_ids))  # 创建页面展示建议。
        suggestions.sort(key=lambda item: (not item.selected_by_default, -item.confidence, item.code))  # 默认选中标签优先，再按置信度排序。
        return {"suggestions": suggestions}  # 将最终建议写回图状态。

    def predict(self, question: QuestionInput) -> PredictionResult:  # 对外暴露稳定的同步预测入口。
        final_state = cast(LabelingState, self.graph.invoke({"question": question}))  # 执行整张图并获取最终状态。
        version = f"historical={self.historical_model.version};reasoner={self.reasoner.version};ensemble=v1"  # 组合完整模型链版本。
        return PredictionResult(question=question, suggestions=final_state["suggestions"], evidence=final_state.get("evidence", []), requires_human_review=True, model_version=version, warnings=final_state.get("warnings", []))  # 返回可审计结果。
