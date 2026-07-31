# Xiaolinnote RAG 1-20 专题分析

本目录逐页分析 [Xiaolinnote RAG 面试题](https://xiaolinnote.com/ai/rag/)，每个网页对应一篇文档。内容按“网页理论、项目现状、高级参考实现”三层组织，避免把文章中的生产案例误写成当前项目能力。

## 代码入口

- Day8-Day21：项目原始学习脚本，覆盖固定窗口切块、FAISS 检索、基础问答、引用、重排、离线评估、Query Rewrite、混合召回、缓存、延迟成本与发布门禁。
- [高级 RAG 参考实现](examples/rag_capabilities_reference.py)：补充语义切块、父子切块、句子窗口、BM25、RRF、质量门控、声明引用校验、Hit@K/MRR、增量索引、图遍历、CRAG 与有界 Agentic RAG。
- [高级参考实现测试](../tests/test_rag_capabilities_reference.py)：10 个无网络依赖的单元测试。

> 边界说明：参考实现是独立教学层，尚未接入 Day8-Day21 主流程；项目没有部署 Milvus、Qdrant、HNSW、GraphRAG、RAGAs、Kafka 或生产级 Web Search。

## 文档索引

| 编号 | 原网页主题 | 分析文档 |
| --- | --- | --- |
| 1 | 什么是 RAG 与完整流程 | [完整工作流](1_whatisrag_complete_workflow.md) |
| 2 | RAG 解决的问题 | [知识、时效性与幻觉](2_rag_problems_knowledge_and_hallucination.md) |
| 3 | RAG 与微调 | [方案选型](3_rag_vs_finetune_selection.md) |
| 4 | Chunking | [存储与切块策略](4_chunking_storage_and_strategies.md) |
| 5 | 语义截断 | [上下文恢复](5_semantic_cuts_context_recovery.md) |
| 6 | Embedding | [模型选型与评估](6_embedding_selection_and_evaluation.md) |
| 7 | Embedding 算法 | [三代算法演进](7_embedding_algorithms_evolution.md) |
| 8 | 向量数据库 | [数据库与索引选型](8_vectordb_selection_and_index.md) |
| 9 | 向量库实战 | [规模、性能与瓶颈](9_vectordb_practice_performance.md) |
| 10 | 在线工作流 | [检索到生成](10_online_workflow_retrieval_generation.md) |
| 11 | 向量与关键词检索 | [Dense 与 Sparse](11_retrieval_types_dense_sparse.md) |
| 12 | Query Rewrite | [四类改写策略](12_query_rewrite_strategies.md) |
| 13 | 多路召回 | [混合检索与 RRF](13_multi_retrieval_rrf.md) |
| 14 | 检索优化 | [四层优化框架](14_retrieval_optimization_four_layers.md) |
| 15 | 高级 RAG | [Self-RAG、CRAG 与 GraphRAG](15_advanced_paradigms_self_crag_graphrag.md) |
| 16 | 图数据库 | [多跳检索](16_graph_db_multi_hop_retrieval.md) |
| 17 | 幻觉治理 | [门控与引用](17_hallucination_prevention_and_citations.md) |
| 18 | 效果评估 | [指标与闭环](18_evaluation_metrics_and_loop.md) |
| 19 | 动态更新 | [增量索引与版本](19_dynamic_update_and_versioning.md) |
| 20 | 落地难点 | [生产 RAG 难点](20_hardest_parts_production_rag.md) |

## 推荐阅读路径

```mermaid
flowchart LR
    A[基础 1到3] --> B[离线建库 4到9]
    B --> C[在线检索 10到14]
    C --> D[高级范式 15到16]
    D --> E[可信治理 17到20]
```

复习时先读 1、4、6、10、14、18 建立主干，再按面试薄弱点补读其余专题。