# Day 20 延迟与成本统计报告（Per-Request Token + Latency）

- 生成时间（UTC）：2026-07-06T07:57:05.533336+00:00
- 评测集：inputs/day15_evalset_qa.json
- 语料文件：inputs/day8_corpus_backend_notes.txt
- chunk_size / overlap：80 / 20
- top-k：3
- candidate_top_n：8
- use_rerank：True
- embedding 模型：nomic-embed-text
- QA 模型：qwen2.5:0.5b

## 统计口径

- QA token：精确取自问答请求 usage（input/output/total）。
- embedding 成本：当前仅统计请求次数与耗时；不统计 token，因为 embedding 接口未返回 usage。
- 延迟拆分：vector_retrieval_ms / keyword_retrieval_ms / fusion_ms / dedup_ms / choose_hits_ms / qa_ms / end_to_end_ms。

## 汇总对比

| mode | query_count | qa_total_tokens_avg | qa_total_tokens_p95 | end_to_end_ms_avg | end_to_end_ms_p95 | qa_ms_avg | qa_ms_p95 | embedding_ms_avg | avg_embedding_request_count | cache_hit_rate | avg_dedup_removed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 2 | 658.0 | 658.9 | 12057.5 | 13122.9 | 11942.9 | 13013.6 | 114.0 | 1.00 | 0.000 | 0.00 |
| cache_dedup | 2 | 627.5 | 653.1 | 7247.1 | 8251.1 | 7133.8 | 8133.5 | 112.5 | 1.00 | 0.000 | 0.00 |

## 指标变化（cache_dedup - baseline）

- qa_total_tokens_avg: -30.5
- end_to_end_ms_avg: -4810.4
- qa_ms_avg: -4809.1
- embedding_ms_avg: -1.5
- cache_hit_rate(cache_dedup): 0.000
- avg_embedding_request_count(cache_dedup): 1.00
- avg_dedup_removed(cache_dedup): 0.00