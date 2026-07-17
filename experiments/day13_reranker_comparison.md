# Day 13 重排对比报告（No-Rerank vs Rerank）

- 生成时间（UTC）：2026-07-03T10:00:13.108439+00:00
- chunk_size / overlap：80 / 20
- top-k：3
- candidate_top_n：8
- rerank 权重(alpha/beta/gamma)：0.7/0.25/0.05
- embedding 模型：nomic-embed-text
- QA 模型：qwen2.5:0.5b

## 汇总对比

| mode | query_count | citation_valid_rate | info_insufficient_rate | avg_attempt_count | avg_total_tokens | avg_top1_score |
|---|---:|---:|---:|---:|---:|---:|
| no_rerank | 4 | 0.750 | 0.250 | 1.75 | 676.2 | 0.658 |
| rerank | 4 | 0.750 | 0.250 | 1.50 | 674.0 | 0.658 |

## 指标变化（rerank - no_rerank）

- citation_valid_rate: +0.000
- info_insufficient_rate: +0.000
- avg_attempt_count: -0.25
- avg_total_tokens: -2.2

## 每个 Query 的 Top-K 对比

### Query: 如何排查数据库连接池打满导致的超时问题？

- no_rerank chunk_ids: 2, 4, 6
- rerank chunk_ids: 2, 4, 6
- no_rerank citation_valid=True, attempts=2
- rerank citation_valid=True, attempts=1

### Query: 缓存命中率下降时应该优先看哪些指标？

- no_rerank chunk_ids: 2, 5, 0
- rerank chunk_ids: 2, 5, 0
- no_rerank citation_valid=True, attempts=1
- rerank citation_valid=True, attempts=1

### Query: 在微服务里如何定位超时链路的根因？

- no_rerank chunk_ids: 2, 5, 6
- rerank chunk_ids: 2, 5, 6
- no_rerank citation_valid=True, attempts=2
- rerank citation_valid=True, attempts=2

### Query: 限流和降级策略在高并发场景下怎么落地？

- no_rerank chunk_ids: 5, 6, 2
- rerank chunk_ids: 5, 6, 2
- no_rerank citation_valid=False, attempts=2
- rerank citation_valid=False, attempts=2
