"""FastAPI 应用工厂与生产依赖装配入口。"""  # 通过工厂支持测试注入和多进程部署。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import json  # 输出结构化请求日志。
import logging  # 使用标准日志接口交给容器采集。
import time  # 记录请求延迟。
import uuid  # 生成请求追踪 ID。
from typing import Optional  # 标注可选测试容器。

from fastapi import FastAPI, HTTPException, Request  # 创建 API 与异常处理器。
from fastapi.middleware.cors import CORSMiddleware  # 限制浏览器跨域来源。
from fastapi.responses import JSONResponse, Response  # 返回统一错误结构与 Prometheus 文本。
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # 暴露标准 Prometheus 指标格式。

from app.api.routes import router  # 导入业务路由。
from app.bootstrap import AppContainer, build_container  # 导入无 Web 副作用的依赖装配器。
from app.core.config import Settings  # 导入环境配置。
from app.db.repositories import ConcurrentUpdateError  # 导入并发错误用于统一 HTTP 映射。


LOGGER = logging.getLogger("question_labeling.api")  # 创建统一日志命名空间。


def create_app(container: Optional[AppContainer] = None) -> FastAPI:  # 创建可供 Uvicorn 和测试使用的应用实例。
    active_container = container or build_container(Settings.from_env())  # 默认从环境装配，也允许测试注入。
    app = FastAPI(title="智能题目标注补录系统", version="1.0.0", docs_url="/docs" if active_container.settings.environment != "production" else None, redoc_url=None)  # 生产关闭公开交互文档。
    app.state.container = active_container  # 将依赖容器挂到应用状态。
    app.add_middleware(CORSMiddleware, allow_origins=list(active_container.settings.cors_origins), allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Debug-User", "X-Debug-Roles"])  # 使用显式 CORS 白名单。

    @app.middleware("http")  # 为所有请求添加追踪和结构化日志。
    async def request_context(request: Request, call_next):  # 包装完整 HTTP 生命周期。
        request_id = request.headers.get("X-Request-ID", "")[:64] or str(uuid.uuid4())  # 复用合法上游 ID 或生成新 UUID。
        request.state.request_id = request_id  # 保存给路由、错误响应和审计使用。
        started = time.perf_counter()  # 记录单调时钟起点。
        try:  # 执行下游路由。
            response = await call_next(request)  # 等待业务响应。
        except Exception:  # 未处理异常由外层异常处理器转换前先记录。
            LOGGER.exception("unhandled request error", extra={"request_id": request_id})  # 日志保留堆栈但不返回客户端。
            raise  # 交给 FastAPI 异常机制处理。
        latency_seconds = time.perf_counter() - started  # 计算秒级端到端延迟。
        latency_ms = latency_seconds * 1_000.0  # 转换为毫秒用于结构化日志。
        response.headers["X-Request-ID"] = request_id  # 将追踪 ID 返回前端。
        route = request.scope.get("route")  # 读取 FastAPI 匹配后的路由对象。
        route_path = getattr(route, "path", "unmatched")  # 使用模板路径而非具体 question_id，避免高基数指标。
        active_container.metrics.http_requests.labels(method=request.method, route=route_path, status=str(response.status_code)).inc()  # 记录稳定维度请求量。
        active_container.metrics.http_latency.labels(method=request.method, route=route_path).observe(latency_seconds)  # 记录接口延迟。
        LOGGER.info(json.dumps({"event": "http_request", "request_id": request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "latency_ms": round(latency_ms, 2)}, ensure_ascii=False))  # 输出可由日志平台解析的 JSON。
        return response  # 返回带追踪头的响应。

    @app.exception_handler(KeyError)  # 将资源不存在映射为统一 404。
    async def not_found(request: Request, exc: KeyError) -> JSONResponse:  # 处理仓储不存在错误。
        return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": str(exc), "request_id": str(request.state.request_id)})  # 返回稳定机器错误码。

    @app.exception_handler(LookupError)  # 将预测尚未就绪映射为冲突状态。
    async def prediction_pending(request: Request, exc: LookupError) -> JSONResponse:  # 处理异步预测等待。
        return JSONResponse(status_code=409, content={"code": "PREDICTION_PENDING", "message": str(exc), "request_id": str(request.state.request_id)})  # 提醒前端稍后重试。

    @app.exception_handler(ConcurrentUpdateError)  # 将乐观锁失败映射为 409。
    async def concurrent_update(request: Request, exc: ConcurrentUpdateError) -> JSONResponse:  # 处理多人并发覆盖风险。
        return JSONResponse(status_code=409, content={"code": "STALE_TASK_VERSION", "message": str(exc), "request_id": str(request.state.request_id)})  # 要求页面刷新。

    @app.exception_handler(ValueError)  # 将领域参数错误映射为 422。
    async def invalid_business_input(request: Request, exc: ValueError) -> JSONResponse:  # 处理标签不适用等可修正错误。
        return JSONResponse(status_code=422, content={"code": "INVALID_INPUT", "message": str(exc), "request_id": str(request.state.request_id)})  # 返回可展示错误。

    @app.exception_handler(PermissionError)  # 将领取冲突映射为 403。
    async def forbidden_business_action(request: Request, exc: PermissionError) -> JSONResponse:  # 处理服务端权限冲突。
        return JSONResponse(status_code=403, content={"code": "FORBIDDEN", "message": str(exc), "request_id": str(request.state.request_id)})  # 返回稳定权限错误。

    @app.get("/healthz")  # 提供进程存活探针。
    def health() -> dict:  # 返回不访问外部依赖的轻量结果。
        return {"status": "ok"}  # 供容器编排判断进程存活。

    @app.get("/readyz")  # 提供基础就绪探针。
    def ready() -> dict:  # 当前版本在装配成功后视为就绪。
        return {"status": "ready", "model_mode": active_container.settings.historical_model_mode, "rag_mode": active_container.settings.rag_mode, "llm_mode": active_container.settings.llm_mode}  # 暴露非敏感运行模式。

    @app.get("/metrics", include_in_schema=False)  # 暴露 Prometheus 抓取端点且不显示在业务 API 文档。
    def metrics() -> Response:  # 生成当前进程指标快照。
        payload = generate_latest(active_container.metrics.registry)  # 序列化自定义 Registry。
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)  # 返回 Prometheus 文本格式。

    app.include_router(router)  # 注册全部版本化业务路由。
    return app  # 返回配置完成的 FastAPI 应用。


app = create_app()  # 为 `uvicorn app.main:app` 提供标准模块级入口。