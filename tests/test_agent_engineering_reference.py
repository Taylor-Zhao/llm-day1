import tempfile
import unittest
from collections import deque
from pathlib import Path

from xiaolinnote_agent_engineering_analysis.examples.agent_engineering_reference import (
    AtomicCheckpointStore,
    ControlledToolRegistry,
    DualLevelGraphIndex,
    GateResult,
    LoopAction,
    LoopCandidate,
    LoopEconomics,
    LoopHarness,
    LoopLimits,
    MarkdownAgentWorkspace,
    ToolDefinition,
    WorkspaceError,
    assess_loop_candidate,
)


class MarkdownAgentWorkspaceTests(unittest.TestCase):
    def test_context_uses_relevant_memory_and_recent_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = MarkdownAgentWorkspace(directory)
            workspace.bootstrap(
                {
                    "SOUL.md": "# SOUL\nBe precise.",
                    "AGENTS.md": "# AGENTS\nRun tests.",
                    "TOOLS.md": "# TOOLS\nread only",
                    "HEARTBEAT.md": "# HEARTBEAT\nCheck CI.",
                    "MEMORY.md": "# MEMORY\n- Alice prefers Python\n- Bob prefers Java\n",
                }
            )
            workspace.record_session("2026-07-30", "old session")
            workspace.record_session("2026-07-31", "recent session")

            context = workspace.build_context("Alice Python", recent_session_limit=1)

            self.assertEqual(context.memories, ("- Alice prefers Python",))
            self.assertEqual(context.recent_sessions, ("recent session",))
            self.assertIn("Run tests", context.render())

    def test_workspace_blocks_path_escape_and_deduplicates_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = MarkdownAgentWorkspace(directory)
            workspace.bootstrap()
            self.assertTrue(workspace.remember("Use Python 3.12"))
            self.assertFalse(workspace.remember("Use Python 3.12"))
            with self.assertRaisesRegex(WorkspaceError, "outside"):
                workspace.read("../secret.txt")


class DualLevelGraphIndexTests(unittest.TestCase):
    def build_index(self) -> DualLevelGraphIndex:
        index = DualLevelGraphIndex()
        index.upsert_entity("Vendor A", "European supplier", "vendors.md")
        index.upsert_entity("Audit 2026", "security audit failed", "audit.md")
        index.upsert_entity("PII", "personal data", "contract.md")
        index.upsert_relation(
            "Vendor A",
            "Audit 2026",
            "Vendor A failed the security audit",
            keywords=("risk", "security"),
            source_id="audit.md",
        )
        index.upsert_relation(
            "Vendor A",
            "PII",
            "Vendor A processes personal data",
            keywords=("privacy", "data processing"),
            source_id="contract.md",
        )
        return index

    def test_local_and_global_retrieval_use_entities_and_relations(self) -> None:
        index = self.build_index()
        local = index.local_search(["Vendor A"])
        global_hits = index.global_search(["privacy"])
        self.assertEqual(local[0].kind, "entity")
        self.assertTrue(any(hit.key == "Vendor A->PII" for hit in global_hits))

    def test_entity_upsert_preserves_provenance_and_multi_hop_is_bounded(self) -> None:
        index = self.build_index()
        index.upsert_entity("vendor   a", "handles contracts", "contracts.md")
        entity = index.entities["vendor a"]
        self.assertEqual(entity.source_ids, {"vendors.md", "contracts.md"})
        paths = index.multi_hop_paths("Vendor A", max_depth=1)
        self.assertEqual(set(paths), {("Vendor A", "Audit 2026"), ("Vendor A", "PII")})


class ControlledToolRegistryTests(unittest.TestCase):
    def test_dangerous_tool_requires_approval_and_call_id_is_idempotent(self) -> None:
        calls = []
        tools = ControlledToolRegistry()
        tools.register(
            ToolDefinition("publish", dangerous=True, idempotent=True),
            lambda value: calls.append(value) or {"published": value},
        )
        denied = tools.execute(call_id="1", name="publish", arguments={"value": "draft"})
        first = tools.execute(
            call_id="2",
            name="publish",
            arguments={"value": "final"},
            approved_tools={"publish"},
        )
        second = tools.execute(
            call_id="2",
            name="publish",
            arguments={"value": "changed"},
            approved_tools={"publish"},
        )
        self.assertFalse(denied.ok)
        self.assertTrue(first.ok)
        self.assertEqual(second, first)
        self.assertEqual(calls, ["final"])


