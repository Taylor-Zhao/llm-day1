#!/usr/bin/env python3
"""Agent, local workspace, graph retrieval, harness, and loop reference code.

The module is deliberately offline and standard-library only. The Markdown
workspace is inspired by file-backed local agents, but it is not an OpenClaw
implementation. The graph index demonstrates LightRAG-shaped dual-level
retrieval, but it does not implement Microsoft GraphRAG or HKUDS LightRAG.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


JsonObject = dict[str, Any]
ToolFunction = Callable[..., Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w-]+", text.casefold(), flags=re.UNICODE))


def _lexical_score(terms: Sequence[str], text: str) -> float:
    normalized = text.casefold()
    matches = sum(term.casefold() in normalized for term in terms if term.strip())
    return float(matches)


class WorkspaceError(RuntimeError):
    """A local workspace file is missing, invalid, or outside its root."""


@dataclass(frozen=True)
class WorkspaceContext:
    soul: str
    instructions: str
    tools: str
    heartbeat: str
    memories: tuple[str, ...]
    recent_sessions: tuple[str, ...]

    def render(self) -> str:
        sections = [
            ("SOUL", self.soul),
            ("INSTRUCTIONS", self.instructions),
            ("TOOLS", self.tools),
            ("HEARTBEAT", self.heartbeat),
            ("RELEVANT MEMORY", "\n".join(self.memories)),
            ("RECENT SESSIONS", "\n\n".join(self.recent_sessions)),
        ]
        return "\n\n".join(f"## {title}\n{content}" for title, content in sections if content)


class MarkdownAgentWorkspace:
    """A transparent, file-backed local-agent workspace teaching skeleton."""

    CORE_FILES = ("SOUL.md", "AGENTS.md", "TOOLS.md", "HEARTBEAT.md", "MEMORY.md")

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def bootstrap(self, initial_files: Mapping[str, str] | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "sessions").mkdir(exist_ok=True)
        provided = dict(initial_files or {})
        for name in self.CORE_FILES:
            path = self.root / name
            if not path.exists():
                path.write_text(provided.get(name, f"# {name}\n"), encoding="utf-8")

    def _safe_file(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        if target == self.root or self.root not in target.parents:
            raise WorkspaceError("path is outside the workspace")
        return target

    def read(self, relative_path: str) -> str:
        target = self._safe_file(relative_path)
        if not target.is_file():
            raise WorkspaceError(f"workspace file not found: {relative_path}")
        return target.read_text(encoding="utf-8")

    def record_session(self, session_id: str, content: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", session_id):
            raise WorkspaceError("session_id contains unsafe characters")
        target = self._safe_file(f"sessions/{session_id}.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content.strip() + "\n", encoding="utf-8")
        return target

    def remember(self, fact: str) -> bool:
        normalized = fact.strip()
        if not normalized:
            raise ValueError("memory fact must not be empty")
        target = self._safe_file("MEMORY.md")
        current = target.read_text(encoding="utf-8") if target.exists() else "# MEMORY.md\n"
        existing = {line.strip() for line in current.splitlines()}
        line = f"- {normalized}"
        if line in existing:
            return False
        target.write_text(current.rstrip() + "\n" + line + "\n", encoding="utf-8")
        return True

    def build_context(
        self,
        query: str,
        *,
        memory_limit: int = 5,
        recent_session_limit: int = 2,
        max_characters: int = 12_000,
    ) -> WorkspaceContext:
        if min(memory_limit, recent_session_limit, max_characters) < 0 or max_characters == 0:
            raise ValueError("context limits must be non-negative and max_characters must be positive")
        for name in self.CORE_FILES:
            if not (self.root / name).is_file():
                raise WorkspaceError(f"workspace is not bootstrapped: {name}")

        query_tokens = _tokens(query)
        memory_lines = [
            line.strip()
            for line in self.read("MEMORY.md").splitlines()
            if line.strip().startswith("-")
        ]
        ranked_memories = sorted(
            memory_lines,
            key=lambda line: (-len(query_tokens & _tokens(line)), line.casefold()),
        )
        relevant = tuple(
            line for line in ranked_memories if query_tokens & _tokens(line)
        )[:memory_limit]

        session_paths = sorted((self.root / "sessions").glob("*.md"), key=lambda path: path.name)
        recent = tuple(
            path.read_text(encoding="utf-8").strip()
            for path in session_paths[-recent_session_limit:]
        ) if recent_session_limit else ()

        context = WorkspaceContext(
            soul=self.read("SOUL.md").strip(),
            instructions=self.read("AGENTS.md").strip(),
            tools=self.read("TOOLS.md").strip(),
            heartbeat=self.read("HEARTBEAT.md").strip(),
            memories=relevant,
            recent_sessions=recent,
        )
        if len(context.render()) <= max_characters:
            return context
        return WorkspaceContext(
            soul=context.soul[: max_characters // 5],
            instructions=context.instructions[: max_characters // 3],
            tools=context.tools[: max_characters // 6],
            heartbeat=context.heartbeat[: max_characters // 12],
            memories=context.memories,
            recent_sessions=(),
        )


@dataclass
class EntityRecord:
    name: str
    descriptions: list[str] = field(default_factory=list)
    source_ids: set[str] = field(default_factory=set)

    @property
    def text(self) -> str:
        return f"{self.name}: {'; '.join(self.descriptions)}"


@dataclass
class RelationRecord:
    source: str
    target: str
    descriptions: list[str] = field(default_factory=list)
    keywords: set[str] = field(default_factory=set)
    source_ids: set[str] = field(default_factory=set)

    @property
    def key(self) -> str:
        return f"{self.source}->{self.target}"

    @property
    def text(self) -> str:
        keyword_text = ", ".join(sorted(self.keywords))
        return f"{self.source} -> {self.target} [{keyword_text}]: {'; '.join(self.descriptions)}"


@dataclass(frozen=True)
class GraphHit:
    kind: str
    key: str
    text: str
    source_ids: tuple[str, ...]
    score: float


class DualLevelGraphIndex:
    """Entity-local and relation-global retrieval with bounded graph expansion."""

    def __init__(self) -> None:
        self.entities: dict[str, EntityRecord] = {}
        self.relations: dict[tuple[str, str], RelationRecord] = {}

    @staticmethod
    def _key(name: str) -> str:
        normalized = " ".join(name.casefold().split())
        if not normalized:
            raise ValueError("entity name must not be empty")
        return normalized

    def upsert_entity(self, name: str, description: str, source_id: str) -> EntityRecord:
        key = self._key(name)
        entity = self.entities.setdefault(key, EntityRecord(name=name.strip()))
        if description.strip() and description.strip() not in entity.descriptions:
            entity.descriptions.append(description.strip())
        if source_id:
            entity.source_ids.add(source_id)
        return entity

    def upsert_relation(
        self,
        source: str,
        target: str,
        description: str,
        *,
        keywords: Sequence[str] = (),
        source_id: str,
    ) -> RelationRecord:
        source_key = self._key(source)
        target_key = self._key(target)
        self.entities.setdefault(source_key, EntityRecord(name=source.strip()))
        self.entities.setdefault(target_key, EntityRecord(name=target.strip()))
        relation = self.relations.setdefault(
            (source_key, target_key),
            RelationRecord(source=self.entities[source_key].name, target=self.entities[target_key].name),
        )
        if description.strip() and description.strip() not in relation.descriptions:
            relation.descriptions.append(description.strip())
        relation.keywords.update(keyword.strip().casefold() for keyword in keywords if keyword.strip())
        if source_id:
            relation.source_ids.add(source_id)
        return relation

    def local_search(self, low_level_terms: Sequence[str], *, limit: int = 5) -> list[GraphHit]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        entity_scores = [
            (key, _lexical_score(low_level_terms, entity.text))
            for key, entity in self.entities.items()
        ]
        selected = [
            key
            for key, score in sorted(entity_scores, key=lambda item: (-item[1], item[0]))
            if score > 0
        ][:limit]
        hits: list[GraphHit] = []
        for key in selected:
            entity = self.entities[key]
            score = _lexical_score(low_level_terms, entity.text)
            hits.append(GraphHit("entity", entity.name, entity.text, tuple(sorted(entity.source_ids)), score))
            for (source_key, target_key), relation in self.relations.items():
                if key in {source_key, target_key}:
                    hits.append(
                        GraphHit(
                            "relation",
                            relation.key,
                            relation.text,
                            tuple(sorted(relation.source_ids)),
                            score * 0.5,
                        )
                    )
        return self._deduplicate(hits)[:limit]

    def global_search(self, high_level_terms: Sequence[str], *, limit: int = 5) -> list[GraphHit]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        hits = [
            GraphHit(
                "relation",
                relation.key,
                relation.text,
                tuple(sorted(relation.source_ids)),
                _lexical_score(high_level_terms, relation.text),
            )
            for relation in self.relations.values()
        ]
        return sorted((hit for hit in hits if hit.score > 0), key=lambda hit: (-hit.score, hit.key))[:limit]

    def hybrid_search(
        self,
        *,
        low_level_terms: Sequence[str],
        high_level_terms: Sequence[str],
        limit: int = 8,
    ) -> list[GraphHit]:
        hits = self.local_search(low_level_terms, limit=limit) + self.global_search(high_level_terms, limit=limit)
        return self._deduplicate(hits)[:limit]

    @staticmethod
    def _deduplicate(hits: Sequence[GraphHit]) -> list[GraphHit]:
        best: dict[tuple[str, str], GraphHit] = {}
        for hit in hits:
            identity = (hit.kind, hit.key)
            if identity not in best or hit.score > best[identity].score:
                best[identity] = hit
        return sorted(best.values(), key=lambda hit: (-hit.score, hit.kind, hit.key))

    def multi_hop_paths(
        self,
        start: str,
        *,
        max_depth: int = 2,
        max_nodes: int = 50,
    ) -> list[tuple[str, ...]]:
        if max_depth < 0 or max_nodes <= 0:
            raise ValueError("max_depth must be non-negative and max_nodes must be positive")
        start_key = self._key(start)
        if start_key not in self.entities:
            return []
        adjacency: dict[str, set[str]] = {key: set() for key in self.entities}
        for source, target in self.relations:
            adjacency[source].add(target)
            adjacency[target].add(source)
        queue: deque[tuple[str, tuple[str, ...]]] = deque([(start_key, (start_key,))])
        paths: list[tuple[str, ...]] = []
        visited = {start_key}
        while queue and len(visited) < max_nodes:
            node, path = queue.popleft()
            if len(path) - 1 >= max_depth:
                continue
            for neighbor in sorted(adjacency[node]):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                next_path = path + (neighbor,)
                paths.append(tuple(self.entities[key].name for key in next_path))
                queue.append((neighbor, next_path))
                if len(visited) >= max_nodes:
                    break
        return paths


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    dangerous: bool = False
    idempotent: bool = True


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    value: Any = None
    error: str | None = None
    retryable: bool = False


class ControlledToolRegistry:
    """Allowlisted tools with approval and call-id idempotency controls."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolDefinition, ToolFunction]] = {}
        self._results_by_call_id: dict[str, ToolOutcome] = {}

    def register(self, definition: ToolDefinition, function: ToolFunction) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = (definition, function)

    def execute(
        self,
        *,
        call_id: str,
        name: str,
        arguments: Mapping[str, Any],
        approved_tools: set[str] | None = None,
    ) -> ToolOutcome:
        entry = self._tools.get(name)
        if entry is None:
            return ToolOutcome(False, error="unknown tool")
        definition, function = entry
        if definition.idempotent and call_id in self._results_by_call_id:
            return self._results_by_call_id[call_id]
        if definition.dangerous and name not in (approved_tools or set()):
            return ToolOutcome(False, error="explicit approval required")
        try:
            outcome = ToolOutcome(True, value=function(**dict(arguments)))
        except Exception as exc:
            outcome = ToolOutcome(False, error=str(exc), retryable=False)
        if definition.idempotent:
            self._results_by_call_id[call_id] = outcome
        return outcome


