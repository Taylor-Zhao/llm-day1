#!/usr/bin/env python3
"""Day36-Day38: unified FastAPI service with auth, rate limit, retry, and observability.

Day36:
- Build one unified service layer for local model inference.

Day37:
- Add auth, rate limit, structured error codes, and retry strategy.

Day38:
- Add request/token/latency/failure-reason logging and metrics endpoint.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from chat_cli import utc_now_iso
from run_day34_unified_inference_api import UnifiedInferenceEngine, build_backend_prompt, resolve_project_path


class ApiError(Exception):
    """Business error with stable code/message/http status for API clients."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        http_status: int,
        retryable: bool = False,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        # 统一错误结构：所有业务异常都带稳定 code，便于前端/调用方做分支处理。
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        self.details = details or {}


@dataclass
class ServiceConfig:
    # Day36-Day38 统一服务配置：
    # - Day36：模型与服务基本参数
    # - Day37：鉴权、限流、重试参数
    # - Day38：日志级别等可观测性参数
    host: str = "0.0.0.0"
    port: int = 8070
    log_level: str = "INFO"

    base_model_id: str = "HuggingFaceTB/SmolLM2-135M-Instruct"
    adapter_dir: str = "outputs/day31_sft_lora/adapter"
    inference_mode: str = "fp32"

    api_keys: set[str] = field(default_factory=lambda: {"dev-api-key"})
    rate_limit_requests: int = 30
    rate_limit_window_sec: int = 60

    retry_attempts: int = 3
    retry_backoff_ms: int = 200

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        # 统一从环境变量读取配置，便于本地调试与部署环境切换。
        keys_raw = os.getenv("DAY36_API_KEYS", "dev-api-key")
        keys = {k.strip() for k in keys_raw.split(",") if k.strip()}
        return cls(
            host=os.getenv("DAY36_HOST", "0.0.0.0"),
            port=int(os.getenv("DAY36_PORT", "8070")),
            log_level=os.getenv("DAY36_LOG_LEVEL", "INFO"),
            base_model_id=os.getenv("DAY36_BASE_MODEL_ID", "HuggingFaceTB/SmolLM2-135M-Instruct"),
            adapter_dir=os.getenv("DAY36_ADAPTER_DIR", "outputs/day31_sft_lora/adapter"),
            inference_mode=os.getenv("DAY36_INFERENCE_MODE", "fp32"),
            api_keys=keys or {"dev-api-key"},
            rate_limit_requests=int(os.getenv("DAY37_RATE_LIMIT_REQUESTS", "30")),
            rate_limit_window_sec=int(os.getenv("DAY37_RATE_LIMIT_WINDOW_SEC", "60")),
            retry_attempts=max(1, int(os.getenv("DAY37_RETRY_ATTEMPTS", "3"))),
            retry_backoff_ms=max(0, int(os.getenv("DAY37_RETRY_BACKOFF_MS", "200"))),
        )


class SlidingWindowRateLimiter:
    """Simple in-memory sliding-window limiter keyed by api key."""

    def __init__(self, max_requests: int, window_sec: int) -> None:
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        # 滑动窗口限流核心逻辑：
        # 1) 清理窗口外请求时间戳
        # 2) 判断当前窗口是否超额
        # 3) 记录本次请求并返回剩余额度
        now = time.time()
        with self._lock:
            q = self._events[key]
            cutoff = now - self.window_sec
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                return False, max(0, self.max_requests - len(q))
            q.append(now)
            return True, max(0, self.max_requests - len(q))


@dataclass
class ServiceMetrics:
    # Day38 监控聚合：请求量、成功率、延迟、token 用量、失败原因。
    total_requests: int = 0
    success_requests: int = 0
    failed_requests: int = 0
    total_latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failure_reasons: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def on_request(self, latency_ms: float, ok: bool, failure_code: str = "") -> None:
        # 请求级指标更新：由 middleware 在每个请求结束时调用。
        with self.lock:
            self.total_requests += 1
            self.total_latency_ms += latency_ms
            if ok:
                self.success_requests += 1
            else:
                self.failed_requests += 1
                if failure_code:
                    self.failure_reasons[failure_code] += 1

    def on_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        # 推理完成后记录 token 统计，用于成本分析和容量评估。
        with self.lock:
            self.prompt_tokens += max(0, prompt_tokens)
            self.completion_tokens += max(0, completion_tokens)

    def snapshot(self) -> dict[str, Any]:
        # 输出监控快照给 /metrics。
        with self.lock:
            avg_latency = self.total_latency_ms / self.total_requests if self.total_requests > 0 else 0.0
            success_rate = self.success_requests / self.total_requests if self.total_requests > 0 else 0.0
            return {
                "total_requests": self.total_requests,
                "success_requests": self.success_requests,
                "failed_requests": self.failed_requests,
                "success_rate": round(success_rate, 4),
                "avg_latency_ms": round(avg_latency, 2),
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "failure_reasons": dict(self.failure_reasons),
            }


