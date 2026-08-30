"""本地开发使用的原创合成题目；生产环境应替换为授权内部题库。"""  # 避免把未知许可证的网络题目直接提交到仓库。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

from typing import List  # 标注种子证据列表。

from app.domain.models import RetrievedEvidence, Subject  # 导入证据和学科类型。


def local_evidence() -> List[RetrievedEvidence]:  # 返回覆盖三个学科与关键标签的合成样本。
    evidence = [  # 每条样本都明确标记 synthetic，不能冒充真实训练数据。
        RetrievedEvidence(evidence_id="seed-cn-1", question_id="seed-cn-1", subject=Subject.CHINESE, stem="请写出一个描写春天的成语：____。", reference_answer="春暖花开；其他合理答案也可。", human_labels=["answer_not_unique", "semantic_equivalence"], score=1.0, source="project_synthetic_seed"),  # 语文开放答案示例。
        RetrievedEvidence(evidence_id="seed-cn-2", question_id="seed-cn-2", subject=Subject.CHINESE, stem="默写诗句：海内存知己，____。", reference_answer="天涯若比邻", human_labels=["quotation_required", "exact_match_required"], score=1.0, source="project_synthetic_seed"),  # 语文原文默写示例。
        RetrievedEvidence(evidence_id="seed-cn-3", question_id="seed-cn-3", subject=Subject.CHINESE, stem="根据短文填写主人公的品质：____。", reference_answer="勇敢", human_labels=["context_dependent", "semantic_equivalence"], score=1.0, source="project_synthetic_seed"),  # 语文上下文示例。
        RetrievedEvidence(evidence_id="seed-math-1", question_id="seed-math-1", subject=Subject.MATH, stem="一个长方形长 5 厘米、宽 3 厘米，面积是____。", reference_answer="15 平方厘米", human_labels=["calculation_required", "unit_required"], score=1.0, source="project_synthetic_seed"),  # 数学单位示例。
        RetrievedEvidence(evidence_id="seed-math-2", question_id="seed-math-2", subject=Subject.MATH, stem="与 x+x 等价的表达式是____。", reference_answer="2x", human_labels=["equivalent_expression", "formula_required"], score=1.0, source="project_synthetic_seed"),  # 数学等价表达示例。
        RetrievedEvidence(evidence_id="seed-math-3", question_id="seed-math-3", subject=Subject.MATH, stem="计算：12÷3=____，4×2=____。", reference_answer="4；8", human_labels=["multiple_blanks", "blank_order_matters", "calculation_required"], score=1.0, source="project_synthetic_seed"),  # 数学多空示例。
        RetrievedEvidence(evidence_id="seed-en-1", question_id="seed-en-1", subject=Subject.ENGLISH, stem="Yesterday she ____ (go) to school.", reference_answer="went", human_labels=["grammar_tense", "spelling_sensitive"], score=1.0, source="project_synthetic_seed"),  # 英语时态示例。
        RetrievedEvidence(evidence_id="seed-en-2", question_id="seed-en-2", subject=Subject.ENGLISH, stem="Use the correct form: She sings ____ (beautiful).", reference_answer="beautifully", human_labels=["grammar_part_of_speech", "spelling_sensitive"], score=1.0, source="project_synthetic_seed"),  # 英语词性变化示例。
        RetrievedEvidence(evidence_id="seed-en-3", question_id="seed-en-3", subject=Subject.ENGLISH, stem="Complete the dialogue: A: Thank you. B: ____.", reference_answer="You're welcome / That's all right", human_labels=["answer_not_unique", "context_dependent", "punctuation_sensitive"], score=1.0, source="project_synthetic_seed"),  # 英语对话开放答案示例。
    ]  # 完成原创证据集合。
    return [item.model_copy(update={"license_name": "project-authored"}) for item in evidence]  # 统一声明项目原创授权来源。
