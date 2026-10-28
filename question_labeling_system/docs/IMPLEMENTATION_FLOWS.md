# 实现级流程图与时序图

本文按当前代码实现描述在线接口与离线脚本的数据流。图中的 MySQL 是业务事实源；Qdrant 是可重建的 Dense 检索索引；模型工件和 JSONL 是文件产物。图不表示脚本未实现的自动发布或 alias 切换。

MySQL 业务表为 `questions`（题目与人工任务状态/租约）、`prediction_jobs`（预测队列）、`prediction_runs`（预测快照）、`label_suggestions`（逐标签建议）、`human_annotations`（人工最终标签及差异）、`audit_events`（审计事件）。系统没有独立的 `tasks` 表；最新人工标注通过 `questions.latest_annotation_id` 关联。

## 1. 在线接口与预测流程

```mermaid
flowchart TD
    Importer[导入端] -->|POST /api/v1/questions| RouteCreate[FastAPI create_question]
    RouteCreate --> AuthImport[current_user + importer role]
    AuthImport --> ServiceCreate[LabelingApplicationService.create_question]
    ServiceCreate --> RepoCreate[QuestionRepository.create_question]
    RepoCreate -->|单事务、external_id 幂等| DBCreate[(MySQL: questions + prediction_jobs + audit_events)]
    DBCreate --> PredictMode{predict_now?}

    PredictMode -->|true 默认| SyncPredict["predict_task(force=false)"]
    PredictMode -->|false| Pending[返回 pending 任务/等待预测占位结果]
    Pending --> Worker[app.worker 轮询]
    Worker --> ClaimJob[claim_prediction_job]
    ClaimJob -->|SKIP LOCKED + attempts + lease| DBJob[(MySQL: prediction_jobs)]
    DBJob -->|已领取任务| AsyncPredict["predict_task(force=true)"]

    SyncPredict --> Existing{是否已有可复用预测?}
    Existing -->|是| Snapshot[读取最新 prediction snapshot]
    Existing -->|否| Workflow
    AsyncPredict --> Workflow
    RePredict["POST /api/v1/questions/{id}/predict"] --> AuthLabeler[current_user + labeler role]
    AuthLabeler -->|force 可覆盖复用行为| SyncPredict

    subgraph Graph[QuestionLabelingWorkflow / LangGraph]
        Normalize[normalize: 题干 + 答案 + 解析] --> Rules[rules: 规则分数]
        Rules --> Retrieve[retrieve: evidence 检索]
        Retrieve --> Sparse[MySQL FULLTEXT: questions JOIN human_annotations<br/>同学科题目 + 最新人工标签]
        Sparse --> DenseInput[question_to_model_text / 查询文本]
        DenseInput --> EmbedQuery[Embedding API: embed_query]
        EmbedQuery --> Qdrant[(Qdrant: 同学科 Dense 候选)]
        Qdrant --> RRF[RRF: 排名融合、question_id 去重、取 top-k]
        RRF --> Historical[历史模型工件: HashingVectorizer + 每标签 SGD 概率]
        Historical --> Reason[结构化 LLM: 标签定义 + 题目 + RAG 证据]
        Reason --> Ensemble[ensemble: 规则/历史模型/RAG/LLM 加权]
        Ensemble --> Suggestions[PredictionResult: 全标签建议、默认勾选、理由、证据 ID、warnings]
        Retrieve -. 检索异常 .-> RagFallback[空 evidence + warning]
        RagFallback --> Historical
        Reason -. LLM 异常 .-> LlmFallback[空 ReasonerOutput + warning]
        LlmFallback --> Ensemble
    end

    Workflow --> SavePrediction[save_prediction]
    SavePrediction -->|单事务 INSERT prediction_runs、label_suggestions；UPDATE prediction_jobs；INSERT audit_events| DBPrediction[(MySQL tables<br/>prediction_runs + label_suggestions + prediction_jobs + audit_events)]
    Snapshot --> Response[TaskBundle]
    DBPrediction --> Response
    Response --> Client[API JSON: task + prediction_id + suggestions + evidence]

    Reviewer[补录人员] -->|GET /api/v1/tasks/next| ClaimRoute[FastAPI claim_next_task]
    ClaimRoute --> AuthLabeler
    AuthLabeler --> ClaimTask[service.claim_next]
    ClaimTask -->|事务锁/租约、status=in_review、version+1| DBTask[(MySQL: questions + audit_events)]
    DBTask --> LatestPrediction[latest_prediction: prediction_runs.result_json]
    LatestPrediction --> DBPrediction
    LatestPrediction --> ReviewBundle[返回题目 + 模型默认标签 + 证据]
    ReviewBundle --> Reviewer

    Reviewer -->|"POST /api/v1/tasks/{id}/submit<br/>selected_tags + prediction_id + expected_version + idempotency_key"| SubmitRoute[FastAPI submit_annotation]
    SubmitRoute --> AuthLabeler
    AuthLabeler --> SubmitService[service.submit]
    SubmitService --> SubmitRepo[repository.submit_annotation]
    SubmitRepo -->|事务读取/校验 questions、prediction_runs、label_suggestions、human_annotations 幂等键| DBSubmit[(MySQL transaction)]
    DBSubmit -->|INSERT 最终 selected_tags + added_tags + removed_tags| Annotation[(human_annotations)]
    DBSubmit -->|status=completed、清租约、version+1、latest_annotation_id| Question[(questions)]
    DBSubmit --> Audit[(audit_events)]
    Annotation --> SubmitResponse[AnnotationResponse]
```

