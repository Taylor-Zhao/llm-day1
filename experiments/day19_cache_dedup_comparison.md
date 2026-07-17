# Day 19 缓存与去重对比报告（Baseline vs Cache+Dedup）

- 生成时间（UTC）：2026-07-06T01:56:25.184944+00:00
- 评测集：inputs/day15_evalset_qa.json
- 语料文件：inputs/day8_corpus_backend_notes.txt
- chunk_size / overlap：80 / 20
- top-k：3
- candidate_top_n：8
- use_rerank：True
- embedding 模型：nomic-embed-text
- QA 模型：qwen2.5:0.5b

## 汇总对比

| mode | query_count | retrieval_hit_rate | citation_correct_rate | insufficient_correct_rate | citation_format_valid_rate | avg_total_tokens | avg_elapsed_ms | cache_hit_rate | avg_dedup_removed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 2 | 1.000 | 0.500 | 0.000 | 0.500 | 615.5 | 12888.3 | 0.000 | 0.00 |
| cache_dedup | 2 | 1.000 | 1.000 | 0.000 | 1.000 | 600.0 | 9937.1 | 0.000 | 0.00 |

## 指标变化（cache_dedup - baseline）

- retrieval_hit_rate: +0.000
- citation_correct_rate: +0.500
- insufficient_correct_rate: +0.000
- citation_format_valid_rate: +0.500
- avg_total_tokens: -15.5
- avg_elapsed_ms: -2951.2
- cache_hit_rate(cache_dedup): 0.000
- dedup_total_removed(cache_dedup): 0

## 技术图解（Mermaid）

### 流程图（Day19 未去重 vs 去重后）

```mermaid
flowchart TD
	A[原问题 query] --> B[vector_candidates\n向量召回候选]
	A --> C[keyword_candidates\n关键词召回候选]
	B --> D[hybrid_candidates\nRRF 融合候选池]
	C --> D

	D --> E1[baseline\n不去重]
	D --> E2[cache_dedup\n先 dedup_candidates]

	E1 --> F1[choose_hits]
	F1 --> G1[Top-K 可能重复占坑\n例: chunk-0 / chunk-4 / chunk-0]
	G1 --> H1[证据覆盖变窄\n回答与引用更容易重复]

	E2 --> F2[dedup_candidates\n按 chunk_id 去重并重排 rank]
	F2 --> G2[choose_hits]
	G2 --> H2[Top-K 更分散\n例: chunk-0 / chunk-4 / chunk-3]
	H2 --> I2[证据覆盖更完整\n引用格式与稳定性更好]
```

### 时序图（hybrid_candidates -> dedup_candidates -> choose_hits）

```mermaid
sequenceDiagram
	participant M as 主流程
	participant V as 向量检索
	participant K as 关键词检索
	participant F as RRF融合
	participant D as dedup_candidates
	participant R as choose_hits/rerank
	participant Q as 问答模型

	M->>V: 召回 vector_candidates
	M->>K: 召回 keyword_candidates
	M->>F: 融合两路候选
	F-->>M: hybrid_candidates

	alt baseline（未去重）
		M->>R: 直接传入 hybrid_candidates
		R-->>M: top-k hits（可能含重复证据）
	else cache_dedup（去重后）
		M->>D: 传入 hybrid_candidates
		D-->>M: 去重后的 candidates
		M->>R: 传入去重后的 candidates
		R-->>M: top-k hits（证据更分散）
	end

	M->>Q: 用最终 hits 生成回答与引用
	Q-->>M: answer + citations
```