class ActionKind(str, Enum):
    TOOL = "tool"
    FINISH = "finish"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class LoopAction:
    kind: ActionKind
    tool_name: str = ""
    arguments: JsonObject = field(default_factory=dict)
    call_id: str = ""
    output: Any = None
    summary: str = ""
    tokens_used: int = 0
    cost: float = 0.0

    @classmethod
    def tool(
        cls,
        name: str,
        arguments: Mapping[str, Any],
        *,
        call_id: str,
        tokens_used: int = 0,
        cost: float = 0.0,
    ) -> "LoopAction":
        return cls(ActionKind.TOOL, name, dict(arguments), call_id, tokens_used=tokens_used, cost=cost)

    @classmethod
    def finish(cls, output: Any, *, tokens_used: int = 0, cost: float = 0.0) -> "LoopAction":
        return cls(ActionKind.FINISH, output=output, tokens_used=tokens_used, cost=cost)

    @classmethod
    def escalate(cls, reason: str) -> "LoopAction":
        return cls(ActionKind.ESCALATE, summary=reason)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    evidence: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass
class LoopState:
    run_id: str
    goal: str
    status: str = "running"
    iteration: int = 0
    failures: int = 0
    tokens_used: int = 0
    cost: float = 0.0
    repeated_actions: int = 0
    last_action_fingerprint: str = ""
    observations: list[JsonObject] = field(default_factory=list)
    verifier_feedback: list[str] = field(default_factory=list)
    final_output: Any = None
    gate_evidence: list[str] = field(default_factory=list)
    stop_reason: str = ""
    updated_at: str = field(default_factory=utc_now_iso)

    def to_json(self) -> JsonObject:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "LoopState":
        return cls(**dict(data))