> 当前 `ReciprocalRankFusionRetriever` 的代码依次执行 MySQL Sparse 查询和 Qdrant Dense 查询，并非并发请求。`POST /questions` 默认 `predict_now=true`；设为 `false` 才由 Worker 异步消费已创建的 `prediction_jobs`。预测失败时 Worker 按租约与重试预算退避；LLM/RAG 节点可降级，历史模型错误则会使预测失败并进入 Worker 重试。

## 2. 创建题目和预测时序

```mermaid
sequenceDiagram
    autonumber
    actor Importer as 导入端
    participant API as FastAPI routes
    participant Auth as Auth / RBAC
    participant App as LabelingApplicationService
    participant Repo as QuestionRepository
    participant DB as MySQL: questions, prediction_jobs, prediction_runs, label_suggestions, human_annotations, audit_events
    participant Worker as app.worker
    participant Graph as LangGraph workflow
    participant Rule as 规则评分
    participant Sparse as MySQL FULLTEXT
    participant Embed as Embedding API
    participant Qdrant as Qdrant
    participant Model as 历史分类器工件
    participant LLM as OpenAI-compatible ChatModel

    Importer->>API: POST /api/v1/questions(question, predict_now)
    API->>Auth: current_user + require_role(importer)
    Auth-->>API: 认证主体
    API->>App: create_question(question, actor, request_id)
    App->>Repo: create_question(...)
    Repo->>DB: 查 questions.external_id；同一事务 INSERT questions、prediction_jobs、audit_events
    DB-->>Repo: StoredTask（重复 external_id 返回已有任务）
    Repo-->>App: task

    alt predict_now=true（默认同步）
        App->>App: predict_task(force=false)
        App->>Repo: get_task + latest_prediction
        Repo->>DB: 读取 questions + 最新 prediction_runs.result_json
        DB-->>Repo: task / 可复用 snapshot / none
        alt 已有预测且非 force
            Repo-->>App: prediction snapshot
        else 无可复用预测
            App->>Graph: predict(QuestionInput)
            Graph->>Rule: 题干/答案规则特征
            Rule-->>Graph: rule_scores
            Graph->>Sparse: 同学科 FULLTEXT 查询题干、答案、解析
            Sparse->>DB: questions JOIN latest human_annotations
            DB-->>Sparse: sparse candidates + human_labels + source/license
            Graph->>Embed: embed_query(stem + answer)
            Embed-->>Graph: query vector
            Graph->>Qdrant: 同学科向量 search
            Qdrant-->>Graph: dense candidates + payload
            Graph->>Graph: RRF 融合、去重、生成 RetrievedEvidence
            Graph->>Model: predict_scores(QuestionInput)
            Model-->>Graph: 每个受控标签的概率
            Graph->>LLM: Prompt(题目 + 标签定义 + evidence JSON)
            LLM-->>Graph: ReasonerOutput(labels, confidence, reason, evidence_ids)
            Graph->>Graph: 加权融合并生成全标签 suggestions
            Graph-->>App: PredictionResult + model_version + warnings
            App->>Repo: save_prediction(result, latency_ms)
            Repo->>DB: 事务写 prediction_runs.result_json + label_suggestions；完成 prediction_jobs；写 audit_events
            DB-->>Repo: prediction_id
            Repo-->>App: prediction_id
        end
        App-->>API: TaskBundle(task, prediction_id, PredictionResult)
        API-->>Importer: 201 JSON（题目、建议、证据、warnings）
    else predict_now=false（异步）
        App-->>API: TaskBundle + pending warning
        API-->>Importer: 201 JSON（任务已入库，预测待处理）
        Worker->>Repo: claim_prediction_job(worker_id, lease, max_attempts)
        Repo->>DB: 事务 SELECT ... FOR UPDATE SKIP LOCKED + 更新 running/attempts/lease
        DB-->>Worker: StoredPredictionJob
        Worker->>App: process_next_prediction_job
        App->>Graph: predict(QuestionInput)
        Note over Graph,LLM: 后续检索、模型、LLM 和保存步骤与同步路径相同
        App->>Repo: 成功时 save_prediction；异常时 fail_prediction_job
        Repo->>DB: 成功 INSERT prediction_runs、label_suggestions、audit_events 并 UPDATE prediction_jobs.completed；失败 UPDATE prediction_jobs.retry/failed 并 INSERT audit_events
    end
```

