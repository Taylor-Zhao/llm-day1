#!/usr/bin/env python3
"""Offline reference implementations for core LLM engineering concepts.

The code favors readable equations and deterministic behavior over performance.
It does not replace PyTorch, Transformers, FlashAttention, vLLM, SGLang, or a
production evaluation platform.
"""

from __future__ import annotations

import math
import random
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence


Vector = list[float]
Matrix = list[Vector]


def softmax(logits: Sequence[float], temperature: float = 1.0) -> Vector:
    if not logits:
        raise ValueError("logits must not be empty")
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    scaled = [float(value) / temperature for value in logits]
    finite = [value for value in scaled if math.isfinite(value)]
    if not finite:
        raise ValueError("at least one logit must be finite")
    maximum = max(finite)
    exponents = [math.exp(value - maximum) if math.isfinite(value) else 0.0 for value in scaled]
    total = sum(exponents)
    return [value / total for value in exponents]


def dot(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have the same dimension")
    return sum(a * b for a, b in zip(left, right))


def matrix_multiply(left: Matrix, right: Matrix) -> Matrix:
    if not left or not right or not right[0]:
        raise ValueError("matrices must not be empty")
    left_width = len(left[0])
    if any(len(row) != left_width for row in left):
        raise ValueError("left matrix is ragged")
    right_width = len(right[0])
    if any(len(row) != right_width for row in right):
        raise ValueError("right matrix is ragged")
    if left_width != len(right):
        raise ValueError("matrix dimensions are incompatible")
    columns = list(zip(*right))
    return [[dot(row, column) for column in columns] for row in left]


@dataclass(frozen=True)
class AttentionResult:
    outputs: Matrix
    weights: Matrix


def scaled_dot_product_attention(
    queries: Matrix,
    keys: Matrix,
    values: Matrix,
    *,
    causal: bool = False,
    alibi_slope: float = 0.0,
) -> AttentionResult:
    """Compute one attention head, optionally with a causal mask and ALiBi."""

    if not queries or not keys or not values:
        raise ValueError("Q, K, and V must not be empty")
    if len(keys) != len(values):
        raise ValueError("K and V must have the same sequence length")
    key_dimension = len(keys[0])
    value_dimension = len(values[0])
    if key_dimension == 0 or value_dimension == 0:
        raise ValueError("head dimensions must be positive")
    if any(len(row) != key_dimension for row in queries + keys):
        raise ValueError("Q and K dimensions must match")
    if any(len(row) != value_dimension for row in values):
        raise ValueError("V matrix is ragged")
    if alibi_slope < 0:
        raise ValueError("ALiBi slope must be non-negative")

    scale = math.sqrt(key_dimension)
    all_weights: Matrix = []
    outputs: Matrix = []
    for query_index, query in enumerate(queries):
        scores: Vector = []
        for key_index, key in enumerate(keys):
            if causal and key_index > query_index:
                scores.append(float("-inf"))
                continue
            score = dot(query, key) / scale
            score -= alibi_slope * abs(query_index - key_index)
            scores.append(score)
        weights = softmax(scores)
        output = [
            sum(weight * value[column] for weight, value in zip(weights, values))
            for column in range(value_dimension)
        ]
        all_weights.append(weights)
        outputs.append(output)
    return AttentionResult(outputs, all_weights)


def kv_head_assignment(query_heads: int, kv_heads: int) -> list[int]:
    """Map query heads to KV heads for MHA, GQA, or MQA."""

    if query_heads <= 0 or kv_heads <= 0 or query_heads % kv_heads != 0:
        raise ValueError("query_heads must be divisible by kv_heads")
    group_size = query_heads // kv_heads
    return [head // group_size for head in range(query_heads)]


def estimate_kv_cache_bytes(
    *,
    batch_size: int,
    sequence_length: int,
    layers: int,
    kv_heads: int,
    head_dimension: int,
    bytes_per_value: int = 2,
) -> int:
    values = (batch_size, sequence_length, layers, kv_heads, head_dimension, bytes_per_value)
    if any(value <= 0 for value in values):
        raise ValueError("KV cache dimensions must be positive")
    return 2 * batch_size * sequence_length * layers * kv_heads * head_dimension * bytes_per_value


def sinusoidal_position_encoding(length: int, dimension: int) -> Matrix:
    if length <= 0 or dimension <= 0 or dimension % 2:
        raise ValueError("length must be positive and dimension must be positive and even")
    encodings: Matrix = []
    for position in range(length):
        row: Vector = []
        for pair_index in range(dimension // 2):
            angle = position / (10000 ** (2 * pair_index / dimension))
            row.extend((math.sin(angle), math.cos(angle)))
        encodings.append(row)
    return encodings


def apply_rope(vector: Sequence[float], position: int, base: float = 10000.0) -> Vector:
    if not vector or len(vector) % 2:
        raise ValueError("RoPE requires a non-empty even-dimensional vector")
    if position < 0 or base <= 0:
        raise ValueError("position must be non-negative and base must be positive")
    dimension = len(vector)
    rotated: Vector = []
    for pair_start in range(0, dimension, 2):
        pair_index = pair_start // 2
        angle = position / (base ** (2 * pair_index / dimension))
        cosine = math.cos(angle)
        sine = math.sin(angle)
        first, second = vector[pair_start], vector[pair_start + 1]
        rotated.extend((first * cosine - second * sine, first * sine + second * cosine))
    return rotated


def _merge_pair(symbols: list[str], pair: tuple[str, str]) -> list[str]:
    merged: list[str] = []
    index = 0
    while index < len(symbols):
        if index + 1 < len(symbols) and (symbols[index], symbols[index + 1]) == pair:
            merged.append(symbols[index] + symbols[index + 1])
            index += 2
        else:
            merged.append(symbols[index])
            index += 1
    return merged


def train_bpe(corpus: Sequence[str], merge_count: int) -> list[tuple[str, str]]:
    """Train a small character-level BPE merge table with deterministic ties."""

    if merge_count < 0:
        raise ValueError("merge_count must be non-negative")
    sequences = [list(text) for text in corpus if text]
    merges: list[tuple[str, str]] = []
    for _ in range(merge_count):
        counts: Counter[tuple[str, str]] = Counter()
        for symbols in sequences:
            counts.update(zip(symbols, symbols[1:]))
        if not counts:
            break
        best_pair = min(counts, key=lambda pair: (-counts[pair], pair))
        merges.append(best_pair)
        sequences = [_merge_pair(symbols, best_pair) for symbols in sequences]
    return merges


def bpe_encode(text: str, merges: Sequence[tuple[str, str]]) -> list[str]:
    symbols = list(text)
    for pair in merges:
        symbols = _merge_pair(symbols, pair)
    return symbols


def causal_lm_cross_entropy(target_probabilities: Sequence[float]) -> float:
    if not target_probabilities or any(value <= 0 or value > 1 for value in target_probabilities):
        raise ValueError("target probabilities must be in (0, 1]")
    return -sum(math.log(value) for value in target_probabilities) / len(target_probabilities)


@dataclass(frozen=True)
class PowerLawFit:
    coefficient: float
    exponent: float

    def predict(self, scale: float) -> float:
        if scale <= 0:
            raise ValueError("scale must be positive")
        return self.coefficient * scale**self.exponent


def fit_power_law(scales: Sequence[float], losses: Sequence[float]) -> PowerLawFit:
    """Fit loss = coefficient * scale ** exponent in log space."""

    if len(scales) != len(losses) or len(scales) < 2:
        raise ValueError("at least two matching scale/loss observations are required")
    if any(value <= 0 for value in list(scales) + list(losses)):
        raise ValueError("power-law observations must be positive")
    x_values = [math.log(value) for value in scales]
    y_values = [math.log(value) for value in losses]
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    if denominator == 0:
        raise ValueError("scales must not all be equal")
    exponent = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values)) / denominator
    intercept = y_mean - exponent * x_mean
    return PowerLawFit(math.exp(intercept), exponent)


def lora_parameter_count(input_dimension: int, output_dimension: int, rank: int) -> int:
    if min(input_dimension, output_dimension, rank) <= 0:
        raise ValueError("LoRA dimensions must be positive")
    return rank * (input_dimension + output_dimension)


def merge_lora_weights(base: Matrix, matrix_a: Matrix, matrix_b: Matrix, *, alpha: float) -> Matrix:
    """Merge W + (alpha / rank) * B @ A using [out, in] weight layout."""

    if not matrix_a:
        raise ValueError("matrix A must not be empty")
    rank = len(matrix_a)
    delta = matrix_multiply(matrix_b, matrix_a)
    if len(base) != len(delta) or any(len(left) != len(right) for left, right in zip(base, delta)):
        raise ValueError("LoRA update shape must match base weights")
    scale = alpha / rank
    return [
        [base_value + scale * delta_value for base_value, delta_value in zip(base_row, delta_row)]
        for base_row, delta_row in zip(base, delta)
    ]


def _softplus(value: float) -> float:
    if value > 0:
        return value + math.log1p(math.exp(-value))
    return math.log1p(math.exp(value))


def dpo_loss(
    policy_chosen_logp: float,
    policy_rejected_logp: float,
    reference_chosen_logp: float,
    reference_rejected_logp: float,
    *,
    beta: float = 0.1,
) -> float:
    if beta <= 0:
        raise ValueError("beta must be positive")
    policy_margin = policy_chosen_logp - policy_rejected_logp
    reference_margin = reference_chosen_logp - reference_rejected_logp
    preference_logit = beta * (policy_margin - reference_margin)
    return _softplus(-preference_logit)


def grpo_advantages(rewards: Sequence[float], epsilon: float = 1e-8) -> Vector:
    if not rewards:
        raise ValueError("rewards must not be empty")
    mean = sum(rewards) / len(rewards)
    variance = sum((reward - mean) ** 2 for reward in rewards) / len(rewards)
    standard_deviation = math.sqrt(variance)
    if standard_deviation < epsilon:
        return [0.0 for _ in rewards]
    return [(reward - mean) / standard_deviation for reward in rewards]


def filter_distribution(
    probabilities: Sequence[float],
    *,
    top_k: int | None = None,
    top_p: float | None = None,
) -> Vector:
    if not probabilities or any(value < 0 for value in probabilities):
        raise ValueError("probabilities must be non-negative and non-empty")
    total = sum(probabilities)
    if total <= 0:
        raise ValueError("probability mass must be positive")
    normalized = [value / total for value in probabilities]
    ordered = sorted(range(len(normalized)), key=lambda index: (-normalized[index], index))
    retained = ordered
    if top_k is not None:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        retained = retained[:top_k]
    if top_p is not None:
        if not 0 < top_p <= 1:
            raise ValueError("top_p must be in (0, 1]")
        nucleus: list[int] = []
        cumulative = 0.0
        for index in retained:
            nucleus.append(index)
            cumulative += normalized[index]
            if cumulative >= top_p:
                break
        retained = nucleus
    retained_set = set(retained)
    filtered = [value if index in retained_set else 0.0 for index, value in enumerate(normalized)]
    retained_mass = sum(filtered)
    return [value / retained_mass for value in filtered]


def sample_next_token(
    logits: Sequence[float],
    *,
    temperature: float = 1.0,
    top_k: int | None = None,
    top_p: float | None = None,
    random_value: float | None = None,
) -> int:
    if not logits:
        raise ValueError("logits must not be empty")
    if temperature == 0:
        return max(range(len(logits)), key=lambda index: (logits[index], -index))
    probabilities = filter_distribution(softmax(logits, temperature), top_k=top_k, top_p=top_p)
    draw = random.random() if random_value is None else random_value
    if not 0 <= draw <= 1:
        raise ValueError("random_value must be in [0, 1]")
    cumulative = 0.0
    for index, probability in enumerate(probabilities):
        cumulative += probability
        if draw <= cumulative:
            return index
    return len(probabilities) - 1


StepFunction = Callable[[tuple[int, ...]], Sequence[float]]


def beam_search(
    step_function: StepFunction,
    prompt: Sequence[int],
    *,
    beam_width: int,
    max_new_tokens: int,
    eos_token_id: int | None = None,
) -> tuple[int, ...]:
    if beam_width <= 0 or max_new_tokens <= 0:
        raise ValueError("beam_width and max_new_tokens must be positive")
    beams: list[tuple[tuple[int, ...], float, bool]] = [(tuple(prompt), 0.0, False)]
    for _ in range(max_new_tokens):
        candidates: list[tuple[tuple[int, ...], float, bool]] = []
        for tokens, score, finished in beams:
            if finished:
                candidates.append((tokens, score, True))
                continue
            probabilities = softmax(step_function(tokens))
            token_order = sorted(range(len(probabilities)), key=lambda index: (-probabilities[index], index))
            for token_id in token_order[:beam_width]:
                next_tokens = tokens + (token_id,)
                next_score = score + math.log(max(probabilities[token_id], 1e-300))
                candidates.append((next_tokens, next_score, token_id == eos_token_id))
        beams = sorted(candidates, key=lambda item: (-item[1], item[0]))[:beam_width]
        if all(finished for _tokens, _score, finished in beams):
            break
    return beams[0][0]


class PrefixCache:
    """A bounded LRU cache that reuses the longest exact token prefix."""

    def __init__(self, max_entries: int = 128) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[int, ...], Any] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def put(self, token_prefix: Sequence[int], value: Any) -> None:
        key = tuple(token_prefix)
        if not key:
            raise ValueError("token prefix must not be empty")
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def longest_prefix(self, tokens: Sequence[int]) -> tuple[int, Any] | None:
        query = tuple(tokens)
        matching = [key for key in self._entries if len(key) <= len(query) and query[: len(key)] == key]
        if not matching:
            self.misses += 1
            return None
        key = max(matching, key=len)
        value = self._entries[key]
        self._entries.move_to_end(key)
        self.hits += 1
        return len(key), value

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


