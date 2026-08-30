"""使用 LangChain Prompt、ChatModel 与结构化输出实现标签推理。"""  # 将供应商调用封装在适配器内。

from __future__ import annotations  # 延迟类型求值以兼容 Python 3.9。

import json  # 将标签定义和 RAG 证据序列化进 Prompt。
from typing import Sequence  # 标注标签和证据序列。

from langchain_core.prompts import ChatPromptTemplate  # 使用 LCEL 可组合 Prompt。
from langchain_openai import ChatOpenAI  # 连接 OpenAI 兼容模型服务。

from app.domain.models import QuestionInput, ReasonerOutput, RetrievedEvidence, TagDefinition  # 导入结构化输入输出。


SYSTEM_PROMPT = """你是中小学填空题标签审核助手。只能从给定标签编码中选择，不得创造标签。\n先依据题干和参考答案判断，再使用相似题作为辅助证据；相似题可能有错，不能盲从。\n对答案不唯一、顺序敏感、单位要求和等价表达等高风险属性要保守判断。\n输出每个建议标签、0到1置信度、简短理由和实际使用的 evidence_id；证据不足时写入 warnings。"""  # 固定生产 Prompt 的安全和证据边界。


class DisabledLabelReasoner:  # 为本地无模型环境提供显式降级适配器。
    version = "llm-disabled"  # 在预测快照中明确标记 LLM 未启用。

    def reason(self, question: QuestionInput, tags: Sequence[TagDefinition], evidence: Sequence[RetrievedEvidence]) -> ReasonerOutput:  # 满足统一推理接口。
        del question, tags, evidence  # 明确本适配器不使用输入。
        raise RuntimeError("LLM reasoner is disabled")  # 由 LangGraph 节点捕获并记录降级警告。


class LangChainLabelReasoner:  # 使用真实 LangChain LCEL 结构化链。
    def __init__(self, api_key: str, base_url: str, model_name: str, timeout_seconds: float = 30.0) -> None:  # 注入模型连接参数。
        if not api_key:  # 生产推理必须显式提供密钥。
            raise ValueError("api_key is required")  # 防止请求运行时才失败。
        self._version = f"{model_name}:label-prompt-v1"  # 组合模型和 Prompt 版本。
        model = ChatOpenAI(model=model_name, api_key=api_key, base_url=base_url, temperature=0.0, max_tokens=1_200, timeout=timeout_seconds, max_retries=2)  # 配置低温度、超时和有界 SDK 重试。
        prompt = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", "学科：{subject}\n题干：{stem}\n参考答案：{answer}\n解析：{analysis}\n\n允许标签：\n{tags_json}\n\n相似题证据：\n{evidence_json}")])  # 使用明确字段构造 Prompt。
        structured_model = model.with_structured_output(ReasonerOutput)  # 让 Provider 或解析器按 Pydantic 契约返回数据。
        self._chain = prompt | structured_model  # 使用 LCEL 组合 Prompt 和模型。

    @property  # 暴露稳定版本用于审计。
    def version(self) -> str:  # 返回模型和 Prompt 组合版本。
        return self._version  # 供 PredictionRun 保存。

    def reason(self, question: QuestionInput, tags: Sequence[TagDefinition], evidence: Sequence[RetrievedEvidence]) -> ReasonerOutput:  # 执行一次结构化标签判断。
        tag_payload = [tag.model_dump(mode="json") for tag in tags]  # 将标签定义转换为 JSON 兼容结构。
        evidence_payload = [item.model_dump(mode="json") for item in evidence]  # 将证据转换为 JSON 兼容结构。
        output = self._chain.invoke({"subject": question.subject.value, "stem": question.stem, "answer": question.reference_answer, "analysis": question.analysis or "无", "tags_json": json.dumps(tag_payload, ensure_ascii=False), "evidence_json": json.dumps(evidence_payload, ensure_ascii=False)})  # 调用完整 LCEL Chain。
        if isinstance(output, ReasonerOutput):  # 当前 LangChain/Pydantic 组合应直接返回目标模型。
            return output  # 返回已校验结构。
        return ReasonerOutput.model_validate(output)  # 兼容 Provider 返回普通字典的情况。
