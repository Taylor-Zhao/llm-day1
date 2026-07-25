#!/usr/bin/env python3
"""小林笔记第 8-16 篇 Agent 能力的可运行参考实现。

本模块只使用 Python 标准库，并通过可注入的回调函数隔离 LLM、工具和评价器，
因此无需网络即可演示和测试以下控制面能力：

- 基于 SQLite 的实体/情节记忆、作用域检索和遗忘；
- 带结构化工作状态的短期消息压缩；
- 支持依赖、结果绑定、验收标准和 checkpoint 的 DAG 执行；
- Retry 与 Replan 的职责边界；
- Worker 注册表、追加式共享状态、混合路由和 Handoff 防护；
- 结构化的“生成 → 评价 → 改进”Reflection 循环。

这是用于学习控制逻辑的参考代码，不是可以直接部署的生产级 Agent 平台。
"""

from __future__ import annotations

import copy
import json
import math
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence


JsonObject = dict[str, Any]


def utc_now_iso() -> str:
    """返回带 UTC 时区的 ISO 8601 时间，便于持久化和跨系统比较。"""

    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    """将文本转换为去重词元集合，供离线词法检索使用。"""

    return set(re.findall(r"\w+", text.lower(), flags=re.UNICODE))


def _lexical_similarity(left: str, right: str) -> float:
    """计算两个词元集合的余弦形式相似度，取值范围为 0 到 1。"""

    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / math.sqrt(
        len(left_tokens) * len(right_tokens)
    )


def _freshness(created_at: str, half_life_days: float = 30.0) -> float:
    """按半衰期计算记忆新鲜度，记忆越旧，分数越接近 0。"""

    created = datetime.fromisoformat(created_at)
    age_seconds = max(
        0.0,
        (datetime.now(timezone.utc) - created).total_seconds(),
    )
    age_days = age_seconds / 86_400
    return math.pow(0.5, age_days / half_life_days)


@dataclass(frozen=True)
class MemoryRecord:
    """从记忆库返回的不可变记录；score 是本次查询产生的排序分。"""

    memory_id: int
    tenant_id: str
    user_id: str
    memory_type: str
    content: str
    source: str
    importance: float
    created_at: str
    valid_until: str | None
    status: str
    metadata: JsonObject
    score: float = 0.0