@dataclass(frozen=True)
class QuantizedVector:
    values: tuple[int, ...]
    scale: float
    bits: int

    def dequantize(self) -> Vector:
        return [value * self.scale for value in self.values]


def symmetric_quantize(values: Sequence[float], bits: int) -> QuantizedVector:
    if not values:
        raise ValueError("values must not be empty")
    if bits < 2 or bits > 16:
        raise ValueError("bits must be between 2 and 16")
    maximum_integer = 2 ** (bits - 1) - 1
    maximum_absolute = max(abs(value) for value in values)
    scale = maximum_absolute / maximum_integer if maximum_absolute else 1.0
    quantized = tuple(
        max(-maximum_integer, min(maximum_integer, round(value / scale)))
        for value in values
    )
    return QuantizedVector(quantized, scale, bits)


def build_structured_prompt(
    *,
    role: str,
    task: str,
    context: str,
    output_format: str,
    examples: Sequence[tuple[str, str]] = (),
) -> str:
    required = {"role": role, "task": task, "output_format": output_format}
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise ValueError("missing prompt sections: " + ", ".join(missing))
    sections = [
        f"# Role\n{role.strip()}",
        f"# Task\n{task.strip()}",
        f"# Context\n{context.strip() or '(none)'}",
        f"# Output Format\n{output_format.strip()}",
    ]
    if examples:
        rendered = [f"Input: {source}\nOutput: {target}" for source, target in examples]
        sections.append("# Examples\n" + "\n\n".join(rendered))
    return "\n\n".join(sections)


