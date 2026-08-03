#!/usr/bin/env python3
"""LangGraph 版高级 Agent 控制面实现（可离线运行）。

本文件把框架无关的能力模型（记忆、步骤、路由、反思）挂接到 LangGraph，
并通过显式状态机把“谁负责决策、何时并发、失败如何恢复”落成可审计流程。

核心能力覆盖：
1. 长期记忆检索 + 短期上下文压缩；
2. 支持 ``Send`` 并发扇出的 DAG 执行；
3. 失败后有预算上限的 Replan；
4. 基于白名单与预算控制的多 Agent 路由/Handoff；
5. 有界 evaluator-optimizer Reflection 循环；
6. 基于 ``MemorySaver`` 的按线程 checkpoint。

所有模型调用与外部行为都保持可注入，因此测试可在无网络环境下完成。
"""

from __future__ import annotations

import copy
import operator
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any, Callable, Mapping, Sequence, TypedDict, cast

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xiaolinnote_agent_analysis.examples.agent_capabilities_reference import (
    Evaluation,
    HandoffGuard,
    HybridRouter,
    MemoryRecord,
    PlanStep,
    ReflectionResult,
    ReflectionRound,
    Replanner,
    SQLiteMemoryStore,
    SharedState,
    ShortTermMemory,
    StateEvent,
    StepResult,
    WorkerRegistry,
    check_acceptance,
    deterministic_summary,
    resolve_bindings,
    utc_now_iso,
    validate_plan,
)


JsonObject = dict[str, Any]
GraphConfig = dict[str, dict[str, str]]


def thread_config(thread_id: str) -> GraphConfig:
    """构造 LangGraph checkpoint 线程配置。

    每个 ``thread_id`` 对应一条独立的状态演化轨迹；
    同一图可并行承载多请求而不串状态。
    """

    if not thread_id.strip():
        raise ValueError("thread_id must not be empty")
    return {"configurable": {"thread_id": thread_id}}


def _record_to_dict(record: MemoryRecord) -> JsonObject:
    return asdict(record)


class MemoryGraphState(TypedDict):
    """记忆工作流状态。

    - 输入域：租户、用户、查询词、原始消息窗口；
    - 输出域：召回记忆列表、压缩摘要、可直接喂给模型的上下文。
    """

    tenant_id: str
    user_id: str
    query: str
    top_k: int
    memory_types: list[str]
    max_recent_messages: int
    messages: list[JsonObject]
    summary: str
    working_state: JsonObject
    recalled_memories: list[JsonObject]
    context: list[JsonObject]