class SQLiteMemoryStore:
    """严格按租户和用户隔离的小型混合记忆库。

    实体事实使用精确 key 并原地更新；情节、语义和程序记忆采用追加写入。
    当前使用确定性的词法相似度，生产实现可以替换为 Embedding 检索，同时保留
    这里演示的作用域隔离、有效期和软删除规则。
    """

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        # DAG Worker 可能并发访问记忆库，因此允许跨线程使用连接；具体操作仍由锁串行化。
        self.connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._create_schema()

    def _create_schema(self) -> None:
        """创建记忆表，以及“每个用户的同名实体事实唯一”约束。"""

        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    entity_key TEXT,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    importance REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    valid_until TEXT,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS entity_identity
                ON memories (tenant_id, user_id, memory_type, entity_key)
                WHERE memory_type = 'entity' AND status = 'active'
                """
            )

    def close(self) -> None:
        """关闭底层 SQLite 连接。"""

        self.connection.close()

    def __enter__(self) -> "SQLiteMemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add_memory(
        self,
        *,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        content: str,
        source: str,
        importance: float = 0.5,
        valid_until: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        """追加一条非实体记忆，并返回数据库生成的 memory_id。"""

        if memory_type not in {"episodic", "semantic", "procedural"}:
            raise ValueError("memory_type must be episodic, semantic, or procedural")
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO memories (
                    tenant_id, user_id, memory_type, entity_key, content, source,
                    importance, created_at, valid_until, status, metadata_json
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    tenant_id,
                    user_id,
                    memory_type,
                    content,
                    source,
                    importance,
                    utc_now_iso(),
                    valid_until,
                    json.dumps(dict(metadata or {}), ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def upsert_fact(
        self,
        *,
        tenant_id: str,
        user_id: str,
        key: str,
        value: str,
        source: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        """按租户、用户和 key 新增或更新实体事实。"""

        now = utc_now_iso()
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO memories (
                    tenant_id, user_id, memory_type, entity_key, content, source,
                    importance, created_at, valid_until, status, metadata_json
                ) VALUES (?, ?, 'entity', ?, ?, ?, 1.0, ?, NULL, 'active', ?)
                ON CONFLICT (tenant_id, user_id, memory_type, entity_key)
                WHERE memory_type = 'entity' AND status = 'active'
                DO UPDATE SET
                    content = excluded.content,
                    source = excluded.source,
                    created_at = excluded.created_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    tenant_id,
                    user_id,
                    key,
                    value,
                    source,
                    now,
                    json.dumps(dict(metadata or {}), ensure_ascii=False),
                ),
            )
            row = self.connection.execute(
                """
                SELECT memory_id FROM memories
                WHERE tenant_id = ? AND user_id = ? AND memory_type = 'entity'
                  AND entity_key = ? AND status = 'active'
                """,
                (tenant_id, user_id, key),
            ).fetchone()
            if row is None:
                raise RuntimeError("entity upsert did not create a record")
            return int(row["memory_id"])

    def get_facts(self, *, tenant_id: str, user_id: str) -> dict[str, str]:
        """返回指定用户所有有效实体事实，不跨越租户或用户边界。"""

        rows = self.connection.execute(
            """
            SELECT entity_key, content FROM memories
            WHERE tenant_id = ? AND user_id = ? AND memory_type = 'entity'
              AND status = 'active'
              AND (valid_until IS NULL OR valid_until > ?)
            ORDER BY entity_key
            """,
            (tenant_id, user_id, utc_now_iso()),
        ).fetchall()
        return {str(row["entity_key"]): str(row["content"]) for row in rows}

    def search(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
        top_k: int = 5,
        memory_types: Sequence[str] = ("episodic", "semantic", "procedural"),
    ) -> list[MemoryRecord]:
        """检索有效记忆，并按相关度、重要度和新鲜度的加权分排序。"""

        if top_k <= 0:
            raise ValueError("top_k must be positive")
        allowed_types = tuple(memory_types)
        if not allowed_types:
            return []
        placeholders = ",".join("?" for _ in allowed_types)
        rows = self.connection.execute(
            f"""
            SELECT * FROM memories
            WHERE tenant_id = ? AND user_id = ? AND status = 'active'
              AND memory_type IN ({placeholders})
              AND (valid_until IS NULL OR valid_until > ?)
            """,
            (tenant_id, user_id, *allowed_types, utc_now_iso()),
        ).fetchall()
        ranked: list[MemoryRecord] = []
        for row in rows:
            semantic_score = _lexical_similarity(query, str(row["content"]))
            # 权重是教学示例：相关度主导排序，重要度和新鲜度用于修正同类结果。
            score = (
                0.65 * semantic_score
                + 0.20 * float(row["importance"])
                + 0.15 * _freshness(str(row["created_at"]))
            )
            ranked.append(self._row_to_record(row, score))
        ranked.sort(key=lambda item: (-item.score, -item.importance, item.memory_id))
        return ranked[:top_k]

    def forget(self, *, tenant_id: str, user_id: str, memory_id: int) -> bool:
        """在所属作用域内软删除记忆；无权访问或记录不存在时返回 False。"""

        with self.lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE memories SET status = 'forgotten'
                WHERE memory_id = ? AND tenant_id = ? AND user_id = ?
                  AND status = 'active'
                """,
                (memory_id, tenant_id, user_id),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _row_to_record(row: sqlite3.Row, score: float) -> MemoryRecord:
        """把 SQLite Row 转换为对调用方更稳定的领域对象。"""

        return MemoryRecord(
            memory_id=int(row["memory_id"]),
            tenant_id=str(row["tenant_id"]),
            user_id=str(row["user_id"]),
            memory_type=str(row["memory_type"]),
            content=str(row["content"]),
            source=str(row["source"]),
            importance=float(row["importance"]),
            created_at=str(row["created_at"]),
            valid_until=row["valid_until"],
            status=str(row["status"]),
            metadata=json.loads(str(row["metadata_json"])),
            score=score,
        )


