# Day 19 缓存与去重对比报告（Baseline vs Cache+Dedup）

- 生成时间（UTC）：2026-07-06T02:31:42.732244+00:00
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
| baseline | 24 | 0.895 | 0.474 | 0.600 | 0.625 | 640.3 | 8475.6 | 0.000 | 0.00 |
| cache_dedup | 24 | 0.895 | 0.632 | 0.600 | 0.750 | 625.5 | 7461.2 | 0.000 | 0.00 |

## 指标变化（cache_dedup - baseline）

- retrieval_hit_rate: +0.000
- citation_correct_rate: +0.158
- insufficient_correct_rate: +0.000
- citation_format_valid_rate: +0.125
- avg_total_tokens: -14.8
- avg_elapsed_ms: -1014.4
- cache_hit_rate(cache_dedup): 0.000
- dedup_total_removed(cache_dedup): 0

## 业务图解（Mermaid）

### 业务流程图（Day19 缓存与去重）

```mermaid
flowchart TD
	A[接收问题] --> B[先查缓存]
	B -->|命中| C[复用候选证据]
	B -->|未命中| D[执行混合检索]
	D --> E[写入缓存]
	C --> F[候选去重 dedup]
	E --> F
	F --> G[选择 top-k 证据]
	G --> H[生成回答与引用]
	H --> I[对比 baseline 与 cache_dedup]
```

### 业务时序图（Day19 缓存命中/未命中）

```mermaid
sequenceDiagram
	participant Runner as Day19脚本
	participant Cache as 候选缓存
	participant RET as 混合检索
	participant DD as 去重模块
	participant LLM as 问答模型
	participant REP as 对比报告

	Runner->>Cache: 查询问题缓存键
	alt 缓存命中
		Cache-->>Runner: 返回候选
	else 缓存未命中
		Runner->>RET: 执行向量+关键词召回
		RET-->>Runner: hybrid candidates
		Runner->>Cache: 写入候选缓存
	end
	Runner->>DD: 候选去重与重排
	DD-->>Runner: 去重后候选
	Runner->>LLM: 生成回答
	LLM-->>REP: 回答与引用质量数据
```

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