@dataclass(frozen=True)
class LoopLimits:
    max_iterations: int = 8
    max_failures: int = 3
    max_tokens: int = 20_000
    max_cost: float = 5.0
    max_repeated_action: int = 2
    max_observations: int = 30

    def __post_init__(self) -> None:
        values = (
            self.max_iterations,
            self.max_failures,
            self.max_tokens,
            self.max_repeated_action,
            self.max_observations,
        )
        if any(value <= 0 for value in values) or self.max_cost <= 0:
            raise ValueError("all loop limits must be positive")


class AtomicCheckpointStore:
    """One-run JSON checkpoint written with replace instead of partial overwrite."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, state: LoopState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state.to_json(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def load(self) -> LoopState | None:
        if not self.path.exists():
            return None
        return LoopState.from_json(json.loads(self.path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class TraceEvent:
    sequence: int
    event: str
    payload: JsonObject


class TraceRecorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def record(self, event: str, payload: Mapping[str, Any]) -> None:
        self.events.append(TraceEvent(len(self.events) + 1, event, dict(payload)))


Policy = Callable[[LoopState], LoopAction]
Verifier = Callable[[Any, LoopState], GateResult]


class LoopHarness:
    """A bounded maker/checker loop with durable state and hard stop conditions."""

    TERMINAL_STATUSES = {"completed", "failed", "blocked", "escalated"}

    def __init__(
        self,
        tools: ControlledToolRegistry,
        *,
        limits: LoopLimits | None = None,
        checkpoint: AtomicCheckpointStore | None = None,
        trace: TraceRecorder | None = None,
    ) -> None:
        self.tools = tools
        self.limits = limits or LoopLimits()
        self.checkpoint = checkpoint
        self.trace = trace or TraceRecorder()

    @staticmethod
    def _fingerprint(action: LoopAction) -> str:
        payload = {
            "kind": action.kind.value,
            "tool": action.tool_name,
            "arguments": action.arguments,
            "output": action.output,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def _save(self, state: LoopState) -> None:
        state.updated_at = utc_now_iso()
        state.observations = state.observations[-self.limits.max_observations :]
        if self.checkpoint is not None:
            self.checkpoint.save(state)

    def _stop(self, state: LoopState, status: str, reason: str) -> LoopState:
        state.status = status
        state.stop_reason = reason
        self.trace.record("loop_stopped", {"status": status, "reason": reason})
        self._save(state)
        return state

    def run(
        self,
        *,
        run_id: str,
        goal: str,
        policy: Policy,
        verifier: Verifier,
        approved_tools: set[str] | None = None,
        resume: bool = True,
    ) -> LoopState:
        loaded = self.checkpoint.load() if resume and self.checkpoint is not None else None
        if loaded is not None:
            if loaded.run_id != run_id or loaded.goal != goal:
                raise ValueError("checkpoint does not match run_id and goal")
            if loaded.status in self.TERMINAL_STATUSES:
                return loaded
            state = loaded
            state.status = "running"
            state.stop_reason = ""
            self.trace.record("run_resumed", {"iteration": state.iteration})
        else:
            state = LoopState(run_id=run_id, goal=goal)
            self.trace.record("run_started", {"run_id": run_id, "goal": goal})
            self._save(state)

        while True:
            if state.iteration >= self.limits.max_iterations:
                return self._stop(state, "budget_exhausted", "iteration budget exhausted")
            if state.failures >= self.limits.max_failures:
                return self._stop(state, "failed", "failure budget exhausted")

            action = policy(copy.deepcopy(state))
            state.iteration += 1
            state.tokens_used += max(0, action.tokens_used)
            state.cost += max(0.0, action.cost)
            self.trace.record(
                "action_proposed",
                {"iteration": state.iteration, "kind": action.kind.value, "tool": action.tool_name},
            )

            if state.tokens_used > self.limits.max_tokens:
                return self._stop(state, "budget_exhausted", "token budget exhausted")
            if state.cost > self.limits.max_cost:
                return self._stop(state, "budget_exhausted", "cost budget exhausted")

            fingerprint = self._fingerprint(action)
            if fingerprint == state.last_action_fingerprint:
                state.repeated_actions += 1
            else:
                state.last_action_fingerprint = fingerprint
                state.repeated_actions = 1
            if state.repeated_actions > self.limits.max_repeated_action:
                return self._stop(state, "blocked", "repeated action detected")

            if action.kind is ActionKind.ESCALATE:
                state.final_output = action.summary
                return self._stop(state, "escalated", action.summary or "human review required")

            if action.kind is ActionKind.TOOL:
                outcome = self.tools.execute(
                    call_id=action.call_id or f"{run_id}:{state.iteration}",
                    name=action.tool_name,
                    arguments=action.arguments,
                    approved_tools=approved_tools,
                )
                observation = {
                    "iteration": state.iteration,
                    "type": "tool",
                    "tool": action.tool_name,
                    "call_id": action.call_id,
                    "ok": outcome.ok,
                    "value": outcome.value,
                    "error": outcome.error,
                }
                state.observations.append(observation)
                if not outcome.ok:
                    state.failures += 1
                    state.verifier_feedback.append(outcome.error or "tool failed")
                self.trace.record("tool_result", observation)
                self._save(state)
                continue

            if action.kind is ActionKind.FINISH:
                gate = verifier(action.output, copy.deepcopy(state))
                observation = {
                    "iteration": state.iteration,
                    "type": "gate",
                    "passed": gate.passed,
                    "evidence": list(gate.evidence),
                    "errors": list(gate.errors),
                }
                state.observations.append(observation)
                self.trace.record("gate_result", observation)
                if gate.passed:
                    state.final_output = action.output
                    state.gate_evidence = list(gate.evidence)
                    return self._stop(state, "completed", "objective gate passed")
                state.failures += 1
                state.verifier_feedback.extend(gate.errors or ("objective gate failed",))
                self._save(state)
                continue

            return self._stop(state, "failed", "unsupported action kind")


@dataclass(frozen=True)
class LoopCandidate:
    repetitions_per_week: int
    has_objective_gate: bool
    agent_can_execute_and_observe: bool
    token_budget: float
    risk_level: str = "low"
    irreversible_actions_require_approval: bool = True
    human_will_review: bool = True


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reasons: tuple[str, ...]


def assess_loop_candidate(candidate: LoopCandidate) -> EligibilityResult:
    reasons: list[str] = []
    if candidate.repetitions_per_week < 2:
        reasons.append("task is not repetitive enough")
    if not candidate.has_objective_gate:
        reasons.append("no objective completion gate")
    if not candidate.agent_can_execute_and_observe:
        reasons.append("agent cannot execute and observe its work")
    if candidate.token_budget <= 0:
        reasons.append("no positive token budget")
    if candidate.risk_level.casefold() in {"high", "critical"}:
        reasons.append("risk level requires attended execution")
    if not candidate.irreversible_actions_require_approval:
        reasons.append("irreversible actions lack approval")
    if not candidate.human_will_review:
        reasons.append("no human review commitment")
    return EligibilityResult(not reasons, tuple(reasons))


@dataclass(frozen=True)
class LoopEconomics:
    proposed_changes: int
    accepted_changes: int
    model_and_compute_cost: float
    review_minutes: float
    reviewer_hourly_cost: float

    def __post_init__(self) -> None:
        if self.proposed_changes < 0 or not 0 <= self.accepted_changes <= self.proposed_changes:
            raise ValueError("accepted changes must be between zero and proposed changes")
        if min(self.model_and_compute_cost, self.review_minutes, self.reviewer_hourly_cost) < 0:
            raise ValueError("cost values must be non-negative")

    @property
    def acceptance_rate(self) -> float:
        return self.accepted_changes / self.proposed_changes if self.proposed_changes else 0.0

    @property
    def total_cost(self) -> float:
        return self.model_and_compute_cost + self.review_minutes / 60 * self.reviewer_hourly_cost

    @property
    def cost_per_accepted_change(self) -> float:
        return self.total_cost / self.accepted_changes if self.accepted_changes else math.inf

    def meets_threshold(self, minimum_acceptance_rate: float) -> bool:
        if not 0 <= minimum_acceptance_rate <= 1:
            raise ValueError("minimum acceptance rate must be in [0, 1]")
        return self.acceptance_rate >= minimum_acceptance_rate


def run_demo() -> None:
    tools = ControlledToolRegistry()
    tools.register(ToolDefinition("double"), lambda value: value * 2)
    actions = deque(
        [
            LoopAction.tool("double", {"value": 21}, call_id="call-1"),
            LoopAction.finish({"answer": 42}),
        ]
    )
    harness = LoopHarness(tools)
    state = harness.run(
        run_id="demo",
        goal="calculate 42",
        policy=lambda _state: actions.popleft(),
        verifier=lambda output, _state: GateResult(
            output.get("answer") == 42,
            evidence=("deterministic assertion: answer == 42",),
        ),
    )
    print("Loop status:", state.status)
    print("Gate evidence:", state.gate_evidence)


if __name__ == "__main__":
    run_demo()