@dataclass(frozen=True)
class CitationMetrics:
    claim_coverage: float
    citation_validity: float
    grounded_claim_rate: float


def citation_metrics(
    claim_citations: Sequence[Sequence[str]],
    valid_source_ids: set[str],
) -> CitationMetrics:
    if not claim_citations:
        return CitationMetrics(0.0, 0.0, 0.0)
    covered = sum(bool(citations) for citations in claim_citations)
    flat = [citation for citations in claim_citations for citation in citations]
    valid = sum(citation in valid_source_ids for citation in flat)
    grounded = sum(bool(citations) and all(item in valid_source_ids for item in citations) for citations in claim_citations)
    return CitationMetrics(
        claim_coverage=covered / len(claim_citations),
        citation_validity=valid / len(flat) if flat else 0.0,
        grounded_claim_rate=grounded / len(claim_citations),
    )


ExpertFunction = Callable[[Sequence[float]], Sequence[float]]


class MoERouter:
    def __init__(self, expert_count: int, top_k: int) -> None:
        if expert_count <= 0 or top_k <= 0 or top_k > expert_count:
            raise ValueError("top_k must be between 1 and expert_count")
        self.expert_count = expert_count
        self.top_k = top_k

    def route(self, gate_logits: Sequence[float]) -> list[tuple[int, float]]:
        if len(gate_logits) != self.expert_count:
            raise ValueError("one gate logit is required per expert")
        probabilities = softmax(gate_logits)
        selected = sorted(range(self.expert_count), key=lambda index: (-probabilities[index], index))[: self.top_k]
        selected_mass = sum(probabilities[index] for index in selected)
        return [(index, probabilities[index] / selected_mass) for index in selected]

    def dispatch(
        self,
        token: Sequence[float],
        experts: Sequence[ExpertFunction],
        gate_logits: Sequence[float],
    ) -> Vector:
        if len(experts) != self.expert_count:
            raise ValueError("one function is required per expert")
        routed = [(weight, list(experts[index](token))) for index, weight in self.route(gate_logits)]
        output_dimension = len(routed[0][1])
        if any(len(output) != output_dimension for _weight, output in routed):
            raise ValueError("expert outputs must have matching dimensions")
        return [sum(weight * output[column] for weight, output in routed) for column in range(output_dimension)]


