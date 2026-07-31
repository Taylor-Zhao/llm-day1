import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from xiaolinnote_rag_analysis.examples.rag_capabilities_reference import (  # noqa: E402
    BM25Index,
    Chunk,
    Claim,
    CorrectiveRAG,
    Edge,
    IncrementalIndexManifest,
    KnowledgeGraph,
    RetrievalDecision,
    build_parent_child_index,
    build_sentence_windows,
    hit_rate_at_k,
    mean_reciprocal_rank,
    reciprocal_rank_fusion,
    retrieval_gate,
    run_agentic_rag,
    semantic_chunks,
    validate_claim_sources,
)


class RAGCapabilitiesReferenceTests(unittest.TestCase):
    def test_semantic_chunking_preserves_sentences(self) -> None:
        chunks = semantic_chunks("doc", "第一句完整。第二句也完整！第三句结束。", max_tokens=7)
        self.assertEqual("第一句完整。", chunks[0].text)
        self.assertTrue(all(chunk.text.endswith(("。", "！")) for chunk in chunks))

    def test_parent_child_and_sentence_window_restore_context(self) -> None:
        text = "退款需要订单号。企业用户联系专属客服。处理不超过两天。"
        index = build_parent_child_index("policy", text, parent_tokens=30, child_tokens=10)
        expanded = index.expand([index.children[0].chunk_id, index.children[1].chunk_id])
        self.assertEqual(1, len(expanded))
        windows = build_sentence_windows("policy", text, window_size=1)
        self.assertIn("退款需要订单号", str(windows[1].metadata["window_text"]))
        self.assertIn("处理不超过两天", str(windows[1].metadata["window_text"]))

    def test_bm25_prefers_exact_rare_term(self) -> None:
        chunks = [
            Chunk("a", "通用显卡性能介绍", "doc"),
            Chunk("b", "RTX 4090 显卡功耗为 450W", "doc"),
            Chunk("c", "RTX 4080 产品参数", "doc"),
        ]
        hits = BM25Index(chunks).search("RTX 4090 功耗", top_k=2)
        self.assertEqual("b", hits[0][0].chunk_id)

    def test_rrf_rewards_cross_route_hits(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d", "a"]])
        self.assertEqual("b", fused[0][0])

    def test_gate_and_claim_validation(self) -> None:
        self.assertEqual(RetrievalDecision.LOCAL, retrieval_gate([0.8], 0.3, 0.6))
        self.assertEqual(RetrievalDecision.MIXED, retrieval_gate([0.4], 0.3, 0.6))
        self.assertEqual(RetrievalDecision.FALLBACK, retrieval_gate([0.1], 0.3, 0.6))
        chunks = {"p1": Chunk("p1", "退款期限是七天", "policy")}
        self.assertEqual([], validate_claim_sources([Claim("退款期限七天", ("p1",))], chunks))
        self.assertTrue(validate_claim_sources([Claim("保修期限三年", ("p1",))], chunks))

    def test_hit_rate_and_mrr_separate_recall_from_rank(self) -> None:
        rankings = [["x", "gold"], ["gold", "y"], ["z"]]
        relevant = [{"gold"}, {"gold"}, {"gold"}]
        self.assertAlmostEqual(2 / 3, hit_rate_at_k(rankings, relevant, 2))
        self.assertAlmostEqual(0.5, mean_reciprocal_rank(rankings, relevant))

    def test_manifest_detects_change_and_switches_version(self) -> None:
        manifest = IncrementalIndexManifest()
        self.assertTrue(manifest.needs_update("green", "doc", "v1"))
        manifest.record("green", "doc", "v1", ["doc:0", "doc:1"])
        self.assertFalse(manifest.needs_update("green", "doc", "v1"))
        self.assertTrue(manifest.needs_update("green", "doc", "v2"))
        manifest.activate("green")
        self.assertEqual("green", manifest.active_version)
        self.assertEqual(("doc:0", "doc:1"), manifest.delete("green", "doc"))

    def test_graph_traversal_supports_multi_hop_relations(self) -> None:
        graph = KnowledgeGraph(
            [
                Edge("小米", "竞争", "公司A"),
                Edge("公司A", "CEO", "张三"),
                Edge("公司A", "所在地", "北京"),
            ]
        )
        edges = graph.traverse("小米", max_hops=2, relations={"竞争", "CEO"})
        self.assertEqual(["公司A", "张三"], [edge.target for edge in edges])

    def test_corrective_rag_routes_to_fallback(self) -> None:
        local = Chunk("local", "无关内容", "local")
        web = Chunk("web", "外部补充内容", "web")
        rag = CorrectiveRAG(
            lambda query: [(local, 0.1)],
            lambda query: [(web, 0.9)],
        )
        decision, chunks = rag.retrieve("最新政策")
        self.assertEqual(RetrievalDecision.FALLBACK, decision)
        self.assertEqual(["web"], [chunk.chunk_id for chunk in chunks])

    def test_agentic_rag_is_bounded_and_deduplicates_context(self) -> None:
        chunks = {
            "first": [Chunk("a", "第一步事实", "doc")],
            "second": [Chunk("a", "第一步事实", "doc"), Chunk("b", "第二步事实", "doc")],
        }

        def planner(question, context, iteration):
            return "second" if iteration == 0 else None

        result = run_agentic_rag("first", lambda query: chunks[query], planner, max_iterations=3)
        self.assertTrue(result.completed)
        self.assertEqual(("first", "second"), result.queries)
        self.assertEqual(["a", "b"], [chunk.chunk_id for chunk in result.chunks])


if __name__ == "__main__":
    unittest.main()