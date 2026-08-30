# 系统架构、业务流程与时序图

## 1. 系统上下文

```mermaid
flowchart LR
    Upstream[上游题库/授权数据源] --> Import[题目导入 API]
    Import --> MySQL[(MySQL 8<br/>业务事实与队列)]
    MySQL --> Worker[预测 Worker]
    Worker --> Graph[LangGraph 标注图]
    Graph --> Historical[历史标签模型]
    Graph --> Sparse[MySQL FULLTEXT ngram]
    Graph --> Dense[(Qdrant Dense Index)]
    Graph --> LLM[LangChain<br/>结构化 LLM]
    Graph --> MySQL
    Reviewer[业务补录人员] --> Web[Vue 3 或 React 19 工作台]
    Web --> API[FastAPI]
    API --> MySQL
    API --> Graph
    Reviewer --> Web
    MySQL --> Training[训练/评测流水线]
    Training --> Historical
    Training --> Dense
    API --> Metrics[Prometheus / Logs / Audit]
    Worker --> Metrics
```

### 关键边界

- MySQL 是题目状态、预测快照、人工最终标签和审计事件的事实源。
- Qdrant 是可重建的 Dense 索引，不保存最终人工业务状态。
- LLM 只能从受控标签字典选择，不能直接写数据库。
- 操作员身份来自 JWT Runtime，不作为 Prompt 参数交给模型。
- 人工提交是最终业务决策，同时也是后续训练反馈。

## 2. 组件架构

```mermaid
flowchart TB
    subgraph Frontends[等价 TypeScript 客户端]
        VueApp[Vue App.vue]
        VueState[Vue Composable]
        VueClient[Vue api.ts]
        ReactApp[React App.tsx]
        ReactState[React Reducer Hook]
        ReactClient[React api.ts]
    end

    subgraph Backend[FastAPI Application]
        Routes[api/routes.py]
        Auth[core/auth.py]
        UseCase[services/application.py]
        Workflow[services/labeling_workflow.py]
        Repo[db/repositories.py]
        Metrics[core/metrics.py]
    end

    subgraph ModelAdapters[Model and Retrieval Adapters]
        Rules[rule_based_scores]
        Classifier[SklearnHistoricalLabelModel]
        Retriever[ReciprocalRankFusionRetriever]
        Reasoner[LangChainLabelReasoner]
    end

    VueApp --> VueState --> VueClient --> Routes
    ReactApp --> ReactState --> ReactClient --> Routes
    Routes --> Auth
    Routes --> UseCase
    UseCase --> Workflow
    UseCase --> Repo
    UseCase --> Metrics
    Workflow --> Rules
    Workflow --> Classifier
    Workflow --> Retriever
    Workflow --> Reasoner
    Repo --> DB[(MySQL)]
    Retriever --> DB
    Retriever --> Vector[(Qdrant)]
```

## 3. 主要业务逻辑图

```mermaid
flowchart TD
    A[新题进入系统] --> B[create_question<br/>按 external_id 幂等入库]
    B --> C[同事务创建 prediction_job]
    C --> D[Worker claim_prediction_job<br/>SKIP LOCKED + 租约]
    D --> E[QuestionLabelingWorkflow.predict]
    E --> F[保存 PredictionRun<br/>建议/证据/版本/耗时]
    F --> G[业务人员 claim_next<br/>领取题目租约]
    G --> H[Web 客户端默认选中模型建议]
    H --> I[人工检查题干、答案、理由和相似题]
    I --> J{标签是否正确}
    J -->|正确| K[保留默认选择]
    J -->|误报| L[取消模型标签]
    J -->|漏标| M[增加标签]
    K --> N[submit_annotation]
    L --> N
    M --> N
    N --> O[幂等键 + expected_version 校验]
    O --> P[保存最终标签与 added/removed]
    P --> Q[任务 completed]
    P --> R[离线评测/增量训练/RAG 重建]
```

## 4. LangGraph 方法级流程图

```mermaid
flowchart TD
    START([START]) --> Normalize[_normalize<br/>规范题干、答案与解析]
    Normalize --> Rules[_rules<br/>rule_based_scores]
    Rules --> Retrieve[_retrieve<br/>EvidenceRetriever.retrieve]
    Retrieve -->|成功| Historical[_historical_model<br/>predict_scores]
    Retrieve -->|异常| RagFallback[记录 RAG 降级<br/>evidence=[]]
    RagFallback --> Historical
    Historical --> Reason[_reason<br/>LabelReasoner.reason]
    Reason -->|成功| Ensemble[_ensemble<br/>四路加权融合]
    Reason -->|异常| LlmFallback[记录 LLM 降级<br/>ReasonerOutput 空结果]
    LlmFallback --> Ensemble
    Ensemble --> Result[PredictionResult<br/>完整标签+证据+警告]
    Result --> END([END])
```