def moe_load_balance_loss(routes: Sequence[Sequence[tuple[int, float]]], expert_count: int) -> float:
    if expert_count <= 0 or not routes:
        raise ValueError("routes and expert_count must be non-empty and positive")
    loads = [0.0] * expert_count
    for route in routes:
        for expert, weight in route:
            loads[expert] += weight
    total = sum(loads)
    proportions = [load / total for load in loads]
    target = 1 / expert_count
    return sum((proportion - target) ** 2 for proportion in proportions) / expert_count


@dataclass(frozen=True)
class DeploymentRequirements:
    has_gpu: bool = True
    edge_or_local: bool = False
    high_concurrency: bool = True
    shared_prompt_prefixes: bool = False
    huggingface_first: bool = False
    nvidia_max_performance: bool = False


def choose_deployment_framework(requirements: DeploymentRequirements) -> str:
    if requirements.edge_or_local or not requirements.has_gpu:
        return "llama.cpp"
    if requirements.nvidia_max_performance:
        return "TensorRT-LLM"
    if requirements.shared_prompt_prefixes:
        return "SGLang"
    if requirements.huggingface_first and not requirements.high_concurrency:
        return "TGI"
    return "vLLM"


def classification_metrics(
    expected: Sequence[str],
    predicted: Sequence[str],
    *,
    positive_label: str,
) -> Mapping[str, float]:
    if len(expected) != len(predicted) or not expected:
        raise ValueError("matching non-empty labels are required")
    true_positive = sum(e == positive_label and p == positive_label for e, p in zip(expected, predicted))
    false_positive = sum(e != positive_label and p == positive_label for e, p in zip(expected, predicted))
    false_negative = sum(e == positive_label and p != positive_label for e, p in zip(expected, predicted))
    accuracy = sum(e == p for e, p in zip(expected, predicted)) / len(expected)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1}