class LangGraphMemoryWorkflow:
    """两节点记忆工作流：先召回，再压缩。

    节点顺序：``recall -> compress``。
    目标是把长期记忆与短期上下文拼成稳定、可控长度的模型输入。
    """

    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        summarize: Callable[[Sequence[JsonObject]], str] = deterministic_summary,
        checkpointer: MemorySaver | None = None,
    ) -> None:
        self.store = store
        self.summarize = summarize
        self.checkpointer = checkpointer or MemorySaver()
        builder = StateGraph(MemoryGraphState)
        builder.add_node("recall", self._recall)
        builder.add_node("compress", self._compress)
        builder.add_edge(START, "recall")
        builder.add_edge("recall", "compress")
        builder.add_edge("compress", END)
        self.graph = builder.compile(
            checkpointer=self.checkpointer,
            name="agent-memory-workflow",
        )

    def _recall(self, state: MemoryGraphState) -> JsonObject:
        """按作用域检索长期记忆并写回状态。"""

        records = self.store.search(
            tenant_id=state["tenant_id"],
            user_id=state["user_id"],
            query=state["query"],
            top_k=state["top_k"],
            memory_types=tuple(state["memory_types"]),
        )
        return {"recalled_memories": [_record_to_dict(record) for record in records]}

    def _compress(self, state: MemoryGraphState) -> JsonObject:
        """压缩消息窗口并注入召回记忆。

        这里复用 ``ShortTermMemory`` 的压缩策略，确保摘要与近期原文并存。
        """

        memory = ShortTermMemory(max_recent_messages=state["max_recent_messages"])
        memory.messages = copy.deepcopy(state["messages"])
        memory.summary = state["summary"]
        memory.working_state = copy.deepcopy(state["working_state"])
        memory.compress(self.summarize)

        recalled = state["recalled_memories"]
        context = memory.build_context()
        if recalled:
            context.insert(
                1,
                {
                    "role": "system",
                    "content": "Recalled memories: " + repr(recalled),
                },
            )
        return {
            "messages": memory.messages,
            "summary": memory.summary,
            "working_state": memory.working_state,
            "context": context,
        }

    def invoke(
        self,
        *,
        thread_id: str,
        tenant_id: str,
        user_id: str,
        query: str,
        messages: Sequence[Mapping[str, Any]],
        working_state: Mapping[str, Any] | None = None,
        summary: str = "",
        top_k: int = 5,
        memory_types: Sequence[str] = ("episodic", "semantic", "procedural"),
        max_recent_messages: int = 6,
    ) -> MemoryGraphState:
        """执行记忆图并返回完整状态快照。

        调用方可把返回的 ``context`` 直接用于后续规划或生成。
        """

        if top_k <= 0:
            raise ValueError("top_k must be positive")
        initial: MemoryGraphState = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "query": query,
            "top_k": top_k,
            "memory_types": list(memory_types),
            "max_recent_messages": max_recent_messages,
            "messages": [dict(message) for message in messages],
            "summary": summary,
            "working_state": dict(
                working_state
                or {
                    "goal": "",
                    "confirmed_facts": {},
                    "completed_steps": [],
                    "open_questions": [],
                    "errors": [],
                }
            ),
            "recalled_memories": [],
            "context": [],
        }
        return cast(
            MemoryGraphState,
            self.graph.invoke(initial, config=thread_config(thread_id)),
        )


def _step_to_dict(step: PlanStep) -> JsonObject:
    return asdict(step)


def _step_from_dict(data: Mapping[str, Any]) -> PlanStep:
    """把 JSON 结构恢复为强类型 ``PlanStep``。"""

    return PlanStep(
        step_id=str(data["step_id"]),
        goal=str(data["goal"]),
        worker=str(data["worker"]),
        payload=dict(data.get("payload") or {}),
        depends_on=tuple(data.get("depends_on") or ()),
        success_criteria=dict(data.get("success_criteria") or {"ok": True}),
        on_failure=str(data.get("on_failure") or "fail"),
    )


def _result_from_dict(data: Mapping[str, Any]) -> StepResult:
    """把 JSON 结构恢复为强类型 ``StepResult``。"""

    error = data.get("error")
    acceptance_error = data.get("acceptance_error")
    return StepResult(
        step_id=str(data["step_id"]),
        worker=str(data["worker"]),
        ok=bool(data["ok"]),
        output=dict(data.get("output") or {}),
        error=str(error) if error is not None else None,
        accepted=bool(data.get("accepted", False)),
        acceptance_error=(
            str(acceptance_error) if acceptance_error is not None else None
        ),
    )


def _result_map(results: Sequence[Mapping[str, Any]]) -> dict[str, StepResult]:
    return {
        str(result["step_id"]): _result_from_dict(result)
        for result in results
    }


def _event(event_type: str, **payload: Any) -> JsonObject:
    """统一生成带时间戳的审计事件。"""

    return {
        "timestamp_utc": utc_now_iso(),
        "event_type": event_type,
        "payload": payload,
    }


