# Day 18 多路召回对比报告（Vector-Only vs Hybrid）

- 生成时间（UTC）：2026-07-05T09:59:55.180069+00:00
- 评测集：inputs/day15_evalset_qa.json
- 语料文件：inputs/day8_corpus_backend_notes.txt
- chunk_size / overlap：80 / 20
- top-k：3
- candidate_top_n：8
- use_rerank：True
- embedding 模型：nomic-embed-text
- QA 模型：qwen2.5:0.5b
- hybrid vector/keyword weight：0.7/0.3

## 汇总对比

| mode | query_count | retrieval_hit_rate | citation_correct_rate | insufficient_correct_rate | citation_format_valid_rate | avg_total_tokens |
|---|---:|---:|---:|---:|---:|---:|
| vector_only | 24 | 0.632 | 0.474 | 0.600 | 0.667 | 637.5 |
| hybrid | 24 | 0.895 | 0.579 | 0.600 | 0.625 | 621.2 |

## 指标变化（hybrid - vector_only）

- retrieval_hit_rate: +0.263
- citation_correct_rate: +0.105
- insufficient_correct_rate: +0.000
- citation_format_valid_rate: -0.042
- avg_total_tokens: -16.3

## 业务图解（Mermaid）

### 业务流程图（Day18 多路召回）

```mermaid
flowchart TD
	A[接收问题] --> B[并行召回]
	B --> C1[向量召回]
	B --> C2[关键词召回]
	C1 --> D[融合排序]
	C2 --> D
	D --> E[选择 top-k 证据]
	E --> F[生成回答与引用]
	F --> G[评测并与 vector-only 对比]
	G --> H[输出业务效果报告]
```

### 业务时序图（Day18 双路融合）

```mermaid
sequenceDiagram
	participant Runner as Day18脚本
	participant Vec as 向量召回
	participant Key as 关键词召回
	participant Fuse as 融合器
	participant LLM as 问答模型
	participant Eval as 评测统计

	Runner->>Vec: 按问题召回向量候选
	Runner->>Key: 按问题召回关键词候选
	Vec-->>Fuse: vector candidates
	Key-->>Fuse: keyword candidates
	Fuse-->>Runner: hybrid candidates
	Runner->>LLM: 基于 hybrid top-k 生成回答
	LLM-->>Eval: answer + citations
	Eval-->>Runner: 指标结果
```

## 技术图解（Mermaid）

### 流程图（Day18 多路召回评测流程）

```mermaid
flowchart TD
	A[加载配置与评测集] --> B[读取语料并切分 chunks]
	B --> C[构建向量索引 FAISS]
	B --> D[构建关键词统计: term set + IDF]
	C --> E[逐样本评测]
	D --> E

	E --> F1[vector_only: 向量候选]
	E --> F2[hybrid: 向量候选 + 关键词候选]
	F2 --> G2[RRF 融合候选]

	F1 --> H1[可选 rerank -> top-k]
	G2 --> H2[可选 rerank -> top-k]

	H1 --> I1[answer_with_retry 引用校验]
	H2 --> I2[answer_with_retry 引用校验]

	I1 --> J1[记录样本结果 JSONL]
	I2 --> J2[记录样本结果 JSONL]

	J1 --> K[聚合指标: hit/citation/insufficient/tokens]
	J2 --> K
	K --> L[输出 Markdown + CSV 报告]
```

### 时序图（单样本双模式对比）

```mermaid
sequenceDiagram
	participant M as 主流程
	participant V as 向量检索
	participant K as 关键词检索
	participant F as RRF融合
	participant R as 重排器
	participant Q as 问答模型
	participant C as 引用校验
	participant O as 结果落盘

	M->>V: 用原问题召回 vector candidates
	M->>K: 用原问题召回 keyword candidates

	M->>R: vector_only 候选(可选重排)
	R-->>M: vector top-k
	M->>Q: 基于 vector top-k 生成回答
	Q-->>C: 回答 + 引用
	C-->>M: citation_valid / citations
	M->>O: 写入 vector_only 行

	M->>F: 输入 vector + keyword 候选
	F-->>M: hybrid candidates
	M->>R: hybrid 候选(可选重排)
	R-->>M: hybrid top-k
	M->>Q: 基于 hybrid top-k 生成回答
	Q-->>C: 回答 + 引用
	C-->>M: citation_valid / citations
	M->>O: 写入 hybrid 行

	M->>M: 汇总两模式指标并输出报告
```