## 3. 领取任务和提交人工标签时序

```mermaid
sequenceDiagram
    autonumber
    actor Reviewer as 补录人员
    participant API as FastAPI routes
    participant Auth as Auth / RBAC
    participant App as LabelingApplicationService
    participant Repo as QuestionRepository
    participant DB as MySQL: questions, prediction_jobs, prediction_runs, label_suggestions, human_annotations, audit_events

    Reviewer->>API: GET /api/v1/tasks/next?subject=...
    API->>Auth: current_user + require_role(labeler)
    Auth-->>API: operator_id
    API->>App: claim_next(operator_id, request_id, subject)
    App->>Repo: claim_next(..., lease_seconds)
    Repo->>DB: SELECT/UPDATE questions（status、claimed_by、claimed_until、version）
    Note over Repo,DB: MySQL 使用 FOR UPDATE SKIP LOCKED；同事务 INSERT audit_events
    DB-->>Repo: StoredTask / none
    Repo-->>App: task / none
    alt 队列为空
        App-->>API: none
        API-->>Reviewer: 204 No Content
    else 已领取任务
        App->>Repo: latest_prediction(question_id)
        Repo->>DB: SELECT prediction_runs.result_json（按 question_id / created_at 取最新）
        DB-->>Repo: snapshot / none
        opt 没有预测快照
            App->>App: predict_task(force=false) 同步兜底生成预测
            Note over App,DB: 完整模型链见“创建题目和预测时序”
        end
        App-->>API: TaskBundle
        API-->>Reviewer: 200 task + prediction_id + 默认标签 + evidence
        Reviewer->>Reviewer: 校对并增删 selected_tags、填写 note
        Reviewer->>API: POST /api/v1/tasks/{id}/submit
        Note right of Reviewer: 带 prediction_id、expected_version、idempotency_key
        API->>Auth: current_user + require_role(labeler)
        Auth-->>API: operator_id
        API->>App: submit(...)
        App->>Repo: submit_annotation(...)
        Repo->>DB: 事务查 human_annotations.idempotency_key；读取 questions、prediction_runs、label_suggestions
        Repo->>Repo: 校验版本、租约所有者、标签学科、预测归属
        Repo->>Repo: added_tags=final-defaults；removed_tags=defaults-final
        Repo->>DB: INSERT human_annotations(selected_tags/added_tags/removed_tags/model_version)
        Repo->>DB: UPDATE questions(status=completed, latest_annotation_id, version, clear lease)
        Repo->>DB: INSERT audit_events
        DB-->>Repo: commit / 已存在幂等结果
        Repo-->>App: StoredAnnotation
        App-->>API: AnnotationResponse + added/removed
        API-->>Reviewer: 201 JSON
    end
```

## 4. 离线训练、索引、反馈评测与 SFT 导出流程

