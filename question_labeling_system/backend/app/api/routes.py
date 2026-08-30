"""补录系统 REST API；每个路由只负责鉴权、转换和调用用例服务。"""  # 避免在 HTTP 层复制业务规则。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

from typing import Optional  # 标注可选学科筛选。

from fastapi import APIRouter, Depends, Request, Response, status  # 导入路由和依赖注入能力。

from app.api.schemas import AnnotationResponse, CreateQuestionRequest, PrincipalResponse, SubmitAnnotationRequest, TaskBundleResponse, TaskView, TaxonomyResponse  # 导入传输模型。
from app.core.auth import UserPrincipal, current_user, require_role  # 导入认证授权依赖。
from app.domain.models import Subject  # 导入学科枚举用于查询参数校验。
from app.domain.taxonomy import tags_for_subject  # 导入受控标签查询。
from app.services.application import LabelingApplicationService, TaskBundle  # 导入用例服务和 DTO。


router = APIRouter(prefix="/api/v1")  # 为所有业务接口添加稳定版本前缀。


def _service(request: Request) -> LabelingApplicationService:  # 从应用容器读取用例服务。
    return request.app.state.container.service  # 返回单例无状态服务。


def _request_id(request: Request) -> str:  # 读取中间件生成的请求追踪 ID。
    return str(request.state.request_id)  # 转成稳定字符串。


def _task_response(bundle: TaskBundle) -> TaskBundleResponse:  # 将应用 DTO 转为 HTTP 响应。
    task = TaskView(id=bundle.task.id, question=bundle.task.question, status=bundle.task.status, version=bundle.task.version, claimed_by=bundle.task.claimed_by, claimed_until=bundle.task.claimed_until)  # 构造任务状态。
    return TaskBundleResponse(task=task, prediction_id=bundle.prediction_id, prediction=bundle.prediction)  # 组合模型预测和任务。


@router.get("/me", response_model=PrincipalResponse)  # 暴露当前认证主体供前端展示和审计确认。
def get_current_principal(principal: UserPrincipal = Depends(current_user)) -> PrincipalResponse:  # 读取可信 JWT/开发身份。
    return PrincipalResponse(user_id=principal.user_id, roles=sorted(principal.roles))  # 返回稳定排序角色且不暴露令牌声明原文。


@router.get("/taxonomy", response_model=TaxonomyResponse)  # 暴露按学科过滤的标签字典。
def get_taxonomy(subject: Subject, principal: UserPrincipal = Depends(current_user)) -> TaxonomyResponse:  # 获取补录页面所有复选项。
    require_role(principal, "labeler")  # 仅允许补录人员读取内部标签定义。
    return TaxonomyResponse(subject=subject, tags=tags_for_subject(subject))  # 返回当前学科标签。


@router.post("/questions", response_model=TaskBundleResponse, status_code=status.HTTP_201_CREATED)  # 暴露上游题目导入接口。
def create_question(payload: CreateQuestionRequest, request: Request, principal: UserPrincipal = Depends(current_user)) -> TaskBundleResponse:  # 幂等创建并可同步预测。
    require_role(principal, "importer")  # 仅允许题库导入角色创建任务。
    bundle = _service(request).create_question(payload.question, principal.user_id, _request_id(request), payload.predict_now)  # 调用应用服务。
    return _task_response(bundle)  # 返回页面可直接使用的数据。


@router.post("/questions/{question_id}/predict", response_model=TaskBundleResponse)  # 暴露重跑预测接口用于模型升级或失败恢复。
def predict_question(question_id: int, request: Request, force: bool = False, principal: UserPrincipal = Depends(current_user)) -> TaskBundleResponse:  # 生成或复用预测。
    require_role(principal, "labeler")  # 补录人员可手动刷新模型建议。
    bundle = _service(request).predict_task(question_id, principal.user_id, _request_id(request), force=force)  # 执行预测用例。
    return _task_response(bundle)  # 返回最新预测。


@router.get("/tasks/next", response_model=Optional[TaskBundleResponse], status_code=status.HTTP_200_OK)  # 领取下一条待补录任务。
def claim_next_task(request: Request, response: Response, subject: Optional[Subject] = None, principal: UserPrincipal = Depends(current_user)) -> Optional[TaskBundleResponse]:  # 支持按学科筛选队列。
    require_role(principal, "labeler")  # 仅补录人员可领取任务。
    bundle = _service(request).claim_next(principal.user_id, _request_id(request), subject)  # 原子领取并确保存在预测。
    if bundle is None:  # 队列为空时不返回伪任务。
        response.status_code = status.HTTP_204_NO_CONTENT  # 使用 204 表示暂无任务。
        return None  # 返回空响应体。
    return _task_response(bundle)  # 返回领取后的任务和模型建议。


@router.get("/tasks/{question_id}", response_model=TaskBundleResponse)  # 允许刷新当前任务页面。
def get_task(question_id: int, request: Request, principal: UserPrincipal = Depends(current_user)) -> TaskBundleResponse:  # 读取指定任务和最新预测。
    require_role(principal, "labeler")  # 仅补录人员可查看题目。
    return _task_response(_service(request).get_task(question_id))  # 调用用例服务并转换响应。


@router.post("/tasks/{question_id}/submit", response_model=AnnotationResponse, status_code=status.HTTP_201_CREATED)  # 提交人工最终标签。
def submit_annotation(question_id: int, payload: SubmitAnnotationRequest, request: Request, principal: UserPrincipal = Depends(current_user)) -> AnnotationResponse:  # 接收复选框和备注。
    require_role(principal, "labeler")  # 仅补录人员可提交。
    result = _service(request).submit(question_id, payload.prediction_id, payload.selected_tags, principal.user_id, payload.note, payload.idempotency_key, payload.expected_version, _request_id(request))  # 执行幂等事务提交。
    return AnnotationResponse(annotation_id=result.id, question_id=result.question_id, selected_tags=result.selected_tags, added_tags=result.added_tags, removed_tags=result.removed_tags, operator_id=result.operator_id, created_at=result.created_at)  # 返回可用于 UI 成功提示的结果。