### 节点职责

| 节点 | 输入 | 输出 | 是否允许降级 |
| --- | --- | --- | --- |
| `_normalize` | `QuestionInput` | `normalized_text` | 否，输入异常直接拒绝 |
| `_rules` | 题干、答案 | `rule_scores` | 否，纯函数 |
| `_retrieve` | 题目、学科 | `evidence` | 是，记录警告并返回空证据 |
| `_historical_model` | 题目 | `historical_scores` | 默认否，生产主模型异常应进入 Worker 重试 |
| `_reason` | 题目、标签、证据 | `ReasonerOutput` | 是，保留规则和历史模型结果 |
| `_ensemble` | 四路信号 | 全标签建议 | 否，未知标签被过滤 |

## 5. 人工补录时序图（方法粒度）

```mermaid
sequenceDiagram
    autonumber
    actor U as 业务人员
    participant App as App.vue 或 App.tsx
    participant Hook as Composable 或 Reducer Hook
    participant Client as api.ts
    participant Route as FastAPI routes
    participant Service as LabelingApplicationService
    participant Repo as QuestionRepository
    participant Graph as QuestionLabelingWorkflow
    participant DB as MySQL

    U->>App: 选择学科
    App->>Hook: changeSubject(subject)
    Hook->>Client: fetchTaxonomy + claimNextTask
    Client->>Route: GET taxonomy / tasks/next
    Route->>Route: current_user + require_role
    Route->>Service: claim_next(operator, subject)
    Service->>Repo: claim_next(SKIP LOCKED, lease)
    Repo->>DB: SELECT FOR UPDATE + UPDATE version
    DB-->>Repo: StoredTask
    Repo-->>Service: task
    Service->>Repo: latest_prediction(question_id)
    alt 已有后台预测
        Repo-->>Service: prediction snapshot
    else 预测尚未生成
        Service->>Graph: predict(question)
        Graph-->>Service: PredictionResult
        Service->>Repo: save_prediction(result)
    end
    Service-->>Route: TaskBundle
    Route-->>Client: task + suggestions + evidence
    Client-->>Hook: typed response
    Hook->>Hook: 应用预测并计算默认勾选
    Hook-->>App: 响应式或 Reducer 更新
    App-->>U: 展示题目、标签和 RAG 证据

    U->>App: 增删标签并填写备注
    App->>Hook: submitReview()
    Hook->>Client: submitAnnotation(idempotency_key, expected_version)
    Client->>Route: POST tasks/{id}/submit
    Route->>Service: submit(...)
    Service->>Repo: submit_annotation(...)
    Repo->>DB: 校验幂等键、租约、版本和标签
    Repo->>DB: INSERT annotation + UPDATE question + INSERT audit
    DB-->>Repo: commit
    Repo-->>Service: added_tags / removed_tags
    Service-->>App: AnnotationResponse
    App-->>U: 提交成功，可领取下一题
```

## 6. 异步预测时序图

```mermaid
sequenceDiagram
    autonumber
    participant Import as 导入 API
    participant Repo as QuestionRepository
    participant DB as MySQL
    participant Worker as app.worker
    participant Service as ApplicationService
    participant Graph as LangGraph
    participant RAG as MySQL + Qdrant
    participant LLM as LangChain Model

    Import->>Repo: create_question(predict_now=false)
    Repo->>DB: 同事务 INSERT question + prediction_job
    DB-->>Import: 202/任务等待预测
    loop Worker 轮询
        Worker->>Repo: claim_prediction_job(worker_id)
        Repo->>DB: SELECT FOR UPDATE SKIP LOCKED
        DB-->>Worker: job lease
        Worker->>Service: process_next_prediction_job
        Service->>Graph: predict(question)
        Graph->>RAG: Sparse + Dense retrieval
        RAG-->>Graph: RRF evidence
        Graph->>LLM: prompt | structured_model
        LLM-->>Graph: ReasonerOutput
        Graph-->>Service: PredictionResult
        Service->>Repo: save_prediction
        Repo->>DB: INSERT run/suggestions + job completed
        alt 瞬时失败
            Worker->>Repo: fail_prediction_job
            Repo->>DB: pending + exponential backoff
        else 重试耗尽
            Repo->>DB: status=failed + audit
        end
    end
```

## 7. Hybrid RAG 流程