class DAGGraphState(TypedDict):
    """DAG 编排状态。

    重点字段：
    - ``plan``：当前有效执行计划；
    - ``results``：步骤执行结果（累积）；
    - ``handled_failures``：已被 replan 处理过的失败步骤。
    """

    task_id: str
    goal: str
    plan: list[JsonObject]
    results: Annotated[list[JsonObject], operator.add]
    events: Annotated[list[JsonObject], operator.add]
    status: str
    error: str
    replan_count: int
    max_replans: int
    handled_failures: list[str]


class DAGWorkerInput(TypedDict):
    task_id: str
    goal: str
    step: JsonObject
    results: list[JsonObject]


@dataclass(frozen=True)
class LangGraphDAGResult:
    status: str
    error: str
    replan_count: int
    results: dict[str, StepResult]
    events: tuple[JsonObject, ...]
    graph_state: JsonObject


class LangGraphDAGOrchestrator:
    """DAG 编排器：就绪步骤并发执行 + 有界 Replan。"""

    def __init__(
        self,
        registry: WorkerRegistry,
        *,
        max_replans: int = 1,
        replanner: Replanner | None = None,
        checkpointer: MemorySaver | None = None,
    ) -> None:
        if max_replans < 0:
            raise ValueError("max_replans must be non-negative")
        self.registry = registry
        self.default_max_replans = max_replans
        self.replanner = replanner
        self.checkpointer = checkpointer or MemorySaver()

        builder = StateGraph(DAGGraphState)
        builder.add_node("initialize", self._initialize)
        builder.add_node("schedule", self._schedule)
        builder.add_node("execute_step", self._execute_step)
        builder.add_node("replan", self._replan)
        builder.add_node("finalize", self._finalize)
        builder.add_node("fail", self._fail)
        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "schedule")
        builder.add_conditional_edges(
            "schedule",
            self._route_schedule,
            {"replan": "replan", "finalize": "finalize", "fail": "fail"},
        )
        builder.add_edge("execute_step", "schedule")
        builder.add_edge("replan", "schedule")
        builder.add_edge("finalize", END)
        builder.add_edge("fail", END)
        self.graph = builder.compile(
            checkpointer=self.checkpointer,
            name="agent-dag-orchestrator",
        )

    def _initialize(self, state: DAGGraphState) -> JsonObject:
        """初始化阶段：做计划合法性与 worker 白名单检查。"""

        steps = [_step_from_dict(step) for step in state["plan"]]
        validate_plan(steps)
        unknown_workers = {step.worker for step in steps} - self.registry.names
        if unknown_workers:
            raise ValueError(f"plan references unknown workers: {sorted(unknown_workers)}")
        return {
            "status": "running",
            "error": "",
            "events": [_event("plan.initialized", step_count=len(steps))],
        }

    @staticmethod
    def _schedule(_state: DAGGraphState) -> JsonObject:
        return {}

    def _route_schedule(self, state: DAGGraphState) -> str | list[Send]:
        # Scheduler decisions are purely derived from persisted graph state.
        plan = [_step_from_dict(step) for step in state["plan"]]
        results = _result_map(state["results"])
        handled_failures = set(state["handled_failures"])
        failed = next(
            (
                result
                for result in results.values()
                if not result.accepted and result.step_id not in handled_failures
            ),
            None,
        )
        if failed is not None:
            failed_step = next(step for step in plan if step.step_id == failed.step_id)
            if (
                failed_step.on_failure == "replan"
                and self.replanner is not None
                and state["replan_count"] < state["max_replans"]
            ):
                return "replan"
            return "fail"

        pending = [step for step in plan if step.step_id not in results]
        if not pending:
            return "finalize"
        ready = [
            step
            for step in pending
            if all(
                dependency in results and results[dependency].accepted
                for dependency in step.depends_on
            )
        ]
        if not ready:
            return "fail"
        # ``Send`` fan-out enables parallel execution for all currently-ready steps.
        result_payload = [asdict(result) for result in results.values()]
        return [
            Send(
                "execute_step",
                {
                    "task_id": state["task_id"],
                    "goal": state["goal"],
                    "step": _step_to_dict(step),
                    "results": result_payload,
                },
            )
            for step in ready
        ]

    def _execute_step(self, state: DAGWorkerInput) -> JsonObject:
        """执行单个步骤并返回增量结果。

        注意：这里返回的是“增量状态片段”，由 LangGraph reducer 聚合进总状态。
        """

        step = _step_from_dict(state["step"])
        results = _result_map(state["results"])
        shared = SharedState(state["task_id"], state["goal"])
        shared.results.update(results)
        try:
            # Bindings are resolved against accepted upstream outputs before dispatch.
            payload = resolve_bindings(step.payload, results)
            output = self.registry.execute(step.worker, payload, shared)
            accepted, acceptance_error = check_acceptance(output, step.success_criteria)
            result = StepResult(
                step_id=step.step_id,
                worker=step.worker,
                ok=True,
                output=output,
                accepted=accepted,
                acceptance_error=acceptance_error,
            )
        except Exception as error:
            result = StepResult(
                step_id=step.step_id,
                worker=step.worker,
                ok=False,
                output={},
                error=str(error),
                accepted=False,
            )
        worker_events = [asdict(event) for event in shared.events]
        return {
            "results": [asdict(result)],
            "events": [
                *worker_events,
                _event(
                    "step.completed",
                    step_id=step.step_id,
                    worker=step.worker,
                    accepted=result.accepted,
                )
            ],
        }

    def _replan(self, state: DAGGraphState) -> JsonObject:
        """触发 Replan 并把新步骤补丁拼接到当前计划。

        规则：只能追加“未执行过”的新步骤 ID，防止覆盖已产生副作用的步骤。
        """

        if self.replanner is None:
            raise RuntimeError("replanner is not configured")
        current_plan = [_step_from_dict(step) for step in state["plan"]]
        results = _result_map(state["results"])
        handled_failures = set(state["handled_failures"])
        failed = next(
            result
            for result in results.values()
            if not result.accepted and result.step_id not in handled_failures
        )
        shared = SharedState(state["task_id"], state["goal"])
        shared.results.update(results)
        revised = list(self.replanner(current_plan, shared, failed))
        if not revised:
            raise ValueError("replanner must return at least one recovery step")
        completed_ids = set(results)
        duplicate_ids = {step.step_id for step in revised} & completed_ids
        if duplicate_ids:
            raise ValueError(
                "replanner must return only new step ids: " + repr(sorted(duplicate_ids))
            )
        retained = [step for step in current_plan if step.step_id in completed_ids]
        next_plan = retained + revised
        validate_plan(next_plan)
        unknown_workers = {step.worker for step in revised} - self.registry.names
        if unknown_workers:
            raise ValueError(f"replan references unknown workers: {sorted(unknown_workers)}")
        return {
            "plan": [_step_to_dict(step) for step in next_plan],
            "replan_count": state["replan_count"] + 1,
            "handled_failures": [*state["handled_failures"], failed.step_id],
            "events": [
                _event(
                    "plan.revised",
                    failed_step=failed.step_id,
                    new_steps=[step.step_id for step in revised],
                )
            ],
        }

    @staticmethod
    def _finalize(state: DAGGraphState) -> JsonObject:
        return {
            "status": "completed",
            "events": [_event("run.completed", result_count=len(state["results"]))],
        }

    @staticmethod
    def _failure_message(state: DAGGraphState) -> str:
        """归一化失败原因，优先返回最接近业务语义的错误信息。"""

        results = _result_map(state["results"])
        handled_failures = set(state["handled_failures"])
        failed = next(
            (
                result
                for result in results.values()
                if not result.accepted and result.step_id not in handled_failures
            ),
            None,
        )
        if failed is not None:
            return (
                failed.acceptance_error
                or failed.error
                or f"step failed: {failed.step_id}"
            )
        plan = [_step_from_dict(step) for step in state["plan"]]
        pending = [step.step_id for step in plan if step.step_id not in results]
        return f"no executable steps; blocked: {pending}"

    def _fail(self, state: DAGGraphState) -> JsonObject:
        message = self._failure_message(state)
        return {
            "status": "failed",
            "error": message,
            "events": [_event("run.failed", error=message)],
        }

    def run(
        self,
        *,
        task_id: str,
        goal: str,
        plan: Sequence[PlanStep],
        thread_id: str | None = None,
        max_replans: int | None = None,
    ) -> LangGraphDAGResult:
        """运行 DAG 图直到完成或失败，并返回结构化结果对象。"""

        initial: DAGGraphState = {
            "task_id": task_id,
            "goal": goal,
            "plan": [_step_to_dict(step) for step in plan],
            "results": [],
            "events": [],
            "status": "pending",
            "error": "",
            "replan_count": 0,
            "max_replans": self.default_max_replans if max_replans is None else max_replans,
            "handled_failures": [],
        }
        config = thread_config(thread_id or task_id)
        output = cast(DAGGraphState, self.graph.invoke(initial, config=config))
        result_map = _result_map(output["results"])
        return LangGraphDAGResult(
            status=output["status"],
            error=output["error"],
            replan_count=output["replan_count"],
            results=dict(sorted(result_map.items())),
            events=tuple(output["events"]),
            graph_state=dict(output),
        )


