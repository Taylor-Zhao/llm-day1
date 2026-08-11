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

# 源码逐段导读（按行号，覆盖全文件逻辑）
# 说明：下面的行号锚点对应“未插入本导读注释前”的原始文件版本，
# 目的不是做字节级定位，而是帮助阅读者快速建立全局心智模型。
#
# [1-15]  模块声明与能力边界说明：强调本文件是控制面参考实现，非生产平台。
# [17-29] 标准库导入：并发、持久化、数据模型、类型系统等基础能力。
# [32]    JsonObject 类型别名：统一表达 JSON 结构的字典对象。
#
# [35-68] 基础函数：
#         - utc_now_iso：统一 UTC 时间戳格式；
#         - _tokenize/_lexical_similarity：离线词法检索基础；
#         - _freshness：按半衰期计算“时间衰减分”。
#
# [71-87] MemoryRecord：检索结果的不可变结构体，score 为查询期排序分。
#
# [89-320] SQLiteMemoryStore：长期记忆子系统。
#         - __init__/_create_schema：建表与唯一索引约束；
#         - add_memory：情节/语义/程序记忆追加写；
#         - upsert_fact：实体事实按 key 原地更新；
#         - get_facts：按租户和用户读取有效实体事实；
#         - search：相关度 + 重要度 + 新鲜度加权检索；
#         - forget：软删除；
#         - _row_to_record：数据库行到领域对象转换。
#
# [323-382] ShortTermMemory：短期上下文子系统。
#         - add/update_state：分别维护消息与结构化状态；
#         - compress：对超窗消息做可注入摘要；
#         - build_context：按“状态 -> 摘要 -> 最近原文”组装模型上下文。
#
# [385-420] 任务编排数据模型：PlanStep/StepResult/StateEvent。
#
# [423-469] SharedState：
#         - append-only 审计事件；
#         - 结果单次写入约束；
#         - snapshot 供 checkpoint 与回放。
#
# [471-503] Worker 协议与 WorkerRegistry 白名单执行入口。
#
# [505-540] HybridRouter：静态规则优先，动态路由兜底并做越权回退。
# [543-560] HandoffGuard：转交流程的白名单、预算和环路防护。
#
# [563-593] resolve_bindings：
#         递归解析 ${steps.<id>.output.<path>}，将上游输出绑定到下游输入。
#
# [596-625] validate_plan：步骤唯一性、依赖完整性、DFS 环检测。
# [628-638] check_acceptance：业务验收门，区分“执行成功”与“业务通过”。
#
# [641-793] DAGOrchestrator：编排主引擎。
#         - run：按依赖调度 ready batch，并行执行，失败时按策略 Replan；
#         - _execute_ready_steps：线程池并行；
#         - _execute_step：绑定解析 -> 调用 worker -> 验收；
#         - _write_checkpoint：临时文件 + 原子替换。
#
# [796-873] Reflection 子系统：
#         - Evaluation/ReflectionRound/ReflectionResult 数据模型；
#         - ReflectionEngine.run：评估 -> 改进循环，支持轮次上限和最小提升停止。
#
# [876-884] deterministic_summary：用于离线测试的确定性摘要器。
#
# [887-927] run_demo：串起“短期记忆压缩 -> DAG 调度 -> 结果绑定”的最小闭环。
# [930-931] 脚本入口：打印演示结果 JSON。

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

    # 统一使用 UTC，避免多机器、多时区下的比较歧义。
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    """将文本转换为去重词元集合，供离线词法检索使用。"""

    # 小写化 + 正则切词；返回 set 以便后续交并集计算。
    return set(re.findall(r"\w+", text.lower(), flags=re.UNICODE))


def _lexical_similarity(left: str, right: str) -> float:
    """计算两个词元集合的余弦形式相似度，取值范围为 0 到 1。"""

    # 分别把左右文本转成词元集合。
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    # 任一侧为空，直接定义相似度为 0。
    if not left_tokens or not right_tokens:
        return 0.0
    # 这里使用集合版余弦风格归一化：交集 / sqrt(|A||B|)。
    return len(left_tokens & right_tokens) / math.sqrt(
        len(left_tokens) * len(right_tokens)
    )