class LoopHarnessTests(unittest.TestCase):
    def test_loop_completes_only_after_independent_gate_passes(self) -> None:
        tools = ControlledToolRegistry()
        actions = deque([LoopAction.finish("draft"), LoopAction.finish("verified")])
        harness = LoopHarness(tools)
        state = harness.run(
            run_id="run-1",
            goal="produce verified output",
            policy=lambda _state: actions.popleft(),
            verifier=lambda output, _state: GateResult(
                output == "verified",
                evidence=("unit test passed",) if output == "verified" else (),
                errors=("unit test failed",) if output != "verified" else (),
            ),
        )
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.failures, 1)
        self.assertEqual(state.gate_evidence, ["unit test passed"])

    def test_token_budget_stops_before_unbounded_work(self) -> None:
        harness = LoopHarness(
            ControlledToolRegistry(),
            limits=LoopLimits(max_tokens=10),
        )
        state = harness.run(
            run_id="run-2",
            goal="bounded",
            policy=lambda _state: LoopAction.finish("answer", tokens_used=11),
            verifier=lambda _output, _state: GateResult(True),
        )
        self.assertEqual(state.status, "budget_exhausted")
        self.assertEqual(state.stop_reason, "token budget exhausted")

    def test_repeated_action_detection_blocks_a_stuck_loop(self) -> None:
        tools = ControlledToolRegistry()
        tools.register(ToolDefinition("read"), lambda path: path)
        harness = LoopHarness(
            tools,
            limits=LoopLimits(max_repeated_action=2, max_iterations=6),
        )
        state = harness.run(
            run_id="run-3",
            goal="avoid repetition",
            policy=lambda _state: LoopAction.tool("read", {"path": "same"}, call_id="same"),
            verifier=lambda _output, _state: GateResult(False),
        )
        self.assertEqual(state.status, "blocked")
        self.assertEqual(state.iteration, 3)

    def test_checkpoint_can_resume_in_a_fresh_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = AtomicCheckpointStore(Path(directory) / "state.json")
            tools = ControlledToolRegistry()
            tools.register(ToolDefinition("collect"), lambda: {"value": 42})
            first = LoopHarness(
                tools,
                limits=LoopLimits(max_iterations=1),
                checkpoint=checkpoint,
            ).run(
                run_id="run-4",
                goal="resume",
                policy=lambda _state: LoopAction.tool("collect", {}, call_id="collect-1"),
                verifier=lambda _output, _state: GateResult(False),
            )
            self.assertEqual(first.status, "budget_exhausted")

            resumed = LoopHarness(
                tools,
                limits=LoopLimits(max_iterations=3),
                checkpoint=checkpoint,
            ).run(
                run_id="run-4",
                goal="resume",
                policy=lambda state: LoopAction.finish(state.observations[-1]["value"]),
                verifier=lambda output, _state: GateResult(output == {"value": 42}, evidence=("state restored",)),
            )
            self.assertEqual(resumed.status, "completed")
            self.assertEqual(resumed.iteration, 2)

    def test_explicit_escalation_stops_without_claiming_completion(self) -> None:
        state = LoopHarness(ControlledToolRegistry()).run(
            run_id="run-5",
            goal="high risk",
            policy=lambda _state: LoopAction.escalate("payment change needs approval"),
            verifier=lambda _output, _state: GateResult(False),
        )
        self.assertEqual(state.status, "escalated")
        self.assertIn("approval", state.stop_reason)


class LoopHandbookTests(unittest.TestCase):
    def test_candidate_requires_repetition_gate_observability_budget_and_review(self) -> None:
        result = assess_loop_candidate(
            LoopCandidate(
                repetitions_per_week=1,
                has_objective_gate=False,
                agent_can_execute_and_observe=False,
                token_budget=0,
                risk_level="high",
                irreversible_actions_require_approval=False,
                human_will_review=False,
            )
        )
        self.assertFalse(result.eligible)
        self.assertGreaterEqual(len(result.reasons), 6)

    def test_economics_includes_review_time_and_uses_configurable_threshold(self) -> None:
        economics = LoopEconomics(
            proposed_changes=10,
            accepted_changes=6,
            model_and_compute_cost=12.0,
            review_minutes=30,
            reviewer_hourly_cost=60.0,
        )
        self.assertEqual(economics.acceptance_rate, 0.6)
        self.assertEqual(economics.total_cost, 42.0)
        self.assertEqual(economics.cost_per_accepted_change, 7.0)
        self.assertTrue(economics.meets_threshold(0.5))
        self.assertFalse(economics.meets_threshold(0.7))


if __name__ == "__main__":
    unittest.main()