class GenerateRequest(BaseModel):
    # 推理请求体：限制字段范围，防止异常参数直接冲击底层模型。
    instruction: str = Field(..., min_length=1, max_length=3000)
    user_input: str = Field(default="", max_length=6000)
    max_new_tokens: int = Field(default=220, ge=1, le=1024)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)


class GenerateResponse(BaseModel):
    # 推理响应体：带 request_id + usage + latency，便于链路追踪与观测。
    request_id: str
    timestamp_utc: str
    model_id: str
    inference_mode: str
    retry_attempts_used: int
    latency_ms: float
    usage: dict[str, int]
    answer: str


def build_error_payload(request_id: str, err: ApiError) -> dict[str, Any]:
    # 统一错误返回结构，保证所有失败响应字段形态一致。
    return {
        "request_id": request_id,
        "timestamp_utc": utc_now_iso(),
        "error": {
            "code": err.code,
            "message": err.message,
            "retryable": err.retryable,
            "details": err.details,
        },
    }


def is_retryable_exception(exc: Exception) -> bool:
    # 简化版可重试判定：基于常见瞬时错误关键词。
    # 若后续接入更稳定的上游，可替换为更精确的错误分类策略。
    transient_keywords = ("timeout", "tempor", "busy", "resource", "try again")
    text = str(exc).lower()
    if isinstance(exc, RuntimeError):
        return any(k in text for k in transient_keywords) or not text
    return any(k in text for k in transient_keywords)