SummaryFunction = Callable[[Sequence[JsonObject]], str]


class ShortTermMemory:
    """由“近期消息窗口 + 可替换摘要 + 结构化状态”组成的短期记忆。"""

    def __init__(self, max_recent_messages: int = 6) -> None:
        if max_recent_messages <= 0:
            raise ValueError("max_recent_messages must be positive")
        self.max_recent_messages = max_recent_messages
        self.messages: list[JsonObject] = []
        self.summary = ""
        self.working_state: JsonObject = {
            "goal": "",
            "confirmed_facts": {},
            "completed_steps": [],
            "open_questions": [],
            "errors": [],
        }

    def add(self, role: str, content: str, **fields: Any) -> None:
        """追加一条兼容 Chat Completion 结构的消息。"""

        self.messages.append({"role": role, "content": content, **fields})

    def update_state(self, **changes: Any) -> None:
        """更新不会被摘要丢弃的结构化工作状态。"""

        self.working_state.update(changes)

    def compress(self, summarize: SummaryFunction) -> None:
        """摘要超出窗口的旧消息，只保留最近的原始消息。"""

        overflow_count = len(self.messages) - self.max_recent_messages
        if overflow_count <= 0:
            return
        older_messages = self.messages[:overflow_count]
        if self.summary:
            # 把旧摘要参与下一次压缩，避免多次滑窗后完全遗忘更早的关键信息。
            older_messages = [
                {"role": "system", "content": f"Previous summary: {self.summary}"},
                *older_messages,
            ]
        self.summary = summarize(older_messages)
        self.messages = self.messages[overflow_count:]

    def build_context(self) -> list[JsonObject]:
        """按“工作状态 → 历史摘要 → 近期原文”的顺序构建模型上下文。"""

        context = [
            {
                "role": "system",
                "content": "Working state: "
                + json.dumps(self.working_state, ensure_ascii=False, sort_keys=True),
            }
        ]
        if self.summary:
            context.append({"role": "system", "content": f"History summary: {self.summary}"})
        context.extend(copy.deepcopy(self.messages))
        return context


@dataclass(frozen=True)
class PlanStep:
    """一个可调度步骤，包含依赖、输入、验收条件和失败策略。"""

    step_id: str
    goal: str
    worker: str
    payload: JsonObject = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    success_criteria: JsonObject = field(default_factory=lambda: {"ok": True})
    on_failure: str = "fail"


@dataclass(frozen=True)
class StepResult:
    """Worker 执行结果和验收结果；执行成功不代表业务验收通过。"""

    step_id: str
    worker: str
    ok: bool
    output: JsonObject
    error: str | None = None
    accepted: bool = False
    acceptance_error: str | None = None


@dataclass(frozen=True)
class StateEvent:
    """共享状态中的追加式审计事件。"""

    sequence: int
    timestamp_utc: str
    task_id: str
    agent_id: str
    event_type: str
    payload: JsonObject


