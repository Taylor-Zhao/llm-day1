# Day 8 Embedding 原理笔记

## 1. 什么是 Embedding

Embedding 是把文本映射为稠密向量的过程。语义相近的文本在向量空间里距离更近，常用于检索、聚类、去重和召回。

## 2. 为什么 RAG 依赖 Embedding

RAG 的第一步是召回相关片段。Embedding 把“关键词不完全一致但语义相关”的文本也召回出来，比纯关键词检索更鲁棒。

## 3. 评估 Embedding/切分质量的常见指标

1. Recall@K：目标片段是否被召回。
2. 引用正确率：回答引用是否来自正确来源。
3. Chunk cohesion（块内连贯性）：同一 chunk 内语义是否集中。
4. Chunk redundancy（块间冗余）：相邻 chunk 是否重复过多。

## 4. chunk size 与 overlap 的工程权衡

1. chunk size 太小：上下文不足，召回碎片化。
2. chunk size 太大：噪声增多，召回不精准，成本变高。
3. overlap 太小：跨段信息可能断裂。
4. overlap 太大：重复文本多，索引膨胀，检索结果冗余。

## 5. Day 8 实战目标

1. 运行切分实验脚本，对比多组 size/overlap。
2. 观察每组的 chunk 数量、平均长度、冗余率。
3. 选择一个“信息完整且冗余可控”的参数组，作为 Day 9 向量检索的默认参数。
