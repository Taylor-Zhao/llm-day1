import tempfile
import unittest
from pathlib import Path

from xiaolinnote_agent_analysis.examples.agent_capabilities_reference import (
    DAGOrchestrator,
    Evaluation,
    HandoffGuard,
    HybridRouter,
    PlanStep,
    ReflectionEngine,
    SQLiteMemoryStore,
    SharedState,
    ShortTermMemory,
    WorkerRegistry,
    deterministic_summary,
    validate_plan,
)


class SQLiteMemoryStoreTests(unittest.TestCase):
    def test_search_is_scoped_and_forgotten_memory_disappears(self) -> None:
        with SQLiteMemoryStore() as store:
            target_id = store.add_memory(
                tenant_id="tenant-a",
                user_id="alice",
                memory_type="episodic",
                content="Alice prefers concise Python examples",
                source="conversation",
                importance=0.9,
            )
            store.add_memory(
                tenant_id="tenant-a",
                user_id="bob",
                memory_type="episodic",
                content="Bob prefers concise Python examples",
                source="conversation",
            )

            results = store.search(
                tenant_id="tenant-a",
                user_id="alice",
                query="Python examples",
            )

            self.assertEqual([record.memory_id for record in results], [target_id])
            self.assertFalse(
                store.forget(tenant_id="tenant-a", user_id="bob", memory_id=target_id)
            )
            self.assertTrue(
                store.forget(tenant_id="tenant-a", user_id="alice", memory_id=target_id)
            )
            self.assertEqual(
                store.search(
                    tenant_id="tenant-a",
                    user_id="alice",
                    query="Python examples",
                ),
                [],
            )

    def test_entity_fact_is_updated_without_cross_user_overwrite(self) -> None:
        with SQLiteMemoryStore() as store:
            first_id = store.upsert_fact(
                tenant_id="tenant-a",
                user_id="alice",
                key="language",
                value="Python",
                source="profile",
            )
            second_id = store.upsert_fact(
                tenant_id="tenant-a",
                user_id="alice",
                key="language",
                value="Go",
                source="conversation",
            )
            store.upsert_fact(
                tenant_id="tenant-a",
                user_id="bob",
                key="language",
                value="Java",
                source="profile",
            )

            self.assertEqual(first_id, second_id)
            self.assertEqual(
                store.get_facts(tenant_id="tenant-a", user_id="alice"),
                {"language": "Go"},
            )


class ShortTermMemoryTests(unittest.TestCase):
    def test_compression_keeps_recent_messages_and_structured_state(self) -> None:
        memory = ShortTermMemory(max_recent_messages=2)
        memory.update_state(goal="answer question", completed_steps=["retrieve"])
        memory.add("user", "old question")
        memory.add("assistant", "old answer")
        memory.add("user", "recent question")
        memory.add("assistant", "recent answer")

        memory.compress(deterministic_summary)
        context = memory.build_context()

        self.assertIn("completed_steps", context[0]["content"])
        self.assertIn("old question", context[1]["content"])
        self.assertEqual(
            [message["content"] for message in context[-2:]],
            ["recent question", "recent answer"],
        )


class DAGOrchestratorTests(unittest.TestCase):
    def test_cycle_is_rejected(self) -> None:
        plan = [
            PlanStep("a", "A", "worker", depends_on=("b",)),
            PlanStep("b", "B", "worker", depends_on=("a",)),
        ]

        with self.assertRaisesRegex(ValueError, "cycle"):
            validate_plan(plan)

    def test_dependency_output_is_bound_and_checkpointed(self) -> None:
        registry = WorkerRegistry()
        registry.register("source", lambda _payload, _state: {"ok": True, "value": 21})
        registry.register(
            "double",
            lambda payload, _state: {"ok": True, "value": payload["number"] * 2},
        )
        plan = [
            PlanStep("source", "produce", "source"),
            PlanStep(
                "double",
                "transform",
                "double",
                payload={"number": "${steps.source.output.value}"},
                depends_on=("source",),
                success_criteria={"value": 42},
            ),
        ]

        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            state = DAGOrchestrator(registry, checkpoint_path=checkpoint).run(
                task_id="task-1",
                goal="calculate",
                plan=plan,
            )

            self.assertTrue(state.results["double"].accepted)
            self.assertEqual(state.results["double"].output["value"], 42)
            self.assertTrue(checkpoint.exists())

    def test_acceptance_failure_invokes_bounded_replan(self) -> None:
        registry = WorkerRegistry()
        registry.register("worker", lambda payload, _state: {"ok": payload["ok"]})
        calls = []

        def replan(_plan, _state, failed_result):
            calls.append(failed_result.step_id)
            return [PlanStep("recovery", "recover", "worker", payload={"ok": True})]

        state = DAGOrchestrator(registry, max_replans=1).run(
            task_id="task-2",
            goal="recover",
            plan=[
                PlanStep(
                    "initial",
                    "try",
                    "worker",
                    payload={"ok": False},
                    on_failure="replan",
                )
            ],
            replanner=replan,
        )

        self.assertEqual(calls, ["initial"])
        self.assertFalse(state.results["initial"].accepted)
        self.assertTrue(state.results["recovery"].accepted)


class RoutingTests(unittest.TestCase):
    def test_dynamic_router_output_is_constrained_by_allowlist(self) -> None:
        router = HybridRouter(
            allowed_workers={"researcher", "writer"},
            static_rules={"research": "researcher"},
            dynamic_router=lambda _context, _allowed: "untrusted-worker",
            fallback_worker="writer",
        )

        self.assertEqual(router.route("research", {}), "researcher")
        self.assertEqual(router.route("unknown", {}), "writer")

    def test_handoff_budget_stops_unbounded_routing(self) -> None:
        state = SharedState("task", "goal")
        guard = HandoffGuard({"researcher", "writer"}, max_handoffs=2)
        guard.handoff(state, "researcher")
        guard.handoff(state, "writer")

        with self.assertRaisesRegex(RuntimeError, "budget"):
            guard.handoff(state, "researcher")


class ReflectionEngineTests(unittest.TestCase):
    def test_failed_output_is_improved_until_it_passes(self) -> None:
        def evaluate(_task, output, _rubric):
            if "evidence" in output:
                return Evaluation(True, 1.0)
            return Evaluation(False, 0.2, ("missing evidence",))

        result = ReflectionEngine(max_rounds=2).run(
            task="answer",
            initial_output="claim",
            rubric={"requires": "evidence"},
            evaluator=evaluate,
            improver=lambda _task, output, _evaluation: output + " with evidence",
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.output, "claim with evidence")
        self.assertEqual(len(result.rounds), 2)

    def test_max_rounds_prevents_an_unchecked_extra_improvement(self) -> None:
        improvements = []

        result = ReflectionEngine(max_rounds=1).run(
            task="answer",
            initial_output="draft",
            rubric={},
            evaluator=lambda _task, _output, _rubric: Evaluation(False, 0.1),
            improver=lambda _task, output, _evaluation: improvements.append(output) or "new",
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.output, "draft")
        self.assertEqual(improvements, [])
        self.assertEqual(len(result.rounds), 1)


if __name__ == "__main__":
    unittest.main()