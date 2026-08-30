"""语文、数学、英语填空题的受控标签字典。"""  # 标签应由业务配置管理，而不是让模型自由创造。

from __future__ import annotations  # 延迟类型求值以兼容 Python 3.9。

from typing import Dict, List  # 导入显式集合类型，便于静态检查。

from .models import Subject, TagDefinition  # 复用领域枚举和标签结构。


ALL_SUBJECTS = [Subject.CHINESE, Subject.MATH, Subject.ENGLISH]  # 定义三个学科的复用集合。


TAG_DEFINITIONS: List[TagDefinition] = [  # 定义生产初始标签集，后续可迁移到带版本的数据库字典。
    TagDefinition(code="answer_not_unique", name="答案不唯一", description="存在多个语义或数值等价答案，人工需确认可接受范围。", subjects=ALL_SUBJECTS, high_risk=True),  # 通用开放答案标签。
    TagDefinition(code="multiple_blanks", name="多空题", description="题干包含两个或以上需要分别作答的空。", subjects=ALL_SUBJECTS),  # 通用多空标签。
    TagDefinition(code="blank_order_matters", name="空位顺序敏感", description="多个答案与空位顺序一一对应，交换后不可得分。", subjects=ALL_SUBJECTS, high_risk=True),  # 通用顺序标签。
    TagDefinition(code="exact_match_required", name="精确匹配", description="答案需按标准字符串或固定术语匹配。", subjects=ALL_SUBJECTS),  # 通用精确匹配标签。
    TagDefinition(code="context_dependent", name="依赖上下文", description="需结合短文、对话、图表或前后句才能判断答案。", subjects=ALL_SUBJECTS),  # 通用上下文标签。
    TagDefinition(code="punctuation_sensitive", name="标点敏感", description="标点属于答案组成部分或会影响语义。", subjects=[Subject.CHINESE, Subject.ENGLISH]),  # 语文英语标点标签。
    TagDefinition(code="semantic_equivalence", name="语义等价可接受", description="不同措辞表达相同含义时可以判为正确。", subjects=[Subject.CHINESE, Subject.ENGLISH], high_risk=True),  # 文科语义标签。
    TagDefinition(code="quotation_required", name="需引用原文", description="答案必须来自指定文本或诗文原句。", subjects=[Subject.CHINESE]),  # 语文原文引用标签。
    TagDefinition(code="classical_chinese", name="文言文", description="题目涉及文言词义、句式或篇章。", subjects=[Subject.CHINESE]),  # 语文学科标签。
    TagDefinition(code="calculation_required", name="需要计算", description="需要执行算术、代数、几何或统计计算。", subjects=[Subject.MATH]),  # 数学计算标签。
    TagDefinition(code="unit_required", name="必须带单位", description="答案需包含正确单位，缺单位可能不得分。", subjects=[Subject.MATH], high_risk=True),  # 数学单位标签。
    TagDefinition(code="equivalent_expression", name="等价表达式可接受", description="不同但数学等价的式子都可作为答案。", subjects=[Subject.MATH], high_risk=True),  # 数学等价式标签。
    TagDefinition(code="formula_required", name="需填写公式", description="答案是公式、方程或符号表达式。", subjects=[Subject.MATH]),  # 数学公式标签。
    TagDefinition(code="case_sensitive", name="大小写敏感", description="英语答案的字母大小写会影响判分。", subjects=[Subject.ENGLISH]),  # 英语大小写标签。
    TagDefinition(code="spelling_sensitive", name="拼写敏感", description="单词拼写必须正确，轻微拼写错误不可接受。", subjects=[Subject.ENGLISH]),  # 英语拼写标签。
    TagDefinition(code="grammar_tense", name="时态考点", description="空格主要考查动词时态或语态变化。", subjects=[Subject.ENGLISH]),  # 英语时态标签。
    TagDefinition(code="grammar_part_of_speech", name="词性变化", description="空格需要根据语法完成名词、动词、形容词或副词变化。", subjects=[Subject.ENGLISH]),  # 英语词性标签。
]  # 结束标签字典定义。


TAG_BY_CODE: Dict[str, TagDefinition] = {item.code: item for item in TAG_DEFINITIONS}  # 建立 O(1) 编码查找表。


def tags_for_subject(subject: Subject) -> List[TagDefinition]:  # 返回指定学科可以展示的全部标签。
    return [item for item in TAG_DEFINITIONS if subject in item.subjects]  # 保留通用标签和当前学科标签。


def require_known_tag(code: str) -> TagDefinition:  # 校验模型或客户端返回的标签是否在受控字典中。
    try:  # 尝试按稳定编码读取标签。
        return TAG_BY_CODE[code]  # 返回已注册标签。
    except KeyError as exc:  # 捕获模型幻觉出的未知标签。
        raise ValueError(f"unknown tag code: {code}") from exc  # 转成清晰的领域错误。
