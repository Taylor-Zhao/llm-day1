"""HTTP 层的请求与响应结构。"""  # 将传输契约与数据库 ORM 解耦。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

from datetime import datetime  # 表示任务租约和提交时间。
from typing import List, Optional  # 声明可选字段和列表。

from pydantic import BaseModel, Field  # 使用 Pydantic 校验 API 数据。

from app.domain.models import PredictionResult, QuestionInput, Subject, TagDefinition  # 复用领域结构。


class CreateQuestionRequest(BaseModel):  # 定义上游题库导入请求。
    question: QuestionInput  # 保存完整题目内容。
    predict_now: bool = True  # 小批请求默认同步预测，大批导入应关闭并交给 worker。


class TaskView(BaseModel):  # 定义补录任务页面需要的状态字段。
    id: int  # 保存数据库任务 ID。
    question: QuestionInput  # 保存题目内容。
    status: str  # 保存当前任务状态。
    version: int  # 保存乐观锁版本，提交时必须回传。
    claimed_by: Optional[str]  # 保存当前领取人。
    claimed_until: Optional[datetime]  # 保存任务租约到期时间。


class TaskBundleResponse(BaseModel):  # 定义补录页面完整响应。
    task: TaskView  # 保存任务状态。
    prediction_id: str  # 保存模型预测运行 ID。
    prediction: PredictionResult  # 保存默认标签、证据和解释。


class SubmitAnnotationRequest(BaseModel):  # 定义人工最终提交请求。
    prediction_id: str = Field(min_length=1, max_length=36)  # 指明人工核对的是哪次预测。
    selected_tags: List[str] = Field(default_factory=list, max_length=100)  # 保存最终选择标签，允许确认无标签。
    note: str = Field(default="", max_length=2_000)  # 保存人工备注。
    idempotency_key: str = Field(min_length=8, max_length=128)  # 防止网络重试重复写入。
    expected_version: int = Field(ge=1)  # 防止旧页面覆盖新提交。


class AnnotationResponse(BaseModel):  # 定义人工提交结果。
    annotation_id: int  # 保存标注记录 ID。
    question_id: int  # 保存题目 ID。
    selected_tags: List[str]  # 保存最终标签。
    added_tags: List[str]  # 保存相对模型新增标签。
    removed_tags: List[str]  # 保存相对模型移除标签。
    operator_id: str  # 保存提交人。
    created_at: datetime  # 保存提交时间。


class TaxonomyResponse(BaseModel):  # 定义标签字典响应。
    subject: Subject  # 保存查询学科。
    tags: List[TagDefinition]  # 返回该学科全部可选标签。


class PrincipalResponse(BaseModel):  # 定义当前认证主体响应。
    user_id: str  # 保存 JWT subject 或开发调试用户。
    roles: List[str]  # 保存服务端确认的角色列表。


class ErrorResponse(BaseModel):  # 定义统一错误体。
    code: str  # 保存稳定机器错误码。
    message: str  # 保存面向用户的错误说明。
    request_id: str  # 保存排查请求 ID。