```mermaid
flowchart LR
    subgraph Source[训练数据源]
        DB[(MySQL<br/>questions JOIN human_annotations<br/>latest via questions.latest_annotation_id)]
        Snapshot[授权 JSONL 快照<br/>external_id/question/selected_tags]
    end

    subgraph Classifier[train_historical_model.py]
        DB -->|status=completed，最新标注| Reader[iter_database_examples<br/>stream_results + fetchmany]
        Snapshot -->|逐行 json.loads| JsonReader[iter_jsonl_examples]
        Reader --> Example[QuestionInput + selected_tags]
        JsonReader --> Example
        Example --> Split[SHA-256 external_id 稳定切分]
        Split -->|训练桶| Batch[epochs + 有界 batch]
        Batch --> ModelText[question_to_model_text<br/>学科/题干/答案/解析]
        ModelText --> Vectorizer[HashingVectorizer<br/>char 2-5 gram / sparse matrix]
        Vectorizer --> SGD[每个 tag 一个 SGDClassifier<br/>partial_fit + 正例 sample_weight]
        Split -->|eval 桶| Holdout[固定留出集]
        SGD --> EvalModel[每标签 predict_proba<br/>threshold=0.5]
        Holdout --> EvalModel
        EvalModel --> Metrics[micro/macro F1<br/>exact match]
        Metrics --> Artifact[(本地 joblib 工件<br/>临时文件后原子 replace)]
        Artifact --> Manifest[模型 manifest JSON<br/>version/参数/未知标签/metrics]
    end

    subgraph Index[RAG：index_rag.py]
        DB -->|同一最新人工标注读取器| IndexReader[iter_database_examples]
        Snapshot -->|可复现快照| IndexJson[iter_jsonl_examples]
        IndexReader --> IndexBatch[批次 batch_size]
        IndexJson --> IndexBatch
        IndexBatch --> EmbedText[question_to_model_text]
        EmbedText --> EmbedDocs["Embedding API<br/>embed_documents(batch)"]
        EmbedDocs --> Payload[UUID5 + vector + payload<br/>题目/subject/人工标签/source/license]
        Payload --> QCreate{集合不存在?}
        QCreate -->|是| CreateCollection[Qdrant create_collection<br/>实际向量维度 + cosine]
        QCreate -->|否| Upsert
        CreateCollection --> Upsert["Qdrant upsert(wait=true)<br/>重复点按稳定 ID 覆盖"]
        Upsert --> IndexSummary[终端 JSON 摘要<br/>collection/indexed/vector_size]
    end

    subgraph Feedback[人工反馈评测：evaluate_feedback.py]
        DB -->|human_annotations JOIN questions<br/>LEFT JOIN label_suggestions by prediction_id| FeedbackRows[按 annotation_id 聚合<br/>预测默认标签 vs 最终标签]
        FeedbackRows --> Compare[TP/FP/FN<br/>exact match / human change]
        Compare --> Report[evaluation_report.json<br/>micro/macro/per-label/per-subject/version]
        Report --> Gates{micro F1、exact match、change rate 门禁}
        Gates -->|全部通过| GO[GO，退出码 0]
        Gates -->|有门禁失败| NOGO[NO-GO，退出码 2]
    end

    subgraph SFT[指令微调数据：export_sft_dataset.py]
        DB -->|completed + latest_annotation_id| SFTReader[MySQL stream_results<br/>questions JOIN latest annotation]
        SFTReader --> SFTExample[system/user/assistant messages<br/>标签 reason 来自 taxonomy]
        SFTExample --> SFTSplit[SHA-256 external_id 固定切分]
        SFTSplit --> TrainJSONL[train JSONL<br/>OpenAI messages + metadata]
        SFTSplit --> EvalJSONL[eval JSONL<br/>同格式固定评测集]
        TrainJSONL --> SFTTrainer[train_sft_lora.py<br/>chat template -> text -> TRL SFTTrainer]
        EvalJSONL --> SFTTrainer
        SFTTrainer --> Adapter[(LoRA adapter + tokenizer<br/>training_summary.json)]
    end
```

## 5. 历史分类器训练时序

