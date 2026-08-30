import unittest

from xiaolinnote_langchain_analysis.examples.langchain_capabilities_reference import (
    AgentLoop,
    DeepResearchEngine,
    Evidence,
    InMemoryCheckpointer,
    LongTermStore,
    RuleBasedPlanner,
    RuntimeContext,
    ToolRegistry,
    build_balance_tool,
    build_runnable_chain,
    lookup_order,
)


class RunnableChainTests(unittest.TestCase):
    def test_chain_normalizes_input_and_routes_order_question(self) -> None:
        result = build_runnable_chain().invoke({"question": "  查询   订单 A100  "})
        self.assertEqual(result["question"], "查询 订单 A100")
        self.assertEqual(result["route"], "order")
        self.assertIn("订单查询工具", result["answer"])

    def test_chain_rejects_empty_question(self) -> None:
        with self.assertRaisesRegex(ValueError, "question cannot be empty"):
            build_runnable_chain().invoke({"question": "  "})


class ToolAndAgentTests(unittest.TestCase):
    def test_tool_registry_exposes_schema_and_executes_tool(self) -> None:
        registry = ToolRegistry([lookup_order])
        schema = registry.schemas()[0]
        result = registry.execute("lookup_order", {"order_id": "A100", "detail": "full"})
        self.assertEqual(schema["name"], "lookup_order")
        self.assertIn("order_id", schema["parameters"]["properties"])
        self.assertEqual(result["carrier"], "demo-express")

    def test_runtime_context_is_not_part_of_model_visible_arguments(self) -> None:
        context = RuntimeContext("tenant-a", "alice", frozenset({"balance:read"}))
        registry = ToolRegistry([build_balance_tool(context)])
        schema = registry.schemas()[0]["parameters"]
        result = registry.execute("get_balance", {"account_type": "cash"})
        self.assertNotIn("user_id", schema["properties"])
        self.assertEqual(result["user_id"], "alice")

    def test_agent_loop_returns_tool_result_to_planner(self) -> None:
        run = AgentLoop(RuleBasedPlanner(), ToolRegistry([lookup_order])).run("订单 A100 到哪了？")
        self.assertIn("shipped", run.answer)
        self.assertEqual(run.tool_results[0]["name"], "lookup_order")
        self.assertEqual(run.tool_results[0]["result"]["order_id"], "A100")


class MemoryScopeTests(unittest.TestCase):
    def test_checkpointer_isolates_thread_state(self) -> None:
        checkpointer = InMemoryCheckpointer()
        checkpointer.save("thread-a", [{"role": "user", "content": "A"}])
        checkpointer.save("thread-b", [{"role": "user", "content": "B"}])
        self.assertEqual(checkpointer.load("thread-a")[0]["content"], "A")
        self.assertEqual(checkpointer.load("thread-b")[0]["content"], "B")

    def test_store_isolates_long_term_memory_by_namespace(self) -> None:
        store = LongTermStore()
        store.put(("tenant-a", "alice", "preferences"), "language", {"value": "Python"})
        store.put(("tenant-a", "bob", "preferences"), "language", {"value": "Java"})
        self.assertEqual(
            store.get(("tenant-a", "alice", "preferences"), "language"),
            {"value": "Python"},
        )
        self.assertEqual(
            store.get(("tenant-a", "bob", "preferences"), "language"),
            {"value": "Java"},
        )


class DeepResearchTests(unittest.TestCase):
    def test_parallel_research_filters_weak_evidence_and_reports_gaps(self) -> None:
        evidence = {
            "架构": [Evidence("架构", "有状态图", "official", 0.9)],
            "成本": [Evidence("成本", "可能更贵", "blog", 0.3)],
        }
        result = DeepResearchEngine(
            planner=lambda _question: ["架构", "成本"],
            researcher=lambda topic: evidence[topic],
            min_confidence=0.6,
        ).run("比较 Agent 架构")
        self.assertEqual([item.topic for item in result.evidence], ["架构"])
        self.assertEqual(result.gaps, ("成本",))
        self.assertIn("证据缺口", result.report)


if __name__ == "__main__":
    unittest.main()