def _freshness(created_at: str, half_life_days: float = 30.0) -> float:
    """按半衰期计算记忆新鲜度，记忆越旧，分数越接近 0。"""

    # 把 ISO 字符串恢复为 datetime 对象。
    created = datetime.fromisoformat(created_at)
    # 计算“到当前时刻”为止的年龄秒数，并保证非负。
    age_seconds = max(
        0.0,
        (datetime.now(timezone.utc) - created).total_seconds(),
    )
    # 换算成天，便于代入半衰期模型。
    age_days = age_seconds / 86_400
    # 半衰期衰减公式：0.5^(age / half_life)。
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
        # check_same_thread=False 允许跨线程访问同一连接。
        self.connection = sqlite3.connect(str(database_path), check_same_thread=False)
        # row_factory=sqlite3.Row 让结果支持按列名访问。
        self.connection.row_factory = sqlite3.Row
        # 使用可重入锁，允许同一线程内的嵌套调用安全加锁。
        self.lock = threading.RLock()
        # 初始化数据库结构。
        self._create_schema()

    def _create_schema(self) -> None:
        """创建记忆表，以及“每个用户的同名实体事实唯一”约束。"""

        # 使用连接上下文，自动开启并提交事务。
        with self.connection:
            # memories 主表：统一存实体事实和非实体记忆。
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
            # 仅对 entity + active 应用唯一约束，支持软删除后再写入。
            self.connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS entity_identity
                ON memories (tenant_id, user_id, memory_type, entity_key)
                WHERE memory_type = 'entity' AND status = 'active'
                """
            )

    def close(self) -> None:
        """关闭底层 SQLite 连接。"""

        # 释放数据库句柄。
        self.connection.close()

    def __enter__(self) -> "SQLiteMemoryStore":
        # 支持 with SQLiteMemoryStore() as store 语法。
        return self

    def __exit__(self, *_: object) -> None:
        # 离开 with 代码块时自动关闭连接。
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

        # 限定只允许三种非实体记忆类型。
        if memory_type not in {"episodic", "semantic", "procedural"}:
            raise ValueError("memory_type must be episodic, semantic, or procedural")
        # 重要度应在 [0,1] 范围内。
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        # 锁 + 事务双重保证并发与一致性。
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
            # 返回数据库分配的主键 ID。
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

        # 统一记录更新时间戳。
        now = utc_now_iso()
        # UPSERT: 有则更新，无则插入。
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
            # 再次查询 memory_id，保证调用方拿到稳定标识。
            row = self.connection.execute(
                """
                SELECT memory_id FROM memories
                WHERE tenant_id = ? AND user_id = ? AND memory_type = 'entity'
                  AND entity_key = ? AND status = 'active'
                """,
                (tenant_id, user_id, key),
            ).fetchone()
            # 理论上不应为空，空说明数据库状态异常。
            if row is None:
                raise RuntimeError("entity upsert did not create a record")
            return int(row["memory_id"])

    def get_facts(self, *, tenant_id: str, user_id: str) -> dict[str, str]:
        """返回指定用户所有有效实体事实，不跨越租户或用户边界。"""

        # 查询当前作用域内 active 且未过期的 entity 事实。
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
        # 转换为 key->value 映射，便于上层直接读取。
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

        # 检索条数必须为正。
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        # 固化为 tuple，便于后续占位符展开。
        allowed_types = tuple(memory_types)
        # 没有允许类型时直接返回空。
        if not allowed_types:
            return []
        # 动态构造 SQL IN 子句的占位符个数。
        placeholders = ",".join("?" for _ in allowed_types)
        # 按租户、用户、类型、状态、有效期筛选候选记忆。
        rows = self.connection.execute(
            f"""
            SELECT * FROM memories
            WHERE tenant_id = ? AND user_id = ? AND status = 'active'
              AND memory_type IN ({placeholders})
              AND (valid_until IS NULL OR valid_until > ?)
            """,
            (tenant_id, user_id, *allowed_types, utc_now_iso()),
        ).fetchall()
        # 保存打分后的候选列表。
        ranked: list[MemoryRecord] = []
        for row in rows:
            # 1) 词法相关度
            semantic_score = _lexical_similarity(query, str(row["content"]))
            # 权重是教学示例：相关度主导排序，重要度和新鲜度用于修正同类结果。
            # 2) 综合评分 = 相关度 + 重要度 + 新鲜度。
            score = (
                0.65 * semantic_score
                + 0.20 * float(row["importance"])
                + 0.15 * _freshness(str(row["created_at"]))
            )
            # 行对象转领域对象并携带分数。
            ranked.append(self._row_to_record(row, score))
        # 先按 score 降序，再按重要度降序，再按 memory_id 升序稳定排序。
        ranked.sort(key=lambda item: (-item.score, -item.importance, item.memory_id))
        # 返回 top_k。
        return ranked[:top_k]

    def forget(self, *, tenant_id: str, user_id: str, memory_id: int) -> bool:
        """在所属作用域内软删除记忆；无权访问或记录不存在时返回 False。"""

        # 软删除只改状态，不物理删除数据。
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE memories SET status = 'forgotten'
                WHERE memory_id = ? AND tenant_id = ? AND user_id = ?
                  AND status = 'active'
                """,
                (memory_id, tenant_id, user_id),
            )
            # rowcount==1 表示删除成功。
            return cursor.rowcount == 1

    @staticmethod
    def _row_to_record(row: sqlite3.Row, score: float) -> MemoryRecord:
        """把 SQLite Row 转换为对调用方更稳定的领域对象。"""

        # 明确逐字段转换，避免上层接触 sqlite.Row。
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
        # 窗口必须为正。
        if max_recent_messages <= 0:
            raise ValueError("max_recent_messages must be positive")
        # 保存窗口大小配置。
        self.max_recent_messages = max_recent_messages
        # 近期原始消息窗口。
        self.messages: list[JsonObject] = []
        # 历史摘要（可替换）。
        self.summary = ""
        # 结构化工作状态，不参与丢弃。
        self.working_state: JsonObject = {
            "goal": "",
            "confirmed_facts": {},
            "completed_steps": [],
            "open_questions": [],
            "errors": [],
        }

    def add(self, role: str, content: str, **fields: Any) -> None:
        """追加一条兼容 Chat Completion 结构的消息。"""

        # 支持附加字段（如 tool_call_id、name 等）。
        self.messages.append({"role": role, "content": content, **fields})

    def update_state(self, **changes: Any) -> None:
        """更新不会被摘要丢弃的结构化工作状态。"""

        # 增量更新状态字典。
        self.working_state.update(changes)

    def compress(self, summarize: SummaryFunction) -> None:
        """摘要超出窗口的旧消息，只保留最近的原始消息。"""

        # 计算超窗数量。
        overflow_count = len(self.messages) - self.max_recent_messages
        # 未超窗则无需压缩。
        if overflow_count <= 0:
            return
        # 取出将被压缩的旧消息。
        older_messages = self.messages[:overflow_count]
        if self.summary:
            # 把旧摘要参与下一次压缩，避免多次滑窗后完全遗忘更早的关键信息。
            older_messages = [
                {"role": "system", "content": f"Previous summary: {self.summary}"},
                *older_messages,
            ]
        # 调用外部注入摘要器。
        self.summary = summarize(older_messages)
        # 保留最近消息窗口。
        self.messages = self.messages[overflow_count:]

    def build_context(self) -> list[JsonObject]:
        """按“工作状态 → 历史摘要 → 近期原文”的顺序构建模型上下文。"""

        # 先放状态，让模型优先读取稳定结构化信息。
        context = [
            {
                "role": "system",
                "content": "Working state: "
                + json.dumps(self.working_state, ensure_ascii=False, sort_keys=True),
            }
        ]
        # 有摘要时注入历史摘要。
        if self.summary:
            context.append({"role": "system", "content": f"History summary: {self.summary}"})
        # 再拼接近期原始消息（深拷贝防止外部误改内部状态）。
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
        # 任务标识。
        self.task_id = task_id
        # 任务目标文本。
        self.goal = goal
        # 追加式事件流。
        self.events: list[StateEvent] = []
        # 步骤结果索引。
        self.results: dict[str, StepResult] = {}
        # 路由/交接历史。
        self.route_history: list[str] = []
        # 共享状态互斥锁。
        self.lock = threading.RLock()

    def append_event(self, agent_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        """以单调递增序号追加事件，保留完整执行轨迹。"""

        with self.lock:
            # sequence 由当前事件数 + 1 生成，保证单调增长。
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
            # 结果不可覆盖，防止并发或重复执行污染状态。
            if result.step_id in self.results:
                raise ValueError(f"step result already exists: {result.step_id}")
            # 保存步骤结果。
            self.results[result.step_id] = result
            # 同步写入完成事件。
            self.append_event(result.worker, "step.completed", asdict(result))

    def snapshot(self) -> JsonObject:
        """生成可 JSON 序列化的状态快照，供 checkpoint 和审计使用。"""

        with self.lock:
            # 把 dataclass 转 dict，生成可持久化快照。
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
        # worker 名称 -> 可调用对象。
        self.workers: dict[str, Worker] = {}

    def register(self, name: str, worker: Worker) -> None:
        """注册 Worker；重复名称视为配置错误。"""

        # 避免覆盖已有 worker。
        if name in self.workers:
            raise ValueError(f"worker already registered: {name}")
        # 注册新 worker。
        self.workers[name] = worker

    def execute(self, name: str, payload: JsonObject, state: SharedState) -> JsonObject:
        """按白名单名称执行 Worker，拒绝未知目标。"""

        # 查询白名单 worker。
        worker = self.workers.get(name)
        # 未注册则拒绝执行。
        if worker is None:
            raise ValueError(f"unknown worker: {name}")
        # 执行 worker。
        return worker(payload, state)

    @property
    def names(self) -> set[str]:
        """返回注册名称副本，供 Router 构造允许列表。"""

        # 返回副本，避免外部直接修改内部字典。
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
        # 允许的 worker 白名单。
        self.allowed_workers = set(allowed_workers)
        # 静态规则映射（task_type -> worker）。
        self.static_rules = dict(static_rules)
        # 动态路由器（可选）。
        self.dynamic_router = dynamic_router
        # 静态/动态都无法确定时的保底 worker。
        self.fallback_worker = fallback_worker
        # fallback 必须在 allowlist 中。
        if fallback_worker not in self.allowed_workers:
            raise ValueError("fallback_worker must be allowed")
        # 静态规则中的 worker 也必须都在 allowlist 中。
        unknown = set(self.static_rules.values()) - self.allowed_workers
        if unknown:
            raise ValueError(f"static rules reference unknown workers: {sorted(unknown)}")

    def route(self, task_type: str, context: JsonObject) -> str:
        """选择 Worker；动态结果越权时自动退回 fallback_worker。"""

        # 优先静态规则。
        static_worker = self.static_rules.get(task_type)
        if static_worker:
            return static_worker
        # 没有动态路由器时直接 fallback。
        if self.dynamic_router is None:
            return self.fallback_worker
        # 调用动态路由。
        selected = self.dynamic_router(context, set(self.allowed_workers))
        # 动态结果越权则回退。
        if selected not in self.allowed_workers:
            return self.fallback_worker
        # 返回合法动态选择。
        return selected


class HandoffGuard:
    """限制 Agent 间转交目标与次数，并阻止明显的重复路由。"""

    def __init__(self, allowed_workers: Iterable[str], max_handoffs: int = 6) -> None:
        # 可交接目标白名单。
        self.allowed_workers = set(allowed_workers)
        # 交接预算上限。
        self.max_handoffs = max_handoffs

    def handoff(self, state: SharedState, next_worker: str) -> None:
        """校验并记录一次 Handoff，违反白名单或预算时立即失败。"""

        # 目标必须在允许列表。
        if next_worker not in self.allowed_workers:
            raise ValueError(f"handoff target is not allowed: {next_worker}")
        # 交接次数不能超预算。
        if len(state.route_history) >= self.max_handoffs:
            raise RuntimeError("handoff budget exceeded")
        # 防止明显的 A->A->A 重复交接环。
        if len(state.route_history) >= 2 and state.route_history[-2:] == [next_worker] * 2:
            raise RuntimeError("handoff loop detected")
        # 记录路由历史。
        state.route_history.append(next_worker)
        # 写入审计事件。
        state.append_event("orchestrator", "handoff", {"to": next_worker})


_BINDING_PATTERN = re.compile(r"^\$\{steps\.([^.]+)\.output\.([^}]+)\}$")


def _lookup_path(value: Any, dotted_path: str) -> Any:
    """沿点分路径读取嵌套 Mapping，例如 ``response.data.id``。"""

    # 从根对象开始逐层下钻。
    current = value
    for part in dotted_path.split("."):
        # 只允许 Mapping 逐层访问键。
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        # 任一层找不到路径即失败。
        raise KeyError(f"path not found: {dotted_path}")
    # 返回最终路径值。
    return current


def resolve_bindings(value: Any, results: Mapping[str, StepResult]) -> Any:
    """递归解析 payload 中的 ``${steps.<id>.output.<path>}`` 引用。"""

    if isinstance(value, str):
        # 字符串尝试匹配绑定表达式。
        match = _BINDING_PATTERN.match(value)
        if not match:
            # 不是绑定表达式则原样返回。
            return value
        step_id, dotted_path = match.groups()
        # 被引用步骤结果必须已存在。
        if step_id not in results:
            raise KeyError(f"step result not available: {step_id}")
        # 从该步骤 output 中按路径取值。
        return _lookup_path(results[step_id].output, dotted_path)
    if isinstance(value, list):
        # 列表递归解析。
        return [resolve_bindings(item, results) for item in value]
    if isinstance(value, Mapping):
        # 字典递归解析。
        return {key: resolve_bindings(item, results) for key, item in value.items()}
    # 其他类型直接返回。
    return value


def validate_plan(steps: Sequence[PlanStep]) -> None:
    """校验步骤 ID、依赖完整性，并通过 DFS 检测依赖环。"""

    # 收集所有 step_id。
    step_ids = [step.step_id for step in steps]
    # 检查重复 step_id。
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("plan contains duplicate step ids")
    # 已知步骤集合。
    known_ids = set(step_ids)
    for step in steps:
        # 每个步骤依赖都必须可解析到已知 step_id。
        missing = set(step.depends_on) - known_ids
        if missing:
            raise ValueError(f"step {step.step_id} has missing dependencies: {sorted(missing)}")

    # DFS 环检测状态：当前递归栈。
    visiting: set[str] = set()
    # DFS 环检测状态：已完成节点。
    visited: set[str] = set()
    # step_id -> PlanStep 索引。
    by_id = {step.step_id: step for step in steps}

    def visit(step_id: str) -> None:
        # visiting 表示当前递归栈；再次遇到栈内节点即可证明存在依赖环。
        if step_id in visiting:
            raise ValueError("plan contains a dependency cycle")
        if step_id in visited:
            return
        # 入栈。
        visiting.add(step_id)
        # 深度优先访问依赖。
        for dependency in by_id[step_id].depends_on:
            visit(dependency)
        # 出栈并标记完成。
        visiting.remove(step_id)
        visited.add(step_id)

    # 对所有节点启动 DFS，确保非连通图也能被完整检测。
    for step_id in step_ids:
        visit(step_id)


def check_acceptance(output: JsonObject, criteria: Mapping[str, Any]) -> tuple[bool, str | None]:
    """逐项检查业务输出；返回是否通过以及首个失败原因。"""

    # 遍历所有验收条件。
    for dotted_path, expected in criteria.items():
        try:
            # 从输出中读取验收路径值。
            actual = _lookup_path(output, dotted_path)
        except KeyError:
            # 路径不存在则验收失败。
            return False, f"acceptance path missing: {dotted_path}"
        # 值不一致则验收失败。
        if actual != expected:
            return False, f"acceptance failed for {dotted_path}: expected {expected!r}, got {actual!r}"
    # 所有条件都通过。
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
        # worker 执行注册表。
        self.registry = registry
        # 最大并行 worker 数。
        self.max_workers = max_workers
        # 最大重规划次数。
        self.max_replans = max_replans
        # checkpoint 文件路径（可选）。
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

        # 复制计划，避免直接修改调用方传入对象。
        active_plan = list(plan)
        # 先做一次计划合法性校验。
        validate_plan(active_plan)
        # 初始化共享状态。
        state = SharedState(task_id, goal)
        # 当前已发生 replan 次数。
        replan_count = 0

        while True:
            # 找出尚未执行的步骤。
            pending = [step for step in active_plan if step.step_id not in state.results]
            # 没有待执行步骤，任务完成。
            if not pending:
                return state
            # 仅选择“依赖已执行且验收通过”的 ready 步骤。
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

            # 并发执行一批 ready 步骤。
            batch_results = self._execute_ready_steps(ready, state)
            # 记录首个失败结果（用于后续决策 replan/fail）。
            failed_result: StepResult | None = None
            failed_step: PlanStep | None = None
            for step, result in batch_results:
                # 持久化每个步骤结果。
                state.save_result(result)
                # 只保留第一个失败样本用于策略判定。
                if not result.accepted and failed_result is None:
                    failed_result = result
                    failed_step = step
            # 每批执行后写 checkpoint。
            self._write_checkpoint(state)

            # 本批无失败，继续下一轮调度。
            if failed_result is None:
                continue
            # 命中 replan 条件：策略允许、replanner 存在、预算未耗尽。
            if (
                failed_step is not None
                and failed_step.on_failure == "replan"
                and replanner is not None
                and replan_count < self.max_replans
            ):
                # 消耗一次 replan 预算。
                replan_count += 1
                # 让 replanner 生成计划补丁。
                revised_plan = list(replanner(active_plan, state, failed_result))
                # 已完成步骤集合。
                completed_ids = set(state.results)
                # 已执行步骤不可由 Replanner 覆盖，否则审计结果和真实副作用会失配。
                if any(step.step_id in completed_ids for step in revised_plan):
                    raise ValueError("replanner must return only new, unexecuted steps")
                # 新计划 = 已完成步骤 + 新补丁步骤。
                active_plan = [
                    step for step in active_plan if step.step_id in completed_ids
                ] + revised_plan
                # 合并后再次校验计划合法性。
                validate_plan(active_plan)
                # 记录计划重写事件。
                state.append_event(
                    "orchestrator",
                    "plan.revised",
                    {"failed_step": failed_result.step_id, "replan_count": replan_count},
                )
                continue
            # 不可恢复失败：抛出更贴近业务语义的错误信息。
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

        # 收集 (step, result) 元组。
        outcomes: list[tuple[PlanStep, StepResult]] = []
        # 线程池大小不超过 ready 数量，避免空转线程。
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(ready))) as executor:
            # 提交每个步骤执行任务并保存 future->step 对照表。
            future_to_step = {
                executor.submit(self._execute_step, step, state): step for step in ready
            }
            # 按完成先后收集结果，提升吞吐。
            for future in as_completed(future_to_step):
                step = future_to_step[future]
                outcomes.append((step, future.result()))
        # 为了结果稳定性，最终按 step_id 排序返回。
        outcomes.sort(key=lambda item: item[0].step_id)
        return outcomes

    def _execute_step(self, step: PlanStep, state: SharedState) -> StepResult:
        """解析输入、调用 Worker，再把技术成功和业务验收分别记录。"""

        # 先记录步骤启动事件。
        state.append_event(step.worker, "step.started", {"step_id": step.step_id})
        try:
            # 执行前解析 payload 中的跨步骤绑定。
            payload = resolve_bindings(step.payload, state.results)
            # 调用注册表中的目标 worker。
            output = self.registry.execute(step.worker, payload, state)
            # 对 worker 输出执行业务验收。
            accepted, acceptance_error = check_acceptance(output, step.success_criteria)
            # 技术执行成功（ok=True），验收是否通过由 accepted 决定。
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

        # 未配置路径则跳过持久化。
        if self.checkpoint_path is None:
            return
        # 确保目录存在。
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件，防止写入中断导致主文件损坏。
        temporary_path = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(state.snapshot(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 原子替换主文件。
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
        # 轮次上限必须为正。
        if max_rounds <= 0:
            raise ValueError("max_rounds must be positive")
        # 保存最大轮次数。
        self.max_rounds = max_rounds
        # 保存最低进步阈值。
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

        # 当前候选输出。
        current_output = initial_output
        # 每轮历史记录。
        history: list[ReflectionRound] = []
        # 上一轮分数（-1 表示尚未开始）。
        previous_score = -1.0
        # 当前最优输出与最优分。
        best_output = current_output
        best_score = -1.0

        # 从第 1 轮到 max_rounds 轮依次执行。
        for round_index in range(1, self.max_rounds + 1):
            # 调用评价器评估当前候选。
            evaluation = evaluator(task, current_output, rubric)
            # 记录本轮轨迹。
            history.append(ReflectionRound(round_index, current_output, evaluation))
            # 若分数刷新则更新最优结果。
            if evaluation.score > best_score:
                best_score = evaluation.score
                best_output = current_output
            # 通过即提前结束。
            if evaluation.passed:
                return ReflectionResult(current_output, True, tuple(history))
            # 连续两轮提升不足阈值，提前停止。
            if previous_score >= 0 and evaluation.score - previous_score < self.minimum_improvement:
                # 连续两轮提升不足，继续调用模型通常只会增加成本和漂移风险。
                break
            # 记录上一轮分数，用于下一轮比较。
            previous_score = evaluation.score
            # 达到最后一轮后不再调用 improver。
            if round_index == self.max_rounds:
                break
            # 生成下一轮候选输出。
            current_output = improver(task, current_output, evaluation)

        # 未通过时返回“历史中被评价过的最佳输出”。
        return ReflectionResult(best_output, False, tuple(history))


def deterministic_summary(messages: Sequence[JsonObject]) -> str:
    """供 Demo 和测试使用的确定性离线摘要器，不代表生产摘要质量。"""

    # 过滤空内容消息并格式化为 role: content。
    parts = [
        f"{message.get('role', 'unknown')}: {str(message.get('content', '')).strip()}"
        for message in messages
        if str(message.get("content", "")).strip()
    ]
    # 用分隔符拼接成摘要文本。
    return " | ".join(parts)


def run_demo() -> JsonObject:
    """离线运行一个“压缩上下文 → DAG 调度 → 结果绑定”的完整示例。"""

    # 构造短期记忆并设置窗口大小。
    memory = ShortTermMemory(max_recent_messages=2)
    # 更新结构化目标。
    memory.update_state(goal="research and summarize an endpoint")
    # 追加几条消息模拟对话上下文。
    memory.add("user", "Find endpoint details")
    memory.add("assistant", "I will research first")
    memory.add("tool", "endpoint=/orders/{id}")
    # 触发压缩，保留最近窗口。
    memory.compress(deterministic_summary)

    # 初始化 worker 注册表。
    registry = WorkerRegistry()
    # 注册 research worker。
    registry.register(
        "researcher",
        lambda payload, _state: {"ok": True, "endpoint": payload["endpoint"]},
    )
    # 注册 writer worker。
    registry.register(
        "writer",
        lambda payload, _state: {"ok": True, "summary": f"Use {payload['endpoint']}"},
    )
    # 构造两步计划：先 research 再 write。
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
    # 运行 DAG 调度。
    state = DAGOrchestrator(registry).run(
        task_id="demo-task",
        goal="research and summarize an endpoint",
        plan=plan,
    )
    # 返回上下文与状态快照，便于观察结果。
    return {"context": memory.build_context(), "state": state.snapshot()}


if __name__ == "__main__":
    # 脚本入口：打印离线 demo 的 JSON 输出。
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))