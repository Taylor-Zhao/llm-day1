#!/usr/bin/env python3
"""Airflow + LangGraph 生产化示例。

设计分层：
1. Airflow 负责外层调度、重试、可观测性；
2. LangGraph 负责内层 Agent 控制流（记忆、DAG、路由、反思）。

本文件强调“边界可验证”：
- 每个 task 的输入输出都通过 Pydantic schema 严格校验；
- 本地无 Airflow 时也能跑 ``run_local_demo_pipeline`` 做离线回归。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import importlib
import importlib.util
from pathlib import Path
import sys
from typing import Any, Mapping, Optional, Type, TypeVar

from pydantic import BaseModel, Field, ValidationError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xiaolinnote_agent_analysis.examples.agent_capabilities_langgraph import (
    LangGraphDAGOrchestrator,
    LangGraphMemoryWorkflow,
    LangGraphMultiAgentRouter,
    LangGraphReflectionWorkflow,
)
from xiaolinnote_agent_analysis.examples.agent_capabilities_reference import (
    Evaluation,
    HybridRouter,
    PlanStep,
    SQLiteMemoryStore,
    WorkerRegistry,
)

JsonObject = dict[str, Any]
ModelType = TypeVar("ModelType", bound=BaseModel)

AIRFLOW_AVAILABLE = False
AIRFLOW_IMPORT_ERROR: Exception | None = None
dag = None
task = None

try:
    decorators_spec = importlib.util.find_spec("airflow.decorators")
    if decorators_spec is not None:
        decorators = importlib.import_module("airflow.decorators")
        dag = getattr(decorators, "dag")
        task = getattr(decorators, "task")
        AIRFLOW_AVAILABLE = True
    else:
        AIRFLOW_IMPORT_ERROR = ModuleNotFoundError("No module named 'airflow'")
except Exception as error:  # pragma: no cover - depends on local environment
    AIRFLOW_IMPORT_ERROR = error


class StrictModel(BaseModel):
    """严格模型基类：拒绝未知字段，防止 XCom 漂移。"""

    class Config:
        extra = "forbid"


def _model_validate(model_type: Type[ModelType], data: Mapping[str, Any]) -> ModelType:
    """兼容 Pydantic v1/v2 的校验入口。"""

    validator = getattr(model_type, "model_validate", None)
    if callable(validator):
        return validator(data)
    return model_type.parse_obj(data)


def _model_dump(model: BaseModel) -> JsonObject:
    """兼容 Pydantic v1/v2 的序列化入口。"""

    dumper = getattr(model, "model_dump", None)
    if callable(dumper):
        return dumper()
    return model.dict()


class MessageSchema(StrictModel):
    """消息结构：供 memory 上下文拼装与跨 task 传输。"""

    role: str
    content: str


class EventSchema(StrictModel):
    """审计事件结构：记录阶段行为与关键 payload。"""

    timestamp_utc: str
    event_type: str
    payload: JsonObject


class StepResultSchema(StrictModel):
    """步骤结果结构：技术成功与业务验收显式分离。"""

    step_id: str
    worker: str
    ok: bool
    output: JsonObject
    error: Optional[str] = None
    accepted: bool = False
    acceptance_error: Optional[str] = None


class MemoryStageInput(StrictModel):
    """Memory 阶段输入。"""

    thread_id: str = Field(default="airflow-memory", min_length=1)


class MemoryStageOutput(StrictModel):
    """Memory 阶段输出。"""

    tenant_id: str
    user_id: str
    summary: str
    context: list[MessageSchema]
    recalled_memories: list[JsonObject]


class DAGStageInput(StrictModel):
    """DAG 阶段输入（依赖 Memory 输出）。"""

    thread_id: str = Field(default="airflow-dag", min_length=1)
    memory_state: MemoryStageOutput


class DAGStageOutput(StrictModel):
    """DAG 阶段输出。"""

    status: str
    error: str
    replan_count: int
    results: dict[str, StepResultSchema]
    events: list[EventSchema]


class RoutingStageInput(StrictModel):
    """Routing 阶段输入（依赖 DAG 输出）。"""

    dag_state: DAGStageOutput


class RoutingStageOutput(StrictModel):
    """Routing 阶段输出。"""

    status: str
    error: str
    route_history: list[str]
    output: JsonObject
    events: list[EventSchema]


class ReflectionRoundSchema(StrictModel):
    """单轮反思结果结构。"""

    round_index: int
    output: str
    evaluation: JsonObject


class ReflectionStageInput(StrictModel):
    """Reflection 阶段输入（依赖 Routing 输出）。"""

    routing_state: RoutingStageOutput


class ReflectionStageOutput(StrictModel):
    """Reflection 阶段输出。"""

    passed: bool
    output: str
    round_count: int
    rounds: list[ReflectionRoundSchema]


class PipelineOutput(StrictModel):
    """本地串行管道总输出。"""

    memory: MemoryStageOutput
    dag: DAGStageOutput
    routing: RoutingStageOutput
    reflection: ReflectionStageOutput


def _validate_or_raise(model_type: Type[ModelType], data: Mapping[str, Any]) -> ModelType:
    """统一包装校验错误，便于 Airflow 日志定位失败边界。"""

    try:
        return _model_validate(model_type, data)
    except ValidationError as error:
        raise ValueError(f"invalid payload for {model_type.__name__}: {error}") from error


def run_memory_stage(*, thread_id: str = "airflow-memory") -> JsonObject:
    """执行记忆阶段。

    流程：输入校验 -> 种子记忆写入 -> LangGraph memory workflow -> 输出校验。
    """

    validated_input = _validate_or_raise(MemoryStageInput, {"thread_id": thread_id})

    with SQLiteMemoryStore() as store:
        # Seed memory keeps the demo deterministic and stable for runtime validation.
        store.add_memory(
            tenant_id="demo",
            user_id="alice",
            memory_type="episodic",
            content="Alice prefers concise endpoint examples",
            source="seed",
            importance=0.9,
        )
        store.add_memory(
            tenant_id="demo",
            user_id="alice",
            memory_type="semantic",
            content="The orders API endpoint is /orders/{id}",
            source="seed",
            importance=0.8,
        )

        state = LangGraphMemoryWorkflow(store).invoke(
            thread_id=validated_input.thread_id,
            tenant_id="demo",
            user_id="alice",
            query="endpoint example",
            messages=[
                {"role": "user", "content": "Please summarize the endpoint."},
                {"role": "assistant", "content": "I will look up prior notes."},
                {"role": "user", "content": "Need concise output."},
            ],
            max_recent_messages=2,
            working_state={"goal": "summarize endpoint", "completed_steps": []},
        )

    output = _validate_or_raise(
        MemoryStageOutput,
        {
        "tenant_id": state["tenant_id"],
        "user_id": state["user_id"],
        "summary": state["summary"],
        "context": state["context"],
        "recalled_memories": state["recalled_memories"],
        },
    )
    return _model_dump(output)


def run_dag_stage(
    memory_state: Mapping[str, Any],
    *,
    thread_id: str = "airflow-dag",
) -> JsonObject:
    """执行 DAG 阶段。

    流程：输入校验 -> 组装 worker/plan -> LangGraph DAG run -> 输出校验。
    """

    # Validate upstream XCom payload before reading any nested fields.
    validated_input = _validate_or_raise(
        DAGStageInput,
        {
            "thread_id": thread_id,
            "memory_state": dict(memory_state),
        },
    )

    registry = WorkerRegistry()
    registry.register(
        "source",
        lambda _payload, _state: {
            "ok": True,
            "endpoint": "/orders/1001",
            "memory_count": len(validated_input.memory_state.recalled_memories),
        },
    )
    registry.register(
        "writer",
        lambda payload, _state: {
            "ok": True,
            "summary": (
                f"Use endpoint {payload['endpoint']} "
                f"(memories={payload['memory_count']})"
            ),
        },
    )

    plan = [
        PlanStep(step_id="source", goal="fetch endpoint", worker="source"),
        PlanStep(
            step_id="write",
            goal="write summary",
            worker="writer",
            payload={
                "endpoint": "${steps.source.output.endpoint}",
                "memory_count": "${steps.source.output.memory_count}",
            },
            depends_on=("source",),
            success_criteria={"ok": True},
        ),
    ]
    result = LangGraphDAGOrchestrator(registry, max_replans=1).run(
        task_id="airflow-dag-task",
        goal="summarize endpoint",
        plan=plan,
        thread_id=validated_input.thread_id,
    )

    output = _validate_or_raise(
        DAGStageOutput,
        {
        "status": result.status,
        "error": result.error,
        "replan_count": result.replan_count,
        "results": {step_id: asdict(step_result) for step_id, step_result in result.results.items()},
        "events": list(result.events),
        },
    )
    return _model_dump(output)


def run_routing_stage(dag_state: Mapping[str, Any]) -> JsonObject:
    """执行路由阶段。

    从 DAG 输出提取 draft，交给 researcher->writer 协作链条。
    """

    # Parsing here ensures failed/partial DAG payloads are rejected deterministically.
    validated_input = _validate_or_raise(
        RoutingStageInput,
        {"dag_state": dict(dag_state)},
    )

    write_result = _model_dump(validated_input.dag_state.results.get("write", StepResultSchema(
        step_id="write",
        worker="writer",
        ok=False,
        output={},
        accepted=False,
    )))
    draft = dict(write_result.get("output", {})).get("summary", "")

    registry = WorkerRegistry()
    registry.register(
        "researcher",
        lambda payload, _state: {
            "handoff_to": "writer",
            "payload": {**payload, "facts": "LangGraph uses explicit state graphs"},
        },
    )
    registry.register(
        "writer",
        lambda payload, _state: {
            "output": {
                "draft": payload.get("draft", ""),
                "report": payload["facts"],
            }
        },
    )

    router = HybridRouter(
        allowed_workers=registry.names,
        static_rules={"research": "researcher"},
        fallback_worker="writer",
    )

    result = LangGraphMultiAgentRouter(registry, router).run(
        task_id="airflow-routing-task",
        goal="research and write",
        task_type="research",
        payload={"topic": "LangGraph", "draft": draft},
    )
    output = _validate_or_raise(
        RoutingStageOutput,
        {
        "status": result.status,
        "error": result.error,
        "route_history": list(result.route_history),
        "output": result.output,
        "events": list(result.events),
        },
    )
    return _model_dump(output)


def run_reflection_stage(routing_state: Mapping[str, Any]) -> JsonObject:
    """执行反思阶段，保证最终输出满足证据约束。"""

    # Reflection stage receives validated router output to avoid silent key drift.
    validated_input = _validate_or_raise(
        ReflectionStageInput,
        {"routing_state": dict(routing_state)},
    )

    report = str(validated_input.routing_state.output.get("report", "claim"))

    workflow = LangGraphReflectionWorkflow(
        evaluator=lambda _task, output, _rubric: Evaluation(
            passed="evidence" in output,
            score=1.0 if "evidence" in output else 0.2,
            issues=() if "evidence" in output else ("missing evidence",),
        ),
        improver=lambda _task, output, _evaluation: output + " with evidence",
        max_rounds=2,
    )
    result = workflow.run(
        thread_id="airflow-reflection",
        task="final answer",
        initial_output=report,
        rubric={"requires": "evidence"},
    )

    output = _validate_or_raise(
        ReflectionStageOutput,
        {
        "passed": result.passed,
        "output": result.output,
        "round_count": len(result.rounds),
        "rounds": [
            {
                "round_index": item.round_index,
                "output": item.output,
                "evaluation": asdict(item.evaluation),
            }
            for item in result.rounds
        ],
        },
    )
    return _model_dump(output)


def run_local_demo_pipeline() -> JsonObject:
    """离线串行执行完整管道（不依赖 Airflow 运行时）。"""

    memory_state = run_memory_stage(thread_id="local-memory")
    dag_state = run_dag_stage(memory_state, thread_id="local-dag")
    routing_state = run_routing_stage(dag_state)
    reflection_state = run_reflection_stage(routing_state)
    output = _validate_or_raise(
        PipelineOutput,
        {
        "memory": memory_state,
        "dag": dag_state,
        "routing": routing_state,
        "reflection": reflection_state,
        },
    )
    return _model_dump(output)


def create_airflow_dag() -> Any:
    """创建可被调度器加载的 Airflow DAG。

    注意：task 本身只做“薄封装调用”，业务逻辑全部下沉到阶段函数。
    """

    if not AIRFLOW_AVAILABLE:
        message = "Airflow is not available in this environment"
        if AIRFLOW_IMPORT_ERROR is not None:
            message += f": {AIRFLOW_IMPORT_ERROR}"
        raise RuntimeError(message)

    @dag(
        dag_id="agent_capabilities_airflow_langgraph",
        schedule=None,
        start_date=datetime(2024, 1, 1),
        catchup=False,
        default_args={"owner": "agent-team", "retries": 1, "retry_delay": timedelta(seconds=5)},
        tags=["agent", "langgraph", "airflow"],
        doc_md="""
        Outer orchestration in Airflow; inner control plane in LangGraph.

        Task chain:
        1) memory recall/compression
        2) DAG execution
        3) route/handoff
        4) reflection
        """,
    )
    def _dag() -> Any:
        # Each Airflow task delegates to one validated stage function.
        @task(task_id="seed_and_recall")
        def seed_and_recall() -> JsonObject:
            # Stage functions enforce schemas so malformed XCom payloads fail fast.
            return run_memory_stage(thread_id="airflow-memory")

        @task(task_id="execute_langgraph_dag", retries=2, retry_delay=timedelta(seconds=3))
        def execute_langgraph_dag(memory_state: JsonObject) -> JsonObject:
            return run_dag_stage(memory_state, thread_id="airflow-dag")

        @task(task_id="route_and_handoff")
        def route_and_handoff(dag_state: JsonObject) -> JsonObject:
            return run_routing_stage(dag_state)

        @task(task_id="reflect_output")
        def reflect_output(routing_state: JsonObject) -> JsonObject:
            return run_reflection_stage(routing_state)

        reflect_output(route_and_handoff(execute_langgraph_dag(seed_and_recall())))

    return _dag()


agent_capabilities_airflow_langgraph_dag = (
    create_airflow_dag() if AIRFLOW_AVAILABLE else None
)


if __name__ == "__main__":
    import json

    print(json.dumps(run_local_demo_pipeline(), ensure_ascii=False, indent=2))