def pass_at_k(sample_count: int, correct_count: int, k: int) -> float:
    if sample_count <= 0 or not 0 <= correct_count <= sample_count or not 1 <= k <= sample_count:
        raise ValueError("invalid pass@k arguments")
    if sample_count - correct_count < k:
        return 1.0
    return 1 - math.comb(sample_count - correct_count, k) / math.comb(sample_count, k)


def expected_calibration_error(
    confidences: Sequence[float],
    correctness: Sequence[bool],
    *,
    bins: int = 10,
) -> float:
    if len(confidences) != len(correctness) or not confidences or bins <= 0:
        raise ValueError("matching observations and positive bins are required")
    if any(not 0 <= confidence <= 1 for confidence in confidences):
        raise ValueError("confidence must be in [0, 1]")
    total = len(confidences)
    error = 0.0
    for bin_index in range(bins):
        lower = bin_index / bins
        upper = (bin_index + 1) / bins
        indices = [
            index
            for index, confidence in enumerate(confidences)
            if lower <= confidence <= upper and (bin_index == bins - 1 or confidence < upper)
        ]
        if not indices:
            continue
        average_confidence = sum(confidences[index] for index in indices) / len(indices)
        average_accuracy = sum(correctness[index] for index in indices) / len(indices)
        error += len(indices) / total * abs(average_confidence - average_accuracy)
    return error


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    quality: float
    tool_accuracy: float
    cost_per_million_tokens: float
    latency_ms: float
    compliant: bool
    local: bool = False