```mermaid
flowchart LR
    Q[新题题干+答案] --> Sparse[MySqlFullTextSearchBackend<br/>ngram FULLTEXT]
    Q --> Embed[OpenAIEmbeddings]
    Embed --> Dense[QdrantDenseSearchBackend]
    Sparse --> RRF[ReciprocalRankFusionRetriever]
    Dense --> RRF
    RRF --> Filter[同学科过滤+题目ID去重]
    Filter --> Evidence[RetrievedEvidence<br/>人工标签+来源+许可证]
    Evidence --> LLM[LangChainLabelReasoner]
    Evidence --> Ensemble[LangGraph ensemble]
```

RRF 计算：

$$
score(d)=\sum_{r \in \{sparse,dense\}} \frac{w_r}{k+rank_r(d)}
$$

它使用排名而非原始分数，因此不会把 MySQL FULLTEXT 的相关度直接与向量余弦相似度相加。

## 8. 数据模型

```mermaid
erDiagram
    QUESTIONS ||--|| PREDICTION_JOBS : schedules
    QUESTIONS ||--o{ PREDICTION_RUNS : predicts
    PREDICTION_RUNS ||--o{ LABEL_SUGGESTIONS : contains
    QUESTIONS ||--o{ HUMAN_ANNOTATIONS : reviewed_as
    PREDICTION_RUNS o|--o{ HUMAN_ANNOTATIONS : reviewed_from
    QUESTIONS ||--o{ AUDIT_EVENTS : audited_by

    QUESTIONS {
        bigint id PK
        string external_id UK
        string subject
        text stem
        text reference_answer
        string source_uri
        string license_name
        bigint latest_annotation_id
        string status
        int version
        string claimed_by
        datetime claimed_until
    }
    PREDICTION_JOBS {
        uuid id PK
        bigint question_id UK
        string status
        int attempts
        datetime available_at
        datetime leased_until
    }
    PREDICTION_RUNS {
        uuid id PK
        bigint question_id FK
        string model_version
        json result_json
        float latency_ms
    }
    LABEL_SUGGESTIONS {
        bigint id PK
        uuid prediction_id FK
        string tag_code
        float confidence
        boolean selected_by_default
        json source_scores
        json evidence_ids
    }
    HUMAN_ANNOTATIONS {
        bigint id PK
        bigint question_id FK
        uuid prediction_id FK
        json selected_tags
        json added_tags
        json removed_tags
        string operator_id
        string idempotency_key UK
    }
```

## 9. 一致性设计

### 导入幂等

`questions.external_id` 唯一。上游重试返回原任务，不创建重复题目。

### 提交幂等

前端每次用户提交动作生成 `idempotency_key`，数据库唯一约束保证网络重试返回同一人工记录。

### 乐观锁

页面读取 `task.version`，提交时回传 `expected_version`。其他操作已更新任务时返回 `STALE_TASK_VERSION`，防止旧页面覆盖新结论。

### 任务租约

人工任务和 Worker 任务都有 `claimed_until/leased_until`。进程故障后租约到期可重新领取，避免永久卡死。

### 副作用边界

模型推理没有外部业务写副作用。最终写入集中在仓储事务中，预测失败不会修改人工最终标签。

## 10. 降级与停止策略

| 故障 | 行为 |
| --- | --- |
| RAG 超时 | 记录警告，使用规则 + 历史模型 + LLM |
| LLM 超时或结构错误 | 记录警告，使用规则 + 历史模型 + RAG |
| 历史模型不可加载 | 生产启动失败，不用规则静默冒充模型 |
| MySQL 不可用 | API/Worker 不就绪，禁止接受写请求 |
| Qdrant 不可用 | Worker 有界重试，页面可人工补录已有任务 |
| Worker 崩溃 | 租约到期后其他 Worker 重领 |
| 人工页面版本过期 | 返回 409，要求刷新，不自动覆盖 |

## 11. 与 LangChain、LangGraph 和 llm-day1 的关系

| 知识点 | 本项目落点 |
| --- | --- |
| LCEL | `ChatPromptTemplate | with_structured_output` |
| Tool/结构化输出 | Pydantic `ReasonerOutput` 与受控标签合同 |
| LangGraph State/Node/Edge | 六节点 `QuestionLabelingWorkflow` |
| Dense/Sparse/RRF | Qdrant + MySQL ngram + RRF |
| Rerank/证据 | RRF 排名、人工证据和 evidence ID |
| Agent 边界 | LLM 负责判断，代码负责标签、权限、事务和停止条件 |
| Memory/Store | MySQL 保存任务状态和长期人工反馈；不把身份塞进 Prompt |
| SFT/LoRA | 人工反馈导出为固定 train/eval JSONL |
| 离线评测 | micro/macro F1、exact match、人工修改率 |
| 成本与延迟 | LLM 有界重试、RAG limit、预测 Histogram |
| 服务治理 | JWT、CORS、Nginx 限流、错误码、健康检查、审计 |
