import threading
import unittest

from langgraph.checkpoint.memory import MemorySaver

from xiaolinnote_agent_analysis.examples.agent_capabilities_langgraph import (
    LangGraphDAGOrchestrator,
    LangGraphMemoryWorkflow,
    LangGraphMultiAgentRouter,
    LangGraphReflectionWorkflow,
    thread_config,
)
from xiaolinnote_agent_analysis.examples.agent_capabilities_reference import (
    Evaluation,
    HybridRouter,
    PlanStep,
    SQLiteMemoryStore,
    WorkerRegistry,
)


class LangGraphMemoryWorkflowTests(unittest.TestCase):
    def test_recall_is_scoped_and_old_messages_are_compressed(self) -> None:
        with SQLiteMemoryStore() as store:
            store.add_memory(
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
            workflow = LangGraphMemoryWorkflow(store)
            result = workflow.invoke(
                thread_id="memory-alice",
                tenant_id="tenant-a",
                user_id="alice",
                query="Python examples",
                messages=[
                    {"role": "user", "content": "old question"},
                    {"role": "assistant", "content": "old answer"},
                    {"role": "user", "content": "recent question"},
                    {"role": "assistant", "content": "recent answer"},
                ],
                max_recent_messages=2,
            )

            self.assertEqual(len(result["recalled_memories"]), 1)
            self.assertIn("Alice", result["recalled_memories"][0]["content"])
            self.assertIn("old question", result["summary"])
            self.assertEqual(
                [message["content"] for message in result["messages"]],
                ["recent question", "recent answer"],
            )

    def test_checkpointer_keeps_thread_states_isolated(self) -> None:
        checkpointer = MemorySaver()
        with SQLiteMemoryStore() as store:
            workflow = LangGraphMemoryWorkflow(store, checkpointer=checkpointer)
            workflow.invoke(
                thread_id="thread-a",
                tenant_id="tenant",
                user_id="alice",
                query="nothing",
                messages=[{"role": "user", "content": "A"}],
            )
            workflow.invoke(
                thread_id="thread-b",
                tenant_id="tenant",
                user_id="bob",
                query="nothing",
                messages=[{"role": "user", "content": "B"}],
            )

            state_a = workflow.graph.get_state(thread_config("thread-a")).values
            state_b = workflow.graph.get_state(thread_config("thread-b")).values
            self.assertEqual(state_a["user_id"], "alice")
            self.assertEqual(state_b["user_id"], "bob")


class LangGraphDAGOrchestratorTests(unittest.TestCase):
    def test_cycle_is_rejected_before_workers_run(self) -> None:
        registry = WorkerRegistry()
        registry.register("worker", lambda _payload, _state: {"ok": True})

        with self.assertRaisesRegex(ValueError, "cycle"):
            LangGraphDAGOrchestrator(registry).run(
                task_id="cyclic-dag",
                goal="reject cycle",
                plan=[
                    PlanStep("a", "A", "worker", depends_on=("b",)),
                    PlanStep("b", "B", "worker", depends_on=("a",)),
                ],
            )

    def test_send_executes_ready_steps_in_parallel_and_binds_results(self) -> None:
        registry = WorkerRegistry()
        barrier = threading.Barrier(2, timeout=2)

        def source(payload, _state):
            barrier.wait()
            _state.append_event("source", "worker.observed", {"value": payload["value"]})
            return {"ok": True, "value": payload["value"]}

        registry.register("source", source)
        registry.register(
            "sum",
            lambda payload, _state: {
                "ok": True,
                "value": payload["left"] + payload["right"],
            },
        )
        result = LangGraphDAGOrchestrator(registry).run(
            task_id="parallel-dag",
            goal="sum parallel values",
            plan=[
                PlanStep("left", "left", "source", payload={"value": 20}),
                PlanStep("right", "right", "source", payload={"value": 22}),
                PlanStep(
                    "sum",
                    "sum",
                    "sum",
                    payload={
                        "left": "${steps.left.output.value}",
                        "right": "${steps.right.output.value}",
                    },
                    depends_on=("left", "right"),
                    success_criteria={"value": 42},
                ),
            ],
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.results["sum"].output["value"], 42)
        self.assertEqual(
            sum(event["event_type"] == "worker.observed" for event in result.events),
            2,
        )
        checkpoint = result.graph_state
        self.assertEqual(checkpoint["status"], "completed")

    def test_acceptance_failure_routes_to_bounded_replan(self) -> None:
        registry = WorkerRegistry()
        registry.register("worker", lambda payload, _state: {"ok": payload["ok"]})
        replans = []

        def replan(_plan, _state, failed):
            replans.append(failed.step_id)
            return [PlanStep("recovery", "recover", "worker", payload={"ok": True})]

        result = LangGraphDAGOrchestrator(
            registry,
            max_replans=1,
            replanner=replan,
        ).run(
            task_id="replan-dag",
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
        )

        self.assertEqual(replans, ["initial"])
        self.assertEqual(result.replan_count, 1)
        self.assertFalse(result.results["initial"].accepted)
        self.assertTrue(result.results["recovery"].accepted)
        self.assertEqual(result.status, "completed")


class LangGraphMultiAgentRouterTests(unittest.TestCase):
    def test_untrusted_dynamic_route_falls_back_to_allowlisted_worker(self) -> None:
        registry = WorkerRegistry()
        registry.register("researcher", lambda _payload, _state: {"output": {"worker": "researcher"}})
        registry.register("writer", lambda _payload, _state: {"output": {"worker": "writer"}})
        router = HybridRouter(
            allowed_workers=registry.names,
            static_rules={},
            dynamic_router=lambda _context, _allowed: "untrusted-worker",
            fallback_worker="writer",
        )

        result = LangGraphMultiAgentRouter(registry, router).run(
            task_id="routing-fallback",
            goal="stay allowlisted",
            task_type="unknown",
            payload={},
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.route_history, ("writer",))
        self.assertEqual(result.output, {"worker": "writer"})

    def test_allowlisted_route_and_handoff_reach_the_next_worker(self) -> None:
        registry = WorkerRegistry()
        registry.register(
            "researcher",
            lambda payload, _state: {
                "output": {"research": "facts"},
                "payload": {**payload, "research": "facts"},
                "handoff_to": "writer",
            },
        )
        registry.register(
            "writer",
            lambda payload, _state: {
                "output": {"answer": f"report from {payload['research']}"}
            },
        )
        router = HybridRouter(
            allowed_workers=registry.names,
            static_rules={"research": "researcher"},
            fallback_worker="writer",
        )
        result = LangGraphMultiAgentRouter(registry, router).run(
            task_id="routing-1",
            goal="research and write",
            task_type="research",
            payload={"topic": "LangGraph"},
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.route_history, ("researcher", "writer"))
        self.assertEqual(result.output["answer"], "report from facts")

    def test_handoff_budget_stops_a_worker_loop(self) -> None:
        registry = WorkerRegistry()
        registry.register("a", lambda payload, _state: {"handoff_to": "b", "payload": payload})
        registry.register("b", lambda payload, _state: {"handoff_to": "a", "payload": payload})
        router = HybridRouter(
            allowed_workers=registry.names,
            static_rules={"loop": "a"},
            fallback_worker="a",
        )
        result = LangGraphMultiAgentRouter(
            registry,
            router,
            max_handoffs=2,
        ).run(
            task_id="routing-loop",
            goal="stop loop",
            task_type="loop",
            payload={},
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("budget", result.error)
        self.assertEqual(result.route_history, ("a", "b"))


class LangGraphReflectionWorkflowTests(unittest.TestCase):
    def test_evaluator_optimizer_loop_stops_when_output_passes(self) -> None:
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
            thread_id="reflection-pass",
            task="answer",
            initial_output="claim",
            rubric={"requires": "evidence"},
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.output, "claim with evidence")
        self.assertEqual(len(result.rounds), 2)

    def test_last_unchecked_improvement_is_not_returned(self) -> None:
        improvements = []
        workflow = LangGraphReflectionWorkflow(
            evaluator=lambda _task, _output, _rubric: Evaluation(False, 0.1),
            improver=lambda _task, output, _evaluation: improvements.append(output) or "unchecked",
            max_rounds=1,
        )
        result = workflow.run(
            thread_id="reflection-bounded",
            task="answer",
            initial_output="draft",
            rubric={},
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.output, "draft")
        self.assertEqual(improvements, [])
        self.assertEqual(len(result.rounds), 1)


if __name__ == "__main__":
    unittest.main()