class SharedState:
    """Worker 和 Orchestrator 共用的线程安全、追加式任务状态。"""

    def __init__(self, task_id: str, goal: str) -> None:
        self.task_id = task_id
        self.goal = goal
        self.events: list[StateEvent] = []
        self.results: dict[str, StepResult] = {}
        self.route_history: list[str] = []
        self.lock = threading.RLock()

    def append_event(self, agent_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        """以单调递增序号追加事件，保留完整执行轨迹。"""

        with self.lock:
            self.events.append(
                StateEvent(
                    sequence=len(self.events) + 1,
                    timestamp_utc=utc_now_iso(),
                    task_id=self.task_id,
                    agent_id=agent_id,
                    event_type=event_type,
                    payload=dict(payload),
                )
            )

    def save_result(self, result: StepResult) -> None:
        """保存一次且仅一次的步骤结果，并同步写入完成事件。"""

        with self.lock:
            if result.step_id in self.results:
                raise ValueError(f"step result already exists: {result.step_id}")
            self.results[result.step_id] = result
            self.append_event(result.worker, "step.completed", asdict(result))

    def snapshot(self) -> JsonObject:
        """生成可 JSON 序列化的状态快照，供 checkpoint 和审计使用。"""

        with self.lock:
            return {
                "task_id": self.task_id,
                "goal": self.goal,
                "results": {key: asdict(value) for key, value in self.results.items()},
                "route_history": list(self.route_history),
                "events": [asdict(event) for event in self.events],
            }


class Worker(Protocol):
    """所有 Worker 都必须满足的最小调用协议。"""

    def __call__(self, payload: JsonObject, state: SharedState) -> JsonObject: ...


class WorkerRegistry:
    """Worker 名称到实现的显式白名单，避免路由器调用任意函数。"""

    def __init__(self) -> None:
        self.workers: dict[str, Worker] = {}

    def register(self, name: str, worker: Worker) -> None:
        """注册 Worker；重复名称视为配置错误。"""

        if name in self.workers:
            raise ValueError(f"worker already registered: {name}")
        self.workers[name] = worker

    def execute(self, name: str, payload: JsonObject, state: SharedState) -> JsonObject:
        """按白名单名称执行 Worker，拒绝未知目标。"""

        worker = self.workers.get(name)
        if worker is None:
            raise ValueError(f"unknown worker: {name}")
        return worker(payload, state)

    @property
    def names(self) -> set[str]:
        """返回注册名称副本，供 Router 构造允许列表。"""

        return set(self.workers)


DynamicRouter = Callable[[JsonObject, set[str]], str]


class HybridRouter:
    """静态规则优先，未命中时才调用动态路由，并校验动态结果。"""

    def __init__(
        self,
        *,
        allowed_workers: Iterable[str],
        static_rules: Mapping[str, str],
        dynamic_router: DynamicRouter | None = None,
        fallback_worker: str,
    ) -> None:
        self.allowed_workers = set(allowed_workers)
        self.static_rules = dict(static_rules)
        self.dynamic_router = dynamic_router
        self.fallback_worker = fallback_worker
        if fallback_worker not in self.allowed_workers:
            raise ValueError("fallback_worker must be allowed")
        unknown = set(self.static_rules.values()) - self.allowed_workers
        if unknown:
            raise ValueError(f"static rules reference unknown workers: {sorted(unknown)}")

    def route(self, task_type: str, context: JsonObject) -> str:
        """选择 Worker；动态结果越权时自动退回 fallback_worker。"""

        static_worker = self.static_rules.get(task_type)
        if static_worker:
            return static_worker
        if self.dynamic_router is None:
            return self.fallback_worker
        selected = self.dynamic_router(context, set(self.allowed_workers))
        if selected not in self.allowed_workers:
            return self.fallback_worker
        return selected


class HandoffGuard:
    """限制 Agent 间转交目标与次数，并阻止明显的重复路由。"""

    def __init__(self, allowed_workers: Iterable[str], max_handoffs: int = 6) -> None:
        self.allowed_workers = set(allowed_workers)
        self.max_handoffs = max_handoffs

    def handoff(self, state: SharedState, next_worker: str) -> None:
        """校验并记录一次 Handoff，违反白名单或预算时立即失败。"""

        if next_worker not in self.allowed_workers:
            raise ValueError(f"handoff target is not allowed: {next_worker}")
        if len(state.route_history) >= self.max_handoffs:
            raise RuntimeError("handoff budget exceeded")
        if len(state.route_history) >= 2 and state.route_history[-2:] == [next_worker] * 2:
            raise RuntimeError("handoff loop detected")
        state.route_history.append(next_worker)
        state.append_event("orchestrator", "handoff", {"to": next_worker})


_BINDING_PATTERN = re.compile(r"^\$\{steps\.([^.]+)\.output\.([^}]+)\}$")


def _lookup_path(value: Any, dotted_path: str) -> Any:
    """沿点分路径读取嵌套 Mapping，例如 ``response.data.id``。"""

    current = value
    for part in dotted_path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        raise KeyError(f"path not found: {dotted_path}")
    return current


def resolve_bindings(value: Any, results: Mapping[str, StepResult]) -> Any:
    """递归解析 payload 中的 ``${steps.<id>.output.<path>}`` 引用。"""

    if isinstance(value, str):
        match = _BINDING_PATTERN.match(value)
        if not match:
            return value
        step_id, dotted_path = match.groups()
        if step_id not in results:
            raise KeyError(f"step result not available: {step_id}")
        return _lookup_path(results[step_id].output, dotted_path)
    if isinstance(value, list):
        return [resolve_bindings(item, results) for item in value]
    if isinstance(value, Mapping):
        return {key: resolve_bindings(item, results) for key, item in value.items()}
    return value


def validate_plan(steps: Sequence[PlanStep]) -> None:
    """校验步骤 ID、依赖完整性，并通过 DFS 检测依赖环。"""

    step_ids = [step.step_id for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("plan contains duplicate step ids")
    known_ids = set(step_ids)
    for step in steps:
        missing = set(step.depends_on) - known_ids
        if missing:
            raise ValueError(f"step {step.step_id} has missing dependencies: {sorted(missing)}")

    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {step.step_id: step for step in steps}

    def visit(step_id: str) -> None:
        # visiting 表示当前递归栈；再次遇到栈内节点即可证明存在依赖环。
        if step_id in visiting:
            raise ValueError("plan contains a dependency cycle")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dependency in by_id[step_id].depends_on:
            visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in step_ids:
        visit(step_id)


def check_acceptance(output: JsonObject, criteria: Mapping[str, Any]) -> tuple[bool, str | None]:
    """逐项检查业务输出；返回是否通过以及首个失败原因。"""

    for dotted_path, expected in criteria.items():
        try:
            actual = _lookup_path(output, dotted_path)
        except KeyError:
            return False, f"acceptance path missing: {dotted_path}"
        if actual != expected:
            return False, f"acceptance failed for {dotted_path}: expected {expected!r}, got {actual!r}"
    return True, None


Replanner = Callable[[Sequence[PlanStep], SharedState, StepResult], Sequence[PlanStep]]


class DAGOrchestrator:
    """并行执行就绪步骤，并支持有预算限制的 Replan。"""

    def __init__(
        self,
        registry: WorkerRegistry,
        *,
        max_workers: int = 4,
        max_replans: int = 1,
        checkpoint_path: str | Path | None = None,
    ) -> None:
        self.registry = registry
        self.max_workers = max_workers
        self.max_replans = max_replans
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None

    def run(
        self,
        *,
        task_id: str,
        goal: str,
        plan: Sequence[PlanStep],
        replanner: Replanner | None = None,
    ) -> SharedState:
        """执行计划直至完成，或在无法恢复的失败处抛出异常。

        每轮只选择“所有依赖均已执行且验收通过”的步骤。一个 ready batch
        可以并行执行；任一步骤未通过验收时，根据 on_failure 决定终止还是
        请求 Replanner 返回尚未执行的新计划补丁。
        """

        active_plan = list(plan)
        validate_plan(active_plan)
        state = SharedState(task_id, goal)
        replan_count = 0

        while True:
            pending = [step for step in active_plan if step.step_id not in state.results]
            if not pending:
                return state
            ready = [
                step
                for step in pending
                if all(
                    dependency in state.results and state.results[dependency].accepted
                    for dependency in step.depends_on
                )
            ]
            if not ready:
                # 计划已提前做过环检测，因此这里通常表示上游失败导致后续永久阻塞。
                blocked = [step.step_id for step in pending]
                raise RuntimeError(f"no executable steps; blocked: {blocked}")

            batch_results = self._execute_ready_steps(ready, state)
            failed_result: StepResult | None = None
            failed_step: PlanStep | None = None
            for step, result in batch_results:
                state.save_result(result)
                if not result.accepted and failed_result is None:
                    failed_result = result
                    failed_step = step
            self._write_checkpoint(state)

            if failed_result is None:
                continue
            if (
                failed_step is not None
                and failed_step.on_failure == "replan"
                and replanner is not None
                and replan_count < self.max_replans
            ):
                replan_count += 1
                revised_plan = list(replanner(active_plan, state, failed_result))
                completed_ids = set(state.results)
                # 已执行步骤不可由 Replanner 覆盖，否则审计结果和真实副作用会失配。
                if any(step.step_id in completed_ids for step in revised_plan):
                    raise ValueError("replanner must return only new, unexecuted steps")
                active_plan = [
                    step for step in active_plan if step.step_id in completed_ids
                ] + revised_plan
                validate_plan(active_plan)
                state.append_event(
                    "orchestrator",
                    "plan.revised",
                    {"failed_step": failed_result.step_id, "replan_count": replan_count},
                )
                continue
            raise RuntimeError(
                failed_result.acceptance_error
                or failed_result.error
                or f"step failed: {failed_result.step_id}"
            )

    def _execute_ready_steps(
        self,
        ready: Sequence[PlanStep],
        state: SharedState,
    ) -> list[tuple[PlanStep, StepResult]]:
        """并行执行同一批无未完成依赖的步骤，并按 step_id 稳定返回。"""

        outcomes: list[tuple[PlanStep, StepResult]] = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(ready))) as executor:
            future_to_step = {
                executor.submit(self._execute_step, step, state): step for step in ready
            }
            for future in as_completed(future_to_step):
                step = future_to_step[future]
                outcomes.append((step, future.result()))
        outcomes.sort(key=lambda item: item[0].step_id)
        return outcomes

    def _execute_step(self, step: PlanStep, state: SharedState) -> StepResult:
        """解析输入、调用 Worker，再把技术成功和业务验收分别记录。"""

        state.append_event(step.worker, "step.started", {"step_id": step.step_id})
        try:
            payload = resolve_bindings(step.payload, state.results)
            output = self.registry.execute(step.worker, payload, state)
            accepted, acceptance_error = check_acceptance(output, step.success_criteria)
            return StepResult(
                step_id=step.step_id,
                worker=step.worker,
                ok=True,
                output=output,
                accepted=accepted,
                acceptance_error=acceptance_error,
            )
        except Exception as error:
            # Worker 异常被转换为数据化结果，由主循环统一应用失败策略。
            return StepResult(
                step_id=step.step_id,
                worker=step.worker,
                ok=False,
                output={},
                error=str(error),
                accepted=False,
            )

    def _write_checkpoint(self, state: SharedState) -> None:
        """通过“临时文件 + 原子替换”写入状态快照。"""

        if self.checkpoint_path is None:
            return
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(state.snapshot(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.checkpoint_path)


@dataclass(frozen=True)
class Evaluation:
    """Critic 的结构化评价结果。"""

    passed: bool
    score: float
    issues: tuple[str, ...] = ()


Evaluator = Callable[[str, str, Mapping[str, Any]], Evaluation]
Improver = Callable[[str, str, Evaluation], str]


@dataclass(frozen=True)
class ReflectionRound:
    """一轮被正式评价过的候选输出。"""

    round_index: int
    output: str
    evaluation: Evaluation


@dataclass(frozen=True)
class ReflectionResult:
    """Reflection 最终输出、通过状态及完整轮次记录。"""

    output: str
    passed: bool
    rounds: tuple[ReflectionRound, ...]


class ReflectionEngine:
    """带轮次上限和无进展检测的“评价 → 改进”循环。"""

    def __init__(self, max_rounds: int = 2, minimum_improvement: float = 0.01) -> None:
        if max_rounds <= 0:
            raise ValueError("max_rounds must be positive")
        self.max_rounds = max_rounds
        self.minimum_improvement = minimum_improvement

    def run(
        self,
        *,
        task: str,
        initial_output: str,
        rubric: Mapping[str, Any],
        evaluator: Evaluator,
        improver: Improver,
    ) -> ReflectionResult:
        """反复评价和改进候选答案，返回经过评价的最佳版本。

        improver 生成的新版本不会在最后一个允许轮次之后偷偷返回；只有实际
        进入下一轮并接受 evaluator 评价的版本，才有资格成为最终输出。
        """

        current_output = initial_output
        history: list[ReflectionRound] = []
        previous_score = -1.0
        best_output = current_output
        best_score = -1.0

        for round_index in range(1, self.max_rounds + 1):
            evaluation = evaluator(task, current_output, rubric)
            history.append(ReflectionRound(round_index, current_output, evaluation))
            if evaluation.score > best_score:
                best_score = evaluation.score
                best_output = current_output
            if evaluation.passed:
                return ReflectionResult(current_output, True, tuple(history))
            if previous_score >= 0 and evaluation.score - previous_score < self.minimum_improvement:
                # 连续两轮提升不足，继续调用模型通常只会增加成本和漂移风险。
                break
            previous_score = evaluation.score
            if round_index == self.max_rounds:
                break
            current_output = improver(task, current_output, evaluation)

        return ReflectionResult(best_output, False, tuple(history))


def deterministic_summary(messages: Sequence[JsonObject]) -> str:
    """供 Demo 和测试使用的确定性离线摘要器，不代表生产摘要质量。"""

    parts = [
        f"{message.get('role', 'unknown')}: {str(message.get('content', '')).strip()}"
        for message in messages
        if str(message.get("content", "")).strip()
    ]
    return " | ".join(parts)


def run_demo() -> JsonObject:
    """离线运行一个“压缩上下文 → DAG 调度 → 结果绑定”的完整示例。"""

    memory = ShortTermMemory(max_recent_messages=2)
    memory.update_state(goal="research and summarize an endpoint")
    memory.add("user", "Find endpoint details")
    memory.add("assistant", "I will research first")
    memory.add("tool", "endpoint=/orders/{id}")
    memory.compress(deterministic_summary)

    registry = WorkerRegistry()
    registry.register(
        "researcher",
        lambda payload, _state: {"ok": True, "endpoint": payload["endpoint"]},
    )
    registry.register(
        "writer",
        lambda payload, _state: {"ok": True, "summary": f"Use {payload['endpoint']}"},
    )
    plan = [
        PlanStep(
            step_id="research",
            goal="find endpoint",
            worker="researcher",
            payload={"endpoint": "/orders/1001"},
        ),
        PlanStep(
            step_id="write",
            goal="write summary",
            worker="writer",
            # writer 的输入在执行前从 research 的输出中解析。
            payload={"endpoint": "${steps.research.output.endpoint}"},
            depends_on=("research",),
        ),
    ]
    state = DAGOrchestrator(registry).run(
        task_id="demo-task",
        goal="research and summarize an endpoint",
        plan=plan,
    )
    return {"context": memory.build_context(), "state": state.snapshot()}


if __name__ == "__main__":
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))