def build_app() -> FastAPI:
    # 应用装配入口：初始化配置、日志、共享状态、路由、中间件和异常处理。
    cfg = ServiceConfig.from_env()

    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger("day36-day38-service")

    app = FastAPI(
        title="Day36-Day38 Unified Service",
        version="0.1.0",
        description="Unified inference service with auth, rate limit, retries, and observability.",
    )

    app.state.cfg = cfg
    # app.state 作为应用级共享存储：避免全局变量污染。
    app.state.metrics = ServiceMetrics()
    app.state.rate_limiter = SlidingWindowRateLimiter(cfg.rate_limit_requests, cfg.rate_limit_window_sec)
    app.state.engine = None
    app.state.logger = logger

    def count_tokens(text: str) -> int:
        # token 计数优先使用真实 tokenizer；失败时退化为长度近似估算。
        if not text:
            return 0
        engine = app.state.engine
        tokenizer = getattr(engine, "tokenizer", None) if engine is not None else None
        if tokenizer is None:
            return max(1, len(text) // 4)
        try:
            return len(tokenizer.encode(text, add_special_tokens=False))
        except Exception:
            return max(1, len(text) // 4)

    def require_api_key(request: Request) -> str:
        # Day37 鉴权：统一从 x-api-key 读取并校验。
        api_key = request.headers.get("x-api-key", "").strip()
        if not api_key:
            raise ApiError(code="AUTH_MISSING", message="Missing x-api-key header", http_status=401)
        if api_key not in cfg.api_keys:
            raise ApiError(code="AUTH_INVALID", message="Invalid api key", http_status=401)
        return api_key

    @app.on_event("startup")
    def on_startup() -> None:
        # 启动时预加载模型，避免首个请求因懒加载出现大延迟。
        adapter_dir_path: Optional[Path] = None
        if cfg.adapter_dir.strip():
            resolved = resolve_project_path(cfg.adapter_dir)
            if resolved.exists():
                adapter_dir_path = resolved
            else:
                logger.warning("adapter_dir does not exist, fallback to base model only: %s", resolved)

        app.state.engine = UnifiedInferenceEngine(
            base_model_id=cfg.base_model_id,
            adapter_dir=adapter_dir_path,
            inference_mode=cfg.inference_mode,
        ).load()

        logger.info(
            "service started: model=%s mode=%s adapter=%s rate_limit=%s/%ss",
            cfg.base_model_id,
            cfg.inference_mode,
            str(adapter_dir_path) if adapter_dir_path else "none",
            cfg.rate_limit_requests,
            cfg.rate_limit_window_sec,
        )

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        # 业务异常处理：按定义好的错误码和状态码返回。
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        payload = build_error_payload(request_id, exc)
        response = JSONResponse(status_code=exc.http_status, content=payload)
        response.headers["x-request-id"] = request_id
        response.headers["x-error-code"] = exc.code
        return response

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # 未捕获异常兜底：避免直接把 Python traceback 暴露给调用方。
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        app.state.logger.exception("unhandled exception request_id=%s", request_id)
        err = ApiError(
            code="INTERNAL_ERROR",
            message="Unexpected server error",
            http_status=500,
            retryable=False,
            details={"type": exc.__class__.__name__},
        )
        payload = build_error_payload(request_id, err)
        response = JSONResponse(status_code=500, content=payload)
        response.headers["x-request-id"] = request_id
        response.headers["x-error-code"] = "INTERNAL_ERROR"
        return response

    @app.middleware("http")
    async def request_middleware(request: Request, call_next):
        # Day38 统一链路埋点：
        # - 生成 request_id
        # - 统计延迟
        # - 累计成功/失败及失败原因
        # - 输出结构化日志
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000.0

        ok = response.status_code < 400
        failure_code = response.headers.get("x-error-code", "") if not ok else ""
        app.state.metrics.on_request(latency_ms=latency_ms, ok=ok, failure_code=failure_code)

        response.headers["x-request-id"] = request_id
        app.state.logger.info(
            "request_id=%s method=%s path=%s status=%s latency_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            latency_ms,
        )
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        # 基础健康检查：用于探活与最小可用性判断。
        return {
            "status": "ok",
            "timestamp_utc": utc_now_iso(),
            "model_id": cfg.base_model_id,
            "inference_mode": cfg.inference_mode,
        }

    @app.get("/metrics")
    def metrics() -> dict[str, Any]:
        # 监控接口：输出当前聚合指标快照。
        return {
            "timestamp_utc": utc_now_iso(),
            "service": "day36-day38",
            "metrics": app.state.metrics.snapshot(),
        }

    @app.post("/v1/inference", response_model=GenerateResponse)
    def inference(request: Request, payload: GenerateRequest) -> GenerateResponse:
        # 主业务接口执行顺序：
        # 1) 鉴权
        # 2) 限流
        # 3) 构造 prompt
        # 4) 调模型（含重试）
        # 5) 记录 token + 返回响应
        api_key = require_api_key(request)

        allowed, remaining = app.state.rate_limiter.allow(api_key)
        if not allowed:
            # 限流命中时返回 429，并附带窗口参数与剩余额度信息。
            raise ApiError(
                code="RATE_LIMITED",
                message="Too many requests. Please retry later.",
                http_status=429,
                retryable=True,
                details={
                    "limit": cfg.rate_limit_requests,
                    "window_sec": cfg.rate_limit_window_sec,
                    "remaining": remaining,
                },
            )

        prompt = build_backend_prompt(payload.instruction, payload.user_input)
        prompt_tokens = count_tokens(prompt)

        engine: UnifiedInferenceEngine = app.state.engine

        def run_once() -> str:
            # 单次模型调用封装，便于重试循环复用。
            return engine.generate(
                [prompt],
                max_new_tokens=payload.max_new_tokens,
                temperature=payload.temperature,
                top_p=payload.top_p,
                batch_size=1,
            )[0]

        attempts_used = 0
        start = time.perf_counter()
        last_error: Optional[Exception] = None

        for attempt in range(1, cfg.retry_attempts + 1):
            attempts_used = attempt
            try:
                answer = run_once()
                break
            except Exception as exc:  # keep broad to wrap inference issues into stable API error
                last_error = exc
                should_retry = attempt < cfg.retry_attempts and is_retryable_exception(exc)
                if not should_retry:
                    # 达到不可重试条件，按稳定错误码返回给调用方。
                    raise ApiError(
                        code="UPSTREAM_INFERENCE_FAILED",
                        message="Inference failed",
                        http_status=502,
                        retryable=False,
                        details={"cause": str(exc), "attempt": attempt},
                    ) from exc
                backoff = (cfg.retry_backoff_ms * (2 ** (attempt - 1))) / 1000.0
                # 指数退避：attempt 越大等待越久，减少瞬时故障放大效应。
                time.sleep(backoff)
        else:
            raise ApiError(
                code="UPSTREAM_INFERENCE_FAILED",
                message="Inference failed after retries",
                http_status=502,
                retryable=False,
                details={"cause": str(last_error) if last_error else "unknown"},
            )

        latency_ms = (time.perf_counter() - start) * 1000.0
        completion_tokens = count_tokens(answer)
        app.state.metrics.on_tokens(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)

        return GenerateResponse(
            request_id=getattr(request.state, "request_id", str(uuid.uuid4())),
            timestamp_utc=utc_now_iso(),
            model_id=cfg.base_model_id,
            inference_mode=cfg.inference_mode,
            retry_attempts_used=attempts_used,
            latency_ms=round(latency_ms, 2),
            usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            answer=answer,
        )

    return app


app = build_app()


if __name__ == "__main__":
    # 允许直接 python 启动，便于本地开发与调试。
    try:
        import uvicorn
    except Exception as exc:
        raise RuntimeError("Missing uvicorn. Install with: pip install -r requirements_day36_day38.txt") from exc

    cfg = app.state.cfg
    uvicorn.run("run_day36_day38_unified_service:app", host=cfg.host, port=cfg.port, reload=False)
