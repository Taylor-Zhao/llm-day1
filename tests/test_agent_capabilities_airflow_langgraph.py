import unittest

from xiaolinnote_agent_analysis.examples import agent_capabilities_airflow_langgraph as module
from dags import agent_capabilities_airflow_langgraph_dag as dag_entry


class AirflowLangGraphPipelineTests(unittest.TestCase):
    def test_local_pipeline_runs_without_airflow(self) -> None:
        result = module.run_local_demo_pipeline()

        self.assertIn("memory", result)
        self.assertIn("dag", result)
        self.assertIn("routing", result)
        self.assertIn("reflection", result)
        self.assertEqual(result["dag"]["status"], "completed")
        self.assertEqual(result["routing"]["status"], "completed")
        self.assertTrue(result["reflection"]["passed"])
        self.assertIn("evidence", result["reflection"]["output"])

    def test_airflow_dag_factory_behavior_matches_environment(self) -> None:
        if module.AIRFLOW_AVAILABLE:
            dag = module.create_airflow_dag()
            self.assertEqual(dag.dag_id, "agent_capabilities_airflow_langgraph")
            expected = {
                "seed_and_recall",
                "execute_langgraph_dag",
                "route_and_handoff",
                "reflect_output",
            }
            self.assertTrue(expected.issubset(set(dag.task_dict)))
        else:
            with self.assertRaisesRegex(RuntimeError, "Airflow is not available"):
                module.create_airflow_dag()

    def test_stage_schema_validation_rejects_malformed_payloads(self) -> None:
        with self.assertRaisesRegex(ValueError, "DAGStageInput"):
            module.run_dag_stage({"unexpected": 1})

        with self.assertRaisesRegex(ValueError, "RoutingStageInput"):
            module.run_routing_stage({"status": "completed"})

        with self.assertRaisesRegex(ValueError, "ReflectionStageInput"):
            module.run_reflection_stage({"output": {"report": "x"}})

    def test_dags_entrypoint_exports_expected_symbol(self) -> None:
        if module.AIRFLOW_AVAILABLE:
            self.assertIsNotNone(dag_entry.dag)
            self.assertEqual(dag_entry.dag.dag_id, "agent_capabilities_airflow_langgraph")
        else:
            self.assertIsNone(dag_entry.dag)


if __name__ == "__main__":
    unittest.main()