GraphWorkerResult = JsonObject


class RoutingGraphState(TypedDict):
    """多 Agent 路由状态。

    - ``route_history``：用于回路检测与 handoff 预算控制；
    - ``requested_handoff``：worker 请求转交的目标。
    """

    task_id: str
    goal: str
    task_type: str
    context: JsonObject
    payload: JsonObject
    route_history: list[str]
    current_worker: str
    requested_handoff: str
    final_output: JsonObject
    status: str
    error: str
    max_handoffs: int
    events: Annotated[list[JsonObject], operator.add]


@dataclass(frozen=True)
class LangGraphRoutingResult:
    status: str
    output: JsonObject
    route_history: tuple[str, ...]
    error: str
    events: tuple[JsonObject, ...]


class LangGraphMultiAgentRouter:
    """多 Agent 路由器。

    通过 ``Command(goto=...)`` 执行显式跳转，并在每次跳转时应用安全护栏。
    """

    RESERVED_NODES = {"route", "handoff"}

    def __init__(
        self,
        registry: WorkerRegistry,
        router: HybridRouter,
        *,
        max_handoffs: int = 6,
        checkpointer: MemorySaver | None = None,
    ) -> None:
        if self.RESERVED_NODES & registry.names:
            raise ValueError("worker names route and handoff are reserved")
        self.registry = registry
        self.router = router
        self.guard = HandoffGuard(registry.names, max_handoffs=max_handoffs)
        self.checkpointer = checkpointer or MemorySaver()

        builder = StateGraph(RoutingGraphState)
        builder.add_node("route", self._route)
        builder.add_node("handoff", self._handoff)
        for worker_name in sorted(registry.names):
            builder.add_node(worker_name, self._worker_node(worker_name))
        builder.add_edge(START, "route")
        self.graph = builder.compile(
            checkpointer=self.checkpointer,
            name="multi-agent-router",
        )

    @staticmethod
    def _shared_state(state: RoutingGraphState) -> SharedState:
        """把图状态映射为 worker 期望的共享状态视图。"""

        shared = SharedState(state["task_id"], state["goal"])
        shared.route_history = list(state["route_history"])
        return shared

    def _guard_target(self, state: RoutingGraphState, target: str) -> list[str]:
        """在真正跳转前执行 handoff 安全校验并返回更新后的路由历史。"""

        shared = self._shared_state(state)
        self.guard.handoff(shared, target)
        return shared.route_history

    def _route(self, state: RoutingGraphState) -> Command:
        # Hybrid router picks candidate; guard enforces allowlist and handoff budget.
        selected = self.router.route(state["task_type"], state["context"])
        try:
            history = self._guard_target(state, selected)
        except Exception as error:
            return Command(
                update={
                    "status": "failed",
                    "error": str(error),
                    "events": [_event("route.failed", error=str(error))],
                },
                goto=END,
            )
        return Command(
            update={
                "current_worker": selected,
                "route_history": history,
                "events": [_event("route.selected", worker=selected)],
            },
            goto=selected,
        )

    def _worker_node(self, worker_name: str) -> Callable[[RoutingGraphState], Command]:
        """为每个 worker 生成对应图节点函数。"""

        def execute(state: RoutingGraphState) -> Command:
            shared = self._shared_state(state)
            try:
                output = self.registry.execute(worker_name, dict(state["payload"]), shared)
            except Exception as error:
                return Command(
                    update={
                        "status": "failed",
                        "error": str(error),
                        "events": [
                            _event("worker.failed", worker=worker_name, error=str(error))
                        ],
                    },
                    goto=END,
                )
            handoff_to = str(output.get("handoff_to") or "").strip()
            final_output = dict(output.get("output") or output)
            worker_events = [asdict(event) for event in shared.events]
            if handoff_to:
                next_payload = dict(output.get("payload") or state["payload"])
                return Command(
                    update={
                        "payload": next_payload,
                        "requested_handoff": handoff_to,
                        "final_output": final_output,
                        "events": [
                            *worker_events,
                            _event(
                                "worker.handoff_requested",
                                worker=worker_name,
                                target=handoff_to,
                            )
                        ],
                    },
                    goto="handoff",
                )
            return Command(
                update={
                    "final_output": final_output,
                    "status": "completed",
                    "events": [
                        *worker_events,
                        _event("worker.completed", worker=worker_name),
                    ],
                },
                goto=END,
            )

        return execute

    def _handoff(self, state: RoutingGraphState) -> Command:
        """处理 worker 提交的 handoff 请求。"""

        target = state["requested_handoff"]
        try:
            history = self._guard_target(state, target)
        except Exception as error:
            return Command(
                update={
                    "status": "failed",
                    "error": str(error),
                    "events": [_event("handoff.failed", target=target, error=str(error))],
                },
                goto=END,
            )
        return Command(
            update={
                "current_worker": target,
                "requested_handoff": "",
                "route_history": history,
                "events": [_event("handoff.completed", target=target)],
            },
            goto=target,
        )

    def run(
        self,
        *,
        task_id: str,
        goal: str,
        task_type: str,
        payload: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> LangGraphRoutingResult:
        """运行路由图并返回最终输出、路由轨迹与事件。"""

        initial: RoutingGraphState = {
            "task_id": task_id,
            "goal": goal,
            "task_type": task_type,
            "context": dict(context or {}),
            "payload": dict(payload),
            "route_history": [],
            "current_worker": "",
            "requested_handoff": "",
            "final_output": {},
            "status": "pending",
            "error": "",
            "max_handoffs": self.guard.max_handoffs,
            "events": [],
        }
        output = cast(
            RoutingGraphState,
            self.graph.invoke(
                initial,
                config=thread_config(thread_id or task_id),
            ),
        )
        return LangGraphRoutingResult(
            status=output["status"],
            output=output["final_output"],
            route_history=tuple(output["route_history"]),
            error=output["error"],
            events=tuple(output["events"]),
        )


Evaluator = Callable[[str, str, Mapping[str, Any]], Evaluation]
Improver = Callable[[str, str, Evaluation], str]


class ReflectionGraphState(TypedDict):
    """反思工作流状态。"""

    task: str
    output: str
    rubric: JsonObject
    evaluation: JsonObject
    rounds: Annotated[list[JsonObject], operator.add]
    round_index: int
    max_rounds: int
    minimum_improvement: float
    previous_score: float
    current_score: float
    best_score: float
    best_output: str
    status: str


class LangGraphReflectionWorkflow:
    """有界 Reflection 工作流：evaluate -> improve 循环。"""

    def __init__(
        self,
        *,
        evaluator: Evaluator,
        improver: Improver,
        max_rounds: int = 2,
        minimum_improvement: float = 0.01,
        checkpointer: MemorySaver | None = None,
    ) -> None:
        if max_rounds <= 0:
            raise ValueError("max_rounds must be positive")
        self.evaluator = evaluator
        self.improver = improver
        self.default_max_rounds = max_rounds
        self.default_minimum_improvement = minimum_improvement
        self.checkpointer = checkpointer or MemorySaver()

        builder = StateGraph(ReflectionGraphState)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("improve", self._improve)
        builder.add_node("complete", self._complete)
        builder.add_node("stop", self._stop)
        builder.add_edge(START, "evaluate")
        builder.add_conditional_edges(
            "evaluate",
            self._route_evaluation,
            {"complete": "complete", "improve": "improve", "stop": "stop"},
        )
        builder.add_edge("improve", "evaluate")
        builder.add_edge("complete", END)
        builder.add_edge("stop", END)
        self.graph = builder.compile(
            checkpointer=self.checkpointer,
            name="reflection-workflow",
        )

    def _evaluate(self, state: ReflectionGraphState) -> JsonObject:
        """执行评价器并更新当前/最优分数。"""

        evaluation = self.evaluator(state["task"], state["output"], state["rubric"])
        best_score = state["best_score"]
        best_output = state["best_output"]
        if evaluation.score > best_score:
            best_score = evaluation.score
            best_output = state["output"]
        return {
            "evaluation": asdict(evaluation),
            "rounds": [
                {
                    "round_index": state["round_index"],
                    "output": state["output"],
                    "evaluation": asdict(evaluation),
                }
            ],
            "current_score": evaluation.score,
            "best_score": best_score,
            "best_output": best_output,
        }

    @staticmethod
    def _route_evaluation(state: ReflectionGraphState) -> str:
        """根据评价结果决定完成、继续改进或提前停止。"""

        evaluation = state["evaluation"]
        if bool(evaluation.get("passed")):
            return "complete"
        if state["round_index"] >= state["max_rounds"]:
            return "stop"
        if (
            state["previous_score"] >= 0
            and state["current_score"] - state["previous_score"]
            < state["minimum_improvement"]
        ):
            return "stop"
        return "improve"

    def _improve(self, state: ReflectionGraphState) -> JsonObject:
        """调用改进器生成下一轮候选输出。"""

        evaluation_data = state["evaluation"]
        evaluation = Evaluation(
            passed=bool(evaluation_data["passed"]),
            score=float(evaluation_data["score"]),
            issues=tuple(evaluation_data.get("issues") or ()),
        )
        # Improvement output is not final until the next evaluation node validates it.
        improved = self.improver(state["task"], state["output"], evaluation)
        return {
            "output": improved,
            "previous_score": state["current_score"],
            "round_index": state["round_index"] + 1,
        }

    @staticmethod
    def _complete(state: ReflectionGraphState) -> JsonObject:
        return {"status": "completed", "best_output": state["output"]}

    @staticmethod
    def _stop(_state: ReflectionGraphState) -> JsonObject:
        return {"status": "failed"}

    def run(
        self,
        *,
        thread_id: str,
        task: str,
        initial_output: str,
        rubric: Mapping[str, Any],
        max_rounds: int | None = None,
        minimum_improvement: float | None = None,
    ) -> ReflectionResult:
        """执行反思图并返回最佳可验证输出。"""

        initial: ReflectionGraphState = {
            "task": task,
            "output": initial_output,
            "rubric": dict(rubric),
            "evaluation": {},
            "rounds": [],
            "round_index": 1,
            "max_rounds": self.default_max_rounds if max_rounds is None else max_rounds,
            "minimum_improvement": (
                self.default_minimum_improvement
                if minimum_improvement is None
                else minimum_improvement
            ),
            "previous_score": -1.0,
            "current_score": -1.0,
            "best_score": -1.0,
            "best_output": initial_output,
            "status": "running",
        }
        output = cast(
            ReflectionGraphState,
            self.graph.invoke(initial, config=thread_config(thread_id)),
        )
        rounds = tuple(
            ReflectionRound(
                round_index=int(item["round_index"]),
                output=str(item["output"]),
                evaluation=Evaluation(
                    passed=bool(item["evaluation"]["passed"]),
                    score=float(item["evaluation"]["score"]),
                    issues=tuple(item["evaluation"].get("issues") or ()),
                ),
            )
            for item in output["rounds"]
        )
        return ReflectionResult(
            output=output["best_output"],
            passed=output["status"] == "completed",
            rounds=rounds,
        )


def run_demo() -> JsonObject:
    """离线演示：串行跑通 memory/dag/routing/reflection 四段流程。"""

    with SQLiteMemoryStore() as store:
        store.add_memory(
            tenant_id="demo",
            user_id="alice",
            memory_type="episodic",
            content="Alice prefers concise endpoint examples",
            source="demo",
            importance=0.9,
        )
        memory_result = LangGraphMemoryWorkflow(store).invoke(
            thread_id="demo-memory",
            tenant_id="demo",
            user_id="alice",
            query="endpoint examples",
            messages=[
                {"role": "user", "content": "old request"},
                {"role": "assistant", "content": "old response"},
                {"role": "user", "content": "current request"},
            ],
            max_recent_messages=2,
        )

    registry = WorkerRegistry()
    registry.register("source", lambda _payload, _state: {"ok": True, "value": 21})
    registry.register(
        "double",
        lambda payload, _state: {"ok": True, "value": payload["number"] * 2},
    )
    dag_result = LangGraphDAGOrchestrator(registry).run(
        task_id="demo-dag",
        goal="calculate 42",
        plan=[
            PlanStep("source", "produce", "source"),
            PlanStep(
                "double",
                "transform",
                "double",
                payload={"number": "${steps.source.output.value}"},
                depends_on=("source",),
                success_criteria={"value": 42},
            ),
        ],
    )

    routing_registry = WorkerRegistry()
    routing_registry.register(
        "researcher",
        lambda payload, _state: {
            "handoff_to": "writer",
            "payload": {**payload, "facts": "LangGraph uses explicit state graphs"},
        },
    )
    routing_registry.register(
        "writer",
        lambda payload, _state: {"output": {"report": payload["facts"]}},
    )
    routing_result = LangGraphMultiAgentRouter(
        routing_registry,
        HybridRouter(
            allowed_workers=routing_registry.names,
            static_rules={"research": "researcher"},
            fallback_worker="writer",
        ),
    ).run(
        task_id="demo-routing",
        goal="research and write",
        task_type="research",
        payload={"topic": "LangGraph"},
    )

    reflection = LangGraphReflectionWorkflow(
        evaluator=lambda _task, output, _rubric: Evaluation(
            passed="evidence" in output,
            score=1.0 if "evidence" in output else 0.2,
            issues=() if "evidence" in output else ("missing evidence",),
        ),
        improver=lambda _task, output, _evaluation: output + " with evidence",
    ).run(
        thread_id="demo-reflection",
        task="answer",
        initial_output="claim",
        rubric={"requires": "evidence"},
    )

    return {
        "memory_count": len(memory_result["recalled_memories"]),
        "dag_status": dag_result.status,
        "dag_value": dag_result.results["double"].output["value"],
        "routing_status": routing_result.status,
        "routing_history": list(routing_result.route_history),
        "reflection_passed": reflection.passed,
        "reflection_output": reflection.output,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))