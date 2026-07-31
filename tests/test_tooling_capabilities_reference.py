import tempfile
import time
import unittest
from pathlib import Path

from xiaolinnote_tools_analysis.examples.tooling_capabilities_reference import (
    A2ATaskStore,
    AgentCard,
    AgentDirectory,
    AgentSkill,
    GatewayRoute,
    LlmGateway,
    McpClient,
    McpServer,
    SkillCatalog,
    SseEventBuffer,
    TaskStatus,
    ToolDefinition,
    ToolRuntime,
    choose_transport,
)


def calculator_runtime() -> ToolRuntime:
    runtime = ToolRuntime()
    runtime.register(
        ToolDefinition(
            "add",
            "add numbers",
            {
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
                "additionalProperties": False,
            },
        ),
        lambda a, b: a + b,
    )
    return runtime


class ToolRuntimeTests(unittest.TestCase):
    def test_arguments_are_validated_before_execution(self) -> None:
        result = calculator_runtime().execute(call_id="1", name="add", arguments={"a": 1})
        self.assertFalse(result.ok)
        self.assertIn("missing required", result.error or "")

    def test_dangerous_tool_requires_explicit_approval(self) -> None:
        runtime = ToolRuntime()
        runtime.register(
            ToolDefinition("delete", "delete item", {"type": "object"}, dangerous=True),
            lambda: "deleted",
        )
        denied = runtime.execute(call_id="1", name="delete", arguments={})
        allowed = runtime.execute(call_id="2", name="delete", arguments={}, approved=True)
        self.assertFalse(denied.ok)
        self.assertTrue(allowed.ok)

    def test_parallel_results_keep_call_order(self) -> None:
        results = calculator_runtime().execute_many(
            [
                {"id": "first", "name": "add", "arguments": {"a": 1, "b": 2}},
                {"id": "second", "name": "add", "arguments": {"a": 20, "b": 22}},
            ]
        )
        self.assertEqual([(item.call_id, item.value) for item in results], [("first", 3), ("second", 42)])

    def test_timeout_returns_without_waiting_for_worker_shutdown(self) -> None:
        runtime = ToolRuntime()
        runtime.register(
            ToolDefinition("slow", "slow operation", {"type": "object"}),
            lambda: time.sleep(0.2),
        )
        started = time.monotonic()
        result = runtime.execute(call_id="1", name="slow", arguments={}, timeout_seconds=0.01)
        elapsed = time.monotonic() - started
        self.assertFalse(result.ok)
        self.assertIn("timed out", result.error or "")
        self.assertLess(elapsed, 0.1)


class McpTests(unittest.TestCase):
    def test_client_discovers_and_calls_server_tool(self) -> None:
        client = McpClient(McpServer("calculator", calculator_runtime()))
        schemas = client.discover_function_schemas()
        result = client.request("tools/call", {"name": "add", "arguments": {"a": 19, "b": 23}})
        self.assertEqual(schemas[0]["function"]["name"], "add")
        self.assertEqual(result["value"], 42)

    def test_resources_and_prompts_are_separate_capabilities(self) -> None:
        server = McpServer("docs", calculator_runtime())
        server.add_resource("docs://policy", "read only")
        server.add_prompt("review", "Review {code}")
        client = McpClient(server)
        self.assertEqual(client.request("resources/read", {"uri": "docs://policy"})["text"], "read only")
        self.assertEqual(client.request("prompts/get", {"name": "review"})["template"], "Review {code}")


class SkillCatalogTests(unittest.TestCase):
    def test_skill_is_loaded_progressively_and_blocks_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "review"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review code\n---\n# Steps\nRead files.",
                encoding="utf-8",
            )
            (skill_dir / "template.md").write_text("report", encoding="utf-8")
            catalog = SkillCatalog(directory)
            metadata = catalog.scan()
            self.assertEqual(metadata[0].description, "Review code")
            self.assertIn("Read files", catalog.load_instructions("review"))
            self.assertEqual(catalog.read_asset("review", "template.md"), "report")
            with self.assertRaisesRegex(ValueError, "outside"):
                catalog.read_asset("review", "../secret.txt")


class A2ATests(unittest.TestCase):
    def test_agent_card_discovery_and_task_lifecycle(self) -> None:
        directory = AgentDirectory()
        directory.register(
            AgentCard("researcher", "https://agent.example", (AgentSkill("research", "research topics"),))
        )
        store = A2ATaskStore(directory)
        task = store.submit("researcher", "research", "compare protocols")
        self.assertEqual(directory.find_by_skill("research")[0].name, "researcher")
        self.assertEqual(store.start(task.task_id).status, TaskStatus.WORKING)
        completed = store.complete(task.task_id, [{"type": "text", "text": "done"}])
        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        with self.assertRaisesRegex(ValueError, "invalid task transition"):
            store.start(task.task_id)


class TransportTests(unittest.TestCase):
    def test_transport_selection_matches_interaction_shape(self) -> None:
        self.assertEqual(choose_transport(local=True, bidirectional=False, realtime_media=False), "stdio")
        self.assertEqual(choose_transport(local=False, bidirectional=False, realtime_media=False), "streamable_http")
        self.assertEqual(choose_transport(local=False, bidirectional=True, realtime_media=False), "websocket")
        self.assertEqual(choose_transport(local=False, bidirectional=True, realtime_media=True), "webrtc")

    def test_sse_replays_events_after_last_event_id(self) -> None:
        buffer = SseEventBuffer()
        buffer.publish("token", {"text": "A"})
        buffer.publish("token", {"text": "B"})
        replay = buffer.replay_after(1)
        self.assertEqual([item.data["text"] for item in replay], ["B"])
        self.assertIn("data:", replay[0].encode())


class GatewayTests(unittest.TestCase):
    def test_gateway_fails_over_and_tracks_usage(self) -> None:
        gateway = LlmGateway(
            [GatewayRoute("smart", ("primary", "backup"), 0.01)],
            {
                "primary": lambda _request: (_ for _ in ()).throw(RuntimeError("busy")),
                "backup": lambda _request: {"text": "ok", "total_tokens": 120},
            },
        )
        gateway.set_token_quota("team-a", 200)
        response = gateway.complete("team-a", {"model": "smart", "estimated_tokens": 100})
        self.assertEqual(response["gateway_provider"], "backup")
        self.assertEqual(gateway.usage["team-a"].tokens, 120)
        self.assertEqual(gateway.usage["team-a"].failures, 1)
        with self.assertRaisesRegex(PermissionError, "quota"):
            gateway.complete("team-a", {"model": "smart", "estimated_tokens": 100})


if __name__ == "__main__":
    unittest.main()