```mermaid
sequenceDiagram
    autonumber
    actor Operator as 训练操作者/CI
    participant CLI as train_historical_model.py
    participant Source as MySQL: questions + human_annotations / JSONL
    participant Split as stable SHA-256 split
    participant Vectorizer as HashingVectorizer
    participant SGD as 每标签 SGDClassifier
    participant Eval as 留出集评测
    participant FS as 本地工件目录

    Operator->>CLI: --database-url 或 --input-jsonl、epochs、batch-size、output
    CLI->>CLI: 校验参数，读取 TAG_DEFINITIONS 固定 label_codes
    loop 每个 epoch（example_factory 重新读取数据）
        CLI->>Source: 流式 SELECT questions JOIN human_annotations（latest_annotation_id，status=completed）或 JSONL
        Source-->>CLI: TrainingExample(external_id, QuestionInput, labels)
        CLI->>Split: SHA-256(external_id) % 100
        Split-->>CLI: train/eval bucket
        loop 每个训练 batch（排除 eval bucket）
            CLI->>CLI: question_to_model_text(学科/题干/答案/解析)
            CLI->>Vectorizer: transform(texts)
            Vectorizer-->>CLI: sparse char n-gram matrix
            loop 每个受控标签 code
                CLI->>SGD: partial_fit(matrix, 0/1 target, classes=[0,1], positive sample_weight)
                SGD-->>CLI: 更新该标签分类头
            end
        end
    end
    CLI->>Source: 再次读取 questions + human_annotations 或 JSONL，筛选固定 eval bucket
    Source-->>Eval: holdout batches + ground-truth labels
    CLI->>Vectorizer: transform(eval texts)
    Vectorizer-->>Eval: sparse feature matrix
    Eval->>SGD: 各标签 predict_proba >= 0.5
    SGD-->>Eval: predicted tag sets
    Eval->>Eval: micro/macro F1 + exact match
    Eval-->>CLI: metrics + unknown label counts
    CLI->>FS: joblib.dump 到同目录临时文件
    CLI->>FS: 原子 replace -> label_model.joblib
    CLI->>FS: 写 .manifest.json（version、参数、指标）
    CLI-->>Operator: JSON 摘要（artifact、manifest、metrics）
```

> 训练是监督学习，不调用 LLM 或 Embedding API。`trained_examples_with_repeats` 按 epoch 累加，是处理次数而非唯一训练样本数。模型工件保存后，在线 `SklearnHistoricalLabelModel` 用相同 `question_to_model_text` 和 HashingVectorizer 生成每个受控标签的概率。

## 6. Dense RAG 索引时序

```mermaid
sequenceDiagram
    autonumber
    actor Operator as 索引操作者/CI
    participant CLI as index_rag.py
    participant Source as MySQL: questions + human_annotations / JSONL
    participant Embed as Embedding API
    participant Qdrant as Qdrant
    participant FS as CLI stdout

    Operator->>CLI: 数据源、collection、embedding-model、batch-size
    CLI->>CLI: 要求 API key；构造 OpenAIEmbeddings
    opt 显式传入 --recreate 且目标集合已存在
        CLI->>Qdrant: delete_collection(collection)
        Note over CLI,Qdrant: 这是破坏性删除；脚本不会自动做 alias 切换
    end
    CLI->>Source: 流式 SELECT questions JOIN human_annotations（latest_annotation_id，status=completed）或 JSONL
    Source-->>CLI: TrainingExample batches
    loop 每个 batch
        CLI->>CLI: question_to_model_text(question)
        CLI->>Embed: embed_documents(texts)
        Embed-->>CLI: vectors（逐项数量与向量维度校验）
        opt 首批向量到达且 collection 不存在
            CLI->>Qdrant: create_collection(size=实际维度, distance=cosine)
            Qdrant-->>CLI: collection ready
        end
        CLI->>CLI: UUID5(external_id) + payload<br/>question_id/subject/stem/answer/human_labels/source/license
        CLI->>Qdrant: upsert(points, wait=true)
        Qdrant-->>CLI: 当前批次确认
    end
    CLI-->>FS: collection/indexed/vector_size JSON
```

## 7. 反馈评测和 SFT 导出时序

```mermaid
sequenceDiagram
    autonumber
    actor Operator as 评测/导出操作者
    participant EvalCLI as evaluate_feedback.py
    participant ExportCLI as export_sft_dataset.py
    participant SFTCLI as train_sft_lora.py
    participant Tokenizer as 模型 tokenizer / chat template
    participant Trainer as TRL SFTTrainer + PEFT
    participant DB as MySQL: questions, human_annotations, label_suggestions
    participant Metric as 指标与门禁
    participant Files as JSON/JSONL 文件

    par 反馈评测
        Operator->>EvalCLI: database-url + model-version? + limit + thresholds
        EvalCLI->>DB: SELECT human_annotations JOIN questions LEFT JOIN label_suggestions<br/>按 annotation_id、suggestion_id 排序
        DB-->>EvalCLI: selected_tags、tag_code、selected_by_default、subject、model_version
        EvalCLI->>EvalCLI: 聚合每条 annotation 的预测/人工标签集合
        EvalCLI->>Metric: expected=selected_tags；predicted=默认勾选标签
        Metric->>Metric: TP/FP/FN、micro/macro F1、exact match、change rate
        Metric->>Metric: 门禁 micro_f1 / exact_match / human_change_rate
        Metric-->>EvalCLI: GO 或 NO-GO
        EvalCLI->>Files: 写 evaluation_report.json
        EvalCLI-->>Operator: JSON 报告；NO-GO 时退出码 2
    and SFT 数据导出
        Operator->>ExportCLI: database-url + train/eval output + evaluation-percent + limit
        ExportCLI->>DB: SELECT questions JOIN human_annotations（q.latest_annotation_id，q.status=completed）
        DB-->>ExportCLI: 题目字段、selected_tags、source/license
        ExportCLI->>ExportCLI: require_known_tag 取 reason<br/>组装 system/user/assistant JSON messages
        ExportCLI->>ExportCLI: SHA-256(external_id) 稳定划分
        ExportCLI->>Files: 逐行写 sft_train.jsonl / sft_eval.jsonl<br/>每行含 messages + metadata
        ExportCLI-->>Operator: train/eval 样本计数 JSON
        Operator->>SFTCLI: --train-file artifacts/sft_train.jsonl<br/>--eval-file artifacts/sft_eval.jsonl --model-id ...
        SFTCLI->>Files: 读取 id/messages/metadata JSONL
        Files-->>SFTCLI: train/eval 消息样本
        SFTCLI->>Tokenizer: apply_chat_template(messages, tokenize=false)
        Tokenizer-->>SFTCLI: 模型 chat template 格式的 text
        SFTCLI->>Trainer: text Dataset + LoRA 配置 + train/eval
        Trainer-->>SFTCLI: train/eval metrics
        SFTCLI->>Files: 保存 adapter/、tokenizer/、training_summary.json
    end
```

> `evaluate_feedback.py` 的预测集合来自 `label_suggestions.selected_by_default`，人工集合来自 `human_annotations.selected_tags`；它产出评测 JSON 和 GO/NO-GO，不直接训练或部署模型。`export_sft_dataset.py` 只从 MySQL 导出 JSONL，不调用 LLM，也不执行微调；同项目的 `train_sft_lora.py` 直接读取其 `messages` 格式并负责 LoRA/QLoRA 训练。

SFT 依赖与运行示例（从 `llm-day1` 根目录安装依赖；以下训练命令在后端目录执行）：

```bash
pip install -r requirements_day29_day31.txt
cd question_labeling_system/backend
PYTHONPATH=. python -m app.training.export_sft_dataset \
    --train-output ../artifacts/sft_train.jsonl \
    --eval-output ../artifacts/sft_eval.jsonl
PYTHONPATH=. python -m app.training.train_sft_lora \
    --train-file artifacts/sft_train.jsonl \
    --eval-file artifacts/sft_eval.jsonl \
    --model-id Qwen/Qwen2.5-0.5B-Instruct \
    --output-dir artifacts/sft_lora
```

加 `--qlora` 可请求 4-bit QLoRA；需要 CUDA 和 `bitsandbytes`。无 CUDA 时脚本会警告并回退到 LoRA。训练需要适用于所选模型的 tokenizer chat template。

## 8. 脚本边界速查

| 入口 | 输入事实 | 外部模型/存储调用 | 输出 | 是否修改业务 MySQL |
| --- | --- | --- | --- | --- |
| `POST /api/v1/questions` | API `QuestionInput` | 可同步触发 LangGraph | task + 可选 prediction | 是：题目、预测任务、审计；预测时追加运行和建议 |
| `app.worker` | `prediction_jobs` + `questions` | 历史模型、Sparse/Dense RAG、Chat LLM | `PredictionResult` | 是：运行快照、建议、队列状态、审计 |
| `POST /api/v1/tasks/{id}/submit` | 人工选中标签、预测 ID、版本和幂等键 | 无 LLM/RAG | 人工提交差异 | 是：最终标注、题目状态、审计 |
| `train_historical_model.py` | MySQL 最新人工标注或输入 JSONL | sklearn，不调用在线 LLM | `.joblib` + manifest JSON | 否，只读 MySQL |
| `index_rag.py` | MySQL 最新人工标注或输入 JSONL | Embedding API + Qdrant | Qdrant Dense points | 否，只读 MySQL；可写/删除 Qdrant 集合 |
| `evaluate_feedback.py` | MySQL 预测建议与人工最终标签 | 无 | evaluation JSON + GO/NO-GO | 否，只读 MySQL |
| `export_sft_dataset.py` | MySQL 最新人工最终标签 | 无 | train/eval JSONL | 否，只读 MySQL |
| `train_sft_lora.py` | exporter 的 train/eval `messages` JSONL | Hugging Face base model + TRL/PEFT；可选 bitsandbytes | LoRA adapter、tokenizer、training summary | 否 |