@dataclass(frozen=True)
class ModelRequirements:
    quality_weight: float = 0.45
    tool_weight: float = 0.25
    cost_weight: float = 0.15
    latency_weight: float = 0.15
    max_cost_per_million_tokens: float = 100.0
    max_latency_ms: float = 10000.0
    require_compliance: bool = True
    require_local: bool = False


def rank_model_candidates(
    candidates: Iterable[ModelCandidate],
    requirements: ModelRequirements,
) -> list[tuple[str, float]]:
    if requirements.max_cost_per_million_tokens <= 0 or requirements.max_latency_ms <= 0:
        raise ValueError("cost and latency limits must be positive")
    ranked: list[tuple[str, float]] = []
    for candidate in candidates:
        if requirements.require_compliance and not candidate.compliant:
            continue
        if requirements.require_local and not candidate.local:
            continue
        if candidate.cost_per_million_tokens > requirements.max_cost_per_million_tokens:
            continue
        if candidate.latency_ms > requirements.max_latency_ms:
            continue
        score = (
            requirements.quality_weight * candidate.quality
            + requirements.tool_weight * candidate.tool_accuracy
            - requirements.cost_weight
            * candidate.cost_per_million_tokens
            / requirements.max_cost_per_million_tokens
            - requirements.latency_weight * candidate.latency_ms / requirements.max_latency_ms
        )
        ranked.append((candidate.name, score))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


def run_demo() -> None:
    attention = scaled_dot_product_attention(
        [[1.0, 0.0], [0.0, 1.0]],
        [[1.0, 0.0], [0.0, 1.0]],
        [[10.0, 0.0], [0.0, 20.0]],
        causal=True,
    )
    token = sample_next_token([2.0, 1.0, 0.0], temperature=0)
    quantized = symmetric_quantize([0.0, 0.5, -1.0], bits=4)
    print("Causal attention weights:", attention.weights)
    print("Greedy token:", token)
    print("INT4-like values:", quantized.values, "dequantized:", quantized.dequantize())


if __name__ == "__main__":
    run_demo()