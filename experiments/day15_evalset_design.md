# Day 15 评测集设计说明（20-30 条问答基准）

## 1) 目标

Day15 的目标是产出一份可复用、可扩展的 RAG 基准评测集，直接支撑 Day16 离线评测脚本。

本评测集文件：
- `inputs/day15_evalset_qa.json`

## 2) 规模与构成

- 总样本数：24
- 可回答样本（answerable=true）：19
- 信息不足样本（answerable=false）：5

主题覆盖：
- observability
- database
- cache
- microservice
- container
- api-design
- resilience
- rag-chunking
- cross-topic
- insufficient-info

难度分层：
- easy：基础事实与枚举
- medium：多要点融合与因果解释
- hard：跨段综合与防幻觉边界

## 3) 字段定义

每条 case 包含：
- `id`：样本唯一 ID（如 D15-001）
- `question`：评测问题
- `answerable`：是否可由语料直接回答
- `reference_answer`：参考答案（用于人工比对或自动语义比对）
- `answer_keypoints`：关键要点（用于简单覆盖率打分）
- `expected_source_keywords`：期望命中的语料关键词（用于检索命中率/引用正确率近似评估）
- `category`：主题类别
- `difficulty`：难度
- `notes`：备注

## 4) Day16 对接建议

离线评测可先实现一个轻量版本：

1. 命中率（retrieval_hit_rate）
- 仅统计 `answerable=true` 的样本
- 若 Top-K 引用片段文本中包含任一 `expected_source_keywords`，记为命中

2. 引用正确率（citation_valid_rate）
- 若回答满足引用格式校验（你当前 Day11-Day14 已有）且命中关键词，记为引用正确
- 也可拆分成：
  - `citation_format_valid_rate`
  - `citation_grounded_rate`

3. 信息不足正确率（insufficient_precision）
- 对 `answerable=false` 样本，若回答出现“当前信息不足”且不编造具体细节，记为正确拒答

## 5) 评测集迭代建议

- 后续可把 `expected_source_keywords` 升级为更稳定的证据标注：
  - 段落 ID
  - 字符区间（start/end）
  - 可接受证据集合（support sets）
- 当语料规模增大后，可补齐：
  - 多跳问题
  - 同义改写对抗样本
  - 时间变化与版本漂移样本

## 6) 业务图解（Mermaid）

### 业务流程图（Day15 评测集设计）

```mermaid
flowchart TD
  A[明确评测目标\n可回答/不可回答都要覆盖] --> B[梳理业务主题\n观测/数据库/缓存/容灾等]
  B --> C[设计问题样本\n20-30 条问答基准]
  C --> D[标注可回答性\nanswerable=true/false]
  D --> E[编写参考答案与关键要点\nreference_answer + keypoints]
  E --> F[标注证据关键词\nexpected_source_keywords]
  F --> G[质量检查\n主题覆盖与难度分层]
  G --> H[输出评测集 JSON\n交付 Day16 离线评测]
```

### 业务时序图（Day15 评测集产出）

```mermaid
sequenceDiagram
  participant PM as 业务方
  participant DE as 数据/研发
  participant KB as 语料库
  participant QA as 评测集设计者
  participant OUT as Day15评测集

  PM->>DE: 提出评测目标与验收口径
  DE->>KB: 获取当前知识语料范围
  DE->>QA: 定义样本结构与字段
  QA->>QA: 生成可回答样本与信息不足样本
  QA->>QA: 标注关键词、参考答案、难度
  QA-->>DE: 回传评测集草案
  DE->>DE: 覆盖度与一致性检查
  DE-->>OUT: 发布 day15_evalset_qa.json
```
