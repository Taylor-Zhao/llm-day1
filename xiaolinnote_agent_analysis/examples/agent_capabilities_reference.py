#!/usr/bin/env python3
"""Runnable reference implementation for the Agent capabilities in articles 8-16.

The module intentionally uses the Python standard library and injected callables.
It demonstrates control-plane behavior without requiring an LLM or network access:

- SQLite-backed entity and episodic memory with scoped retrieval and forgetting
- short-term message compression with structured working state
- dependency-aware DAG execution, result bindings, acceptance criteria, checkpoints
- retry versus replan boundaries
- worker registry, append-only shared state, hybrid routing, handoff guards
- structured generate/evaluate/improve reflection

It is a teaching reference, not a drop-in production Agent platform.
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
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower(), flags=re.UNICODE))


def _lexical_similarity(left: str, right: str) -> float:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / math.sqrt(
        len(left_tokens) * len(right_tokens)
    )


def _freshness(created_at: str, half_life_days: float = 30.0) -> float:
    created = datetime.fromisoformat(created_at)
    age_seconds = max(
        0.0,
        (datetime.now(timezone.utc) - created).total_seconds(),
    )
    age_days = age_seconds / 86_400
    return math.pow(0.5, age_days / half_life_days)


@dataclass(frozen=True)
class MemoryRecord:
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
    """A small hybrid memory store with strict tenant/user scoping.

    Entity facts use an exact key and are updated in place. Episodic and semantic
    memories are append-only records retrieved with a deterministic lexical score.
    A production implementation can replace `_lexical_similarity` with embeddings
    while preserving the lifecycle and isolation rules shown here.
    """

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._create_schema()

    def _create_schema(self) -> None:
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
            score = (
                0.65 * semantic_score
                + 0.20 * float(row["importance"])
                + 0.15 * _freshness(str(row["created_at"]))
            )
            ranked.append(self._row_to_record(row, score))
        ranked.sort(key=lambda item: (-item.score, -item.importance, item.memory_id))
        return ranked[:top_k]

    def forget(self, *, tenant_id: str, user_id: str, memory_id: int) -> bool:
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
    """Recent-message window plus a replaceable summary and structured state."""

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
        self.messages.append({"role": role, "content": content, **fields})

    def update_state(self, **changes: Any) -> None:
        self.working_state.update(changes)

    def compress(self, summarize: SummaryFunction) -> None:
        overflow_count = len(self.messages) - self.max_recent_messages
        if overflow_count <= 0:
            return
        older_messages = self.messages[:overflow_count]
        if self.summary:
            older_messages = [
                {"role": "system", "content": f"Previous summary: {self.summary}"},
                *older_messages,
            ]
        self.summary = summarize(older_messages)
        self.messages = self.messages[overflow_count:]

    def build_context(self) -> list[JsonObject]:
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
    step_id: str
    goal: str
    worker: str
    payload: JsonObject = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    success_criteria: JsonObject = field(default_factory=lambda: {"ok": True})
    on_failure: str = "fail"


@dataclass(frozen=True)
class StepResult:
    step_id: str
    worker: str
    ok: bool
    output: JsonObject
    error: str | None = None
    accepted: bool = False
    acceptance_error: str | None = None


@dataclass(frozen=True)
class StateEvent:
    sequence: int
    timestamp_utc: str
    task_id: str
    agent_id: str
    event_type: str
    payload: JsonObject


class SharedState:
    """Thread-safe append-only state used by workers and the orchestrator."""

    def __init__(self, task_id: str, goal: str) -> None:
        self.task_id = task_id
        self.goal = goal
        self.events: list[StateEvent] = []
        self.results: dict[str, StepResult] = {}
        self.route_history: list[str] = []
        self.lock = threading.RLock()

    def append_event(self, agent_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
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
        with self.lock:
            if result.step_id in self.results:
                raise ValueError(f"step result already exists: {result.step_id}")
            self.results[result.step_id] = result
            self.append_event(result.worker, "step.completed", asdict(result))

    def snapshot(self) -> JsonObject:
        with self.lock:
            return {
                "task_id": self.task_id,
                "goal": self.goal,
                "results": {key: asdict(value) for key, value in self.results.items()},
                "route_history": list(self.route_history),
                "events": [asdict(event) for event in self.events],
            }


class Worker(Protocol):
    def __call__(self, payload: JsonObject, state: SharedState) -> JsonObject: ...


class WorkerRegistry:
    def __init__(self) -> None:
        self.workers: dict[str, Worker] = {}

    def register(self, name: str, worker: Worker) -> None:
        if name in self.workers:
            raise ValueError(f"worker already registered: {name}")
        self.workers[name] = worker

    def execute(self, name: str, payload: JsonObject, state: SharedState) -> JsonObject:
        worker = self.workers.get(name)
        if worker is None:
            raise ValueError(f"unknown worker: {name}")
        return worker(payload, state)

    @property
    def names(self) -> set[str]:
        return set(self.workers)


DynamicRouter = Callable[[JsonObject, set[str]], str]


class HybridRouter:
    """Static rules first, validated dynamic fallback second."""

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
    def __init__(self, allowed_workers: Iterable[str], max_handoffs: int = 6) -> None:
        self.allowed_workers = set(allowed_workers)
        self.max_handoffs = max_handoffs

    def handoff(self, state: SharedState, next_worker: str) -> None:
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
    current = value
    for part in dotted_path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        raise KeyError(f"path not found: {dotted_path}")
    return current


def resolve_bindings(value: Any, results: Mapping[str, StepResult]) -> Any:
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
    """Dependency scheduler with parallel ready steps and bounded replanning."""

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
            return StepResult(
                step_id=step.step_id,
                worker=step.worker,
                ok=False,
                output={},
                error=str(error),
                accepted=False,
            )

    def _write_checkpoint(self, state: SharedState) -> None:
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
    passed: bool
    score: float
    issues: tuple[str, ...] = ()


Evaluator = Callable[[str, str, Mapping[str, Any]], Evaluation]
Improver = Callable[[str, str, Evaluation], str]


@dataclass(frozen=True)
class ReflectionRound:
    round_index: int
    output: str
    evaluation: Evaluation


@dataclass(frozen=True)
class ReflectionResult:
    output: str
    passed: bool
    rounds: tuple[ReflectionRound, ...]


class ReflectionEngine:
    """Bounded generate/evaluate/improve loop with no-progress detection."""

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
                break
            previous_score = evaluation.score
            if round_index == self.max_rounds:
                break
            current_output = improver(task, current_output, evaluation)

        return ReflectionResult(best_output, False, tuple(history))


def deterministic_summary(messages: Sequence[JsonObject]) -> str:
    """Offline summarizer used by the demo and tests."""

    parts = [
        f"{message.get('role', 'unknown')}: {str(message.get('content', '')).strip()}"
        for message in messages
        if str(message.get("content", "")).strip()
    ]
    return " | ".join(parts)


def run_demo() -> JsonObject:
    """Execute an offline end-to-end example of the reference controls."""

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