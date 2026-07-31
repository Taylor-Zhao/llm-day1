import math
import unittest

from xiaolinnote_llm_analysis.examples.llm_algorithms_reference import (
    DeploymentRequirements,
    ModelCandidate,
    ModelRequirements,
    MoERouter,
    PrefixCache,
    apply_rope,
    beam_search,
    bpe_encode,
    build_structured_prompt,
    causal_lm_cross_entropy,
    choose_deployment_framework,
    citation_metrics,
    classification_metrics,
    dpo_loss,
    estimate_kv_cache_bytes,
    expected_calibration_error,
    filter_distribution,
    fit_power_law,
    grpo_advantages,
    kv_head_assignment,
    lora_parameter_count,
    merge_lora_weights,
    moe_load_balance_loss,
    pass_at_k,
    rank_model_candidates,
    sample_next_token,
    scaled_dot_product_attention,
    sinusoidal_position_encoding,
    symmetric_quantize,
    train_bpe,
)


class AttentionTests(unittest.TestCase):
    def test_causal_attention_masks_future_and_normalizes_rows(self) -> None:
        result = scaled_dot_product_attention(
            [[1.0, 0.0], [0.0, 1.0]],
            [[1.0, 0.0], [0.0, 1.0]],
            [[10.0], [20.0]],
            causal=True,
        )
        self.assertEqual(result.weights[0], [1.0, 0.0])
        self.assertTrue(all(math.isclose(sum(row), 1.0) for row in result.weights))
        self.assertEqual(result.outputs[0], [10.0])

    def test_mha_gqa_and_mqa_assign_query_heads_to_kv_heads(self) -> None:
        self.assertEqual(kv_head_assignment(4, 4), [0, 1, 2, 3])
        self.assertEqual(kv_head_assignment(4, 2), [0, 0, 1, 1])
        self.assertEqual(kv_head_assignment(4, 1), [0, 0, 0, 0])

    def test_gqa_reduces_kv_cache_in_direct_proportion_to_kv_heads(self) -> None:
        common = {"batch_size": 1, "sequence_length": 1024, "layers": 8, "head_dimension": 64}
        mha = estimate_kv_cache_bytes(kv_heads=8, **common)
        gqa = estimate_kv_cache_bytes(kv_heads=2, **common)
        self.assertEqual(gqa, mha // 4)


class PositionAndTokenizerTests(unittest.TestCase):
    def test_position_encodings_have_expected_invariants(self) -> None:
        encoding = sinusoidal_position_encoding(2, 4)
        self.assertEqual(encoding[0], [0.0, 1.0, 0.0, 1.0])
        vector = [3.0, 4.0, 1.0, 2.0]
        rotated = apply_rope(vector, position=7)
        self.assertAlmostEqual(sum(value**2 for value in vector), sum(value**2 for value in rotated))

    def test_bpe_learns_frequent_pairs_and_handles_new_text(self) -> None:
        merges = train_bpe(["low", "lower", "lowest"], merge_count=2)
        self.assertEqual(merges[0], ("l", "o"))
        self.assertEqual(bpe_encode("low123", merges), ["low", "1", "2", "3"])


class TrainingAndAlignmentTests(unittest.TestCase):
    def test_causal_loss_and_power_law_fit(self) -> None:
        self.assertAlmostEqual(causal_lm_cross_entropy([0.5, 0.25]), -math.log(0.5) / 2 - math.log(0.25) / 2)
        fit = fit_power_law([1.0, 4.0, 16.0], [2.0, 1.0, 0.5])
        self.assertAlmostEqual(fit.coefficient, 2.0)
        self.assertAlmostEqual(fit.exponent, -0.5)
        self.assertAlmostEqual(fit.predict(64.0), 0.25)

    def test_lora_count_and_merge(self) -> None:
        self.assertEqual(lora_parameter_count(4, 3, 2), 14)
        merged = merge_lora_weights(
            [[1.0, 1.0], [1.0, 1.0]],
            [[1.0, 0.0], [0.0, 1.0]],
            [[2.0, 0.0], [0.0, 2.0]],
            alpha=2.0,
        )
        self.assertEqual(merged, [[3.0, 1.0], [1.0, 3.0]])

    def test_dpo_rewards_relative_chosen_improvement(self) -> None:
        weak = dpo_loss(-2.0, -2.0, -2.0, -2.0, beta=1.0)
        strong = dpo_loss(-0.5, -3.0, -2.0, -2.0, beta=1.0)
        self.assertLess(strong, weak)

    def test_grpo_advantages_are_centered_and_scaled(self) -> None:
        advantages = grpo_advantages([1.0, 2.0, 3.0])
        self.assertAlmostEqual(sum(advantages), 0.0)
        self.assertAlmostEqual(sum(value**2 for value in advantages) / 3, 1.0)


class DecodingAndCacheTests(unittest.TestCase):
    def test_temperature_top_k_and_top_p(self) -> None:
        self.assertEqual(sample_next_token([1.0, 3.0, 2.0], temperature=0), 1)
        top_k = filter_distribution([0.5, 0.3, 0.2], top_k=2)
        self.assertEqual(top_k[2], 0.0)
        nucleus = filter_distribution([0.7, 0.2, 0.1], top_p=0.8)
        self.assertEqual(nucleus[2], 0.0)
        self.assertEqual(sample_next_token([3.0, 2.0, 1.0], top_k=1, random_value=0.99), 0)

    def test_beam_search_preserves_multiple_paths(self) -> None:
        def step(tokens: tuple[int, ...]) -> list[float]:
            if len(tokens) == 1:
                return [2.0, 1.9]
            return [0.0, 0.0] if tokens[-1] == 0 else [5.0, -5.0]

        greedy = sample_next_token(step((9,)), temperature=0)
        result = beam_search(step, [9], beam_width=2, max_new_tokens=2)
        self.assertEqual(greedy, 0)
        self.assertEqual(result, (9, 1, 0))

    def test_prefix_cache_uses_longest_exact_prefix_and_tracks_hits(self) -> None:
        cache = PrefixCache(max_entries=2)
        cache.put([1, 2], "short")
        cache.put([1, 2, 3], "long")
        self.assertEqual(cache.longest_prefix([1, 2, 3, 4]), (3, "long"))
        self.assertIsNone(cache.longest_prefix([9]))
        self.assertEqual(cache.hit_rate, 0.5)


class QuantizationPromptAndGroundingTests(unittest.TestCase):
    def test_symmetric_quantization_respects_range_and_reconstructs(self) -> None:
        quantized = symmetric_quantize([-1.0, -0.5, 0.0, 0.5, 1.0], bits=4)
        self.assertTrue(all(-7 <= value <= 7 for value in quantized.values))
        reconstructed = quantized.dequantize()
        self.assertLess(max(abs(left - right) for left, right in zip(reconstructed, [-1, -0.5, 0, 0.5, 1])), 0.08)

    def test_structured_prompt_contains_all_operational_sections(self) -> None:
        prompt = build_structured_prompt(
            role="Reviewer",
            task="Find defects",
            context="Python service",
            output_format="JSON",
            examples=[("bad", "finding")],
        )
        for heading in ("# Role", "# Task", "# Context", "# Output Format", "# Examples"):
            self.assertIn(heading, prompt)

    def test_citation_metrics_separate_coverage_from_validity(self) -> None:
        metrics = citation_metrics([["doc-1"], [], ["missing"]], {"doc-1"})
        self.assertAlmostEqual(metrics.claim_coverage, 2 / 3)
        self.assertAlmostEqual(metrics.citation_validity, 0.5)
        self.assertAlmostEqual(metrics.grounded_claim_rate, 1 / 3)


class MoEDeploymentAndEvaluationTests(unittest.TestCase):
    def test_moe_routes_only_top_k_and_combines_experts(self) -> None:
        router = MoERouter(expert_count=3, top_k=2)
        route = router.route([3.0, 2.0, 0.0])
        self.assertEqual([index for index, _weight in route], [0, 1])
        self.assertAlmostEqual(sum(weight for _index, weight in route), 1.0)
        output = router.dispatch(
            [2.0],
            [lambda token: [token[0]], lambda token: [token[0] * 2], lambda token: [99.0]],
            [3.0, 2.0, 0.0],
        )
        self.assertGreater(output[0], 2.0)
        self.assertLess(output[0], 4.0)

    def test_load_balance_loss_is_zero_for_uniform_routes(self) -> None:
        routes = [[(0, 1.0)], [(1, 1.0)], [(0, 1.0)], [(1, 1.0)]]
        self.assertEqual(moe_load_balance_loss(routes, expert_count=2), 0.0)

    def test_deployment_choice_follows_workload_shape(self) -> None:
        self.assertEqual(choose_deployment_framework(DeploymentRequirements(has_gpu=False)), "llama.cpp")
        self.assertEqual(
            choose_deployment_framework(DeploymentRequirements(shared_prompt_prefixes=True)),
            "SGLang",
        )
        self.assertEqual(choose_deployment_framework(DeploymentRequirements()), "vLLM")

    def test_evaluation_metrics_cover_classification_code_and_calibration(self) -> None:
        metrics = classification_metrics(["yes", "yes", "no"], ["yes", "no", "no"], positive_label="yes")
        self.assertAlmostEqual(metrics["accuracy"], 2 / 3)
        self.assertAlmostEqual(metrics["precision"], 1.0)
        self.assertAlmostEqual(pass_at_k(10, 2, 1), 0.2)
        self.assertAlmostEqual(expected_calibration_error([0.8, 0.2], [True, False], bins=2), 0.2)

    def test_model_selection_applies_compliance_gate_before_scoring(self) -> None:
        candidates = [
            ModelCandidate("strong-overseas", 0.99, 0.99, 10.0, 100.0, compliant=False),
            ModelCandidate("balanced-local", 0.85, 0.90, 5.0, 150.0, compliant=True, local=True),
            ModelCandidate("cheap-local", 0.75, 0.75, 1.0, 80.0, compliant=True, local=True),
        ]
        ranked = rank_model_candidates(candidates, ModelRequirements(require_compliance=True))
        self.assertNotIn("strong-overseas", [name for name, _score in ranked])
        self.assertEqual(ranked[0][0], "balanced-local")


if __name__ == "__main__":
    unittest.main()