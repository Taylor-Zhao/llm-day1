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
## 2.1 Worker 消费预测队列时序

`app.worker` 是独立的后台进程，专门消费 `prediction_jobs`。它不提供 HTTP 接口；启动时读取 `Settings.from_env()`，并通过 `build_container()` 装配与 API 相同的数据库、历史模型、RAG 和 LLM。API 使用 `predict_now=false` 入队后，由 Worker 执行实际预测。

```mermaid
sequenceDiagram
    autonumber
    actor Ops as 运维/容器平台
    participant Worker as app.worker
    participant Settings as Settings + build_container
    participant App as LabelingApplicationService
    participant Repo as QuestionRepository
    participant DB as MySQL: prediction_jobs, questions, prediction_runs, label_suggestions, audit_events
    participant Graph as LangGraph workflow

    Ops->>Worker: python -m app.worker --worker-id demo-worker
    Worker->>Worker: 解析 lease-seconds=120、max-attempts=3、poll-seconds=2
    Worker->>Settings: Settings.from_env() + build_container()
    Settings-->>Worker: 与 API 同配置的 service/repository/model/RAG
    loop 常驻消费循环
        Worker->>App: process_next_prediction_job(worker_id, lease, max_attempts)
        App->>Repo: claim_prediction_job(worker_id, lease, max_attempts)
        Repo->>DB: SELECT prediction_jobs 中可运行且租约空闲/过期的任务<br/>FOR UPDATE SKIP LOCKED
        alt 没有可运行任务
            DB-->>Repo: none
            Repo-->>App: None
            App-->>Worker: consumed=false
            alt --once
                Worker-->>Ops: 退出（本轮没有任务）
            else 持续运行
                Worker->>Worker: sleep(poll-seconds)
            end
        else 领取到任务
            Repo->>DB: UPDATE prediction_jobs: status=running、attempts+1、leased_by、leased_until
            DB-->>Repo: StoredPredictionJob(question_id, attempts, lease)
            Repo-->>App: StoredPredictionJob
            App->>Repo: 读取 questions；检查最新 prediction_runs
            Repo->>DB: SELECT questions + latest prediction_runs
            DB-->>Repo: 题目与已有快照
            App->>Graph: predict_task(force=true)
            Note over App,Graph: force=true，确保本次队列任务执行新预测；规则、Sparse/Dense RAG、历史模型、LLM 和融合详见第 2 节
            alt 预测成功
                Graph-->>App: PredictionResult + model_version + warnings
                App->>Repo: save_prediction(result, latency_ms)
                Repo->>DB: 同一事务 INSERT prediction_runs、label_suggestions、audit_events<br/>UPDATE prediction_jobs.status=completed 并清租约
                DB-->>Repo: prediction_id
                Repo-->>App: prediction_id
                App-->>Worker: consumed=true
            else 模型链或保存前处理失败
                Graph-->>App: exception
                App->>Repo: fail_prediction_job(job_id, worker_id, error, max_attempts)
                Repo->>DB: UPDATE prediction_jobs.last_error 并清租约；INSERT audit_events
                alt attempts < max_attempts
                    Repo->>DB: status=pending；available_at=now + min(300, 2^attempts)
                else attempts 达到上限
                    Repo->>DB: status=failed
                end
                App-->>Worker: consumed=true（失败已记录，Worker 继续循环）
            end
            opt --once
                Worker-->>Ops: 处理一个任务后退出
            end
        end
    end
```

持续运行的命令（API 和 Worker 必须使用相同 `DATABASE_URL` 与模型/RAG 配置）：

```bash
cd /Users/zhaoyonggng/work/llm-day1/question_labeling_system/backend
PYTHONPATH=. ../../.venv/bin/python -m app.worker \
  --worker-id demo-worker \
  --lease-seconds 120 \
  --max-attempts 3 \
  --poll-seconds 2
```

调试时可加 `--once`，最多处理一个队列项后退出；若队列为空则立即退出。Worker 空闲时才 sleep；处理过一项（成功或失败）后会继续下一轮。预测异常不会让整个 Worker 进程退出：租约被释放，任务按退避重新排队，或在尝试次数耗尽后进入 `failed`。

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

## 9. 实战串联：从样例题到人工反馈和微调

本节用仓库自带的原创样例 `data/sample_labeled_questions.jsonl` 和一条新导入的春天成语题串起主要脚本与 API。样例文件中的第一条大致如下：

```json
{"external_id":"train-cn-001","subject":"chinese","stem":"请写出一个描写春天的成语：____。","reference_answer":"春暖花开，其他合理答案均可。","analysis":"开放表达","source":"project_synthetic_seed","license_name":"project-authored","selected_tags":["answer_not_unique","semantic_equivalence"]}
```

其中 `selected_tags` 是人工确认标签。历史分类器、RAG 索引和 SFT exporter 都能用它，但它们产生的产物不同：分类器产出 sklearn 工件；索引脚本产出 Qdrant points；SFT exporter 产出聊天消息 JSONL。

### 9.1 训练历史标签分类器

从仓库根目录进入后端目录，并用隔离的本地 SQLite 文件运行：

```bash
cd /Users/zhaoyonggng/work/llm-day1
source .venv/bin/activate
cd question_labeling_system/backend
export DATABASE_URL="sqlite:////tmp/question-labeling-demo.db"

PYTHONPATH=. ../../.venv/bin/python -m app.training.train_historical_model \
    --input-jsonl ../data/sample_labeled_questions.jsonl \
    --output ../artifacts/demo_label_model.joblib \
    --epochs 2 \
    --batch-size 64 \
    --evaluation-percent 20
```

请使用一个尚不存在的演示数据库文件；如果该路径已存在且含有旧待办，请换一个新文件名。API 和后续评测/导出命令必须连接同一个数据库文件。

输入是每行含 `external_id/subject/stem/reference_answer/selected_tags` 的规范训练 JSONL；这里直接读文件，不查询 MySQL。输出在 `question_labeling_system/artifacts/`：

- `demo_label_model.joblib`：HashingVectorizer 和每个标签的 SGDClassifier。
- `demo_label_model.joblib.manifest.json`：模型版本、参数、留出集指标和未知标签统计。
- stdout JSON：`artifact`、`manifest`、`version` 和训练/评测摘要。

仓库样例集较小，固定哈希后评测样本数可能很少；真实训练应使用足够多且经审核的人工标注数据。

### 9.2 可选：把同一批题写入 Qdrant

Qdrant 和 Embedding 服务必须先运行。以 OpenAI-compatible 本地 Embedding 服务为例，按实际地址设置环境变量：

```bash
export QDRANT_URL="http://127.0.0.1:6333"
export OPENAI_BASE_URL="http://127.0.0.1:11434/v1"
export OPENAI_EMBEDDING_MODEL="nomic-embed-text"
export OPENAI_API_KEY="ollama"

PYTHONPATH=. ../../.venv/bin/python -m app.training.index_rag \
    --input-jsonl ../data/sample_labeled_questions.jsonl \
    --qdrant-url "$QDRANT_URL" \
    --collection demo_labeled_questions \
    --base-url "$OPENAI_BASE_URL" \
    --embedding-model "$OPENAI_EMBEDDING_MODEL" \
    --api-key "$OPENAI_API_KEY"
```

输入仍是同一个 JSONL。输出写到 Qdrant collection `demo_labeled_questions`，终端返回类似 `{"collection":"demo_labeled_questions","indexed":8,"vector_size":768}`；点数和向量维度以实际文件、Embedding 模型为准。这里不改 SQLite/MySQL。

> 本地 API 默认 `RAG_MODE=memory`，不会自动查询这个 Qdrant collection。线上 `RAG_MODE=hybrid` 才会同时走 MySQL FULLTEXT 和 Qdrant；该模式需要 MySQL（含 FULLTEXT ngram 索引）、Qdrant 和匹配的 Embedding 配置，不能仅把本地 SQLite 切到 hybrid。

### 9.3 启动本地 API 并导入一条新题

以下设置让 API 复用刚训练的历史分类器，同时保留默认内存 RAG 和禁用 LLM 的本地模式：

若只想快速把仓库的 8 条原创样例写入本地库并生成预测，可在**另一个独立数据库文件**上运行 `seed_local.py`：

```bash
DATABASE_URL="sqlite:////tmp/question-labeling-seed.db" \
    PYTHONPATH=. ../../.venv/bin/python -m app.training.seed_local
```

输出类似 `{"database_url":"sqlite:////tmp/question-labeling-seed.db","imported":8,"predicted":8}`。它会创建题目、预测快照、建议和审计记录，但不创建 `human_annotations`；因此该数据库不能直接用于后面的反馈评测和 SFT 导出。单独数据库也避免它的 8 条待办排在 API 演示题之前。

```bash
export HISTORICAL_MODEL_MODE=artifact
export HISTORICAL_MODEL_PATH="$PWD/../artifacts/demo_label_model.joblib"
export RAG_MODE=memory
export LLM_MODE=disabled

PYTHONPATH=. ../../.venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port 8010
```

在另一个终端用 `POST /api/v1/questions` 导入一条新题。开发环境默认启用调试身份；下面的角色同时包含 `importer` 和 `labeler`：

```bash
curl -sS -X POST http://127.0.0.1:8010/api/v1/questions \
    -H 'Content-Type: application/json' \
    -H 'X-Debug-User: demo-reviewer' \
    -H 'X-Debug-Roles: importer,labeler' \
    -d '{
        "question": {
            "external_id": "api-cn-spring-001",
            "subject": "chinese",
            "stem": "请写出一个描写春天的成语：____。",
            "reference_answer": "春暖花开，其他合理答案均可。",
            "analysis": "开放表达题",
            "source": "manual-demo",
            "source_uri": "",
            "license_name": "project-authored"
        },
        "predict_now": true
    }'
```

`predict_now` 默认为 `true`。接口先在 `questions`、`prediction_jobs`、`audit_events` 中创建数据，再同步执行预测，将快照和逐标签建议写入 `prediction_runs`、`label_suggestions`，并完成预测队列记录。HTTP 状态为 `201`。下面是响应节选，只展开一个建议和一条证据；实际 `suggestions` 包含该学科所有适用标签，ID 和分数由本次运行生成：

```json
{
    "task": {
        "id": 1,
        "question": {"external_id": "api-cn-spring-001", "subject": "chinese", "stem": "请写出一个描写春天的成语：____。", "reference_answer": "春暖花开，其他合理答案均可。", "analysis": "开放表达题", "source": "manual-demo", "source_uri": "", "license_name": "project-authored"},
        "status": "pending",
        "version": 1,
        "claimed_by": null,
        "claimed_until": null
    },
    "prediction_id": "<本次预测 UUID>",
    "prediction": {
        "suggestions": [
            {"code": "answer_not_unique", "name": "答案不唯一", "confidence": 0.8791, "selected_by_default": true, "high_risk": true, "reason": "...", "source_scores": {"historical_model": 0.83, "rule": 0.96, "rag": 0.9167}, "evidence_ids": ["seed-cn-1"]}
        ],
        "evidence": [
            {"evidence_id": "seed-cn-1", "question_id": "seed-cn-1", "subject": "chinese", "stem": "请写出一个描写春天的成语：____。", "reference_answer": "春暖花开；其他合理答案也可。", "human_labels": ["answer_not_unique", "semantic_equivalence"], "score": 0.9167, "source": "project_synthetic_seed", "source_uri": "", "license_name": "project-authored"}
        ],
        "requires_human_review": true,
        "model_version": "historical=...;reasoner=llm-disabled;ensemble=v1",
        "warnings": ["LLM 推理降级：RuntimeError"]
    }
}
```

这是本地降级模式的示例：`warnings` 会说明 LLM 未启用；RAG 分数对应当前内存检索示例，历史模型分数 `0.83` 和最终分数用于展示结构，实际值以训练工件运行结果为准。生产部署应使用 JWT，不能使用 `X-Debug-*`。

### 9.4 领取并提交人工最终标签

先领取语文队列中的下一题。新建的隔离数据库只有上一步这条题，因此领取到的就是它；已有其他待办时，队列按创建时间取最早任务：

```bash
CLAIM=$(curl -sS 'http://127.0.0.1:8010/api/v1/tasks/next?subject=chinese' \
    -H 'X-Debug-User: demo-reviewer' \
    -H 'X-Debug-Roles: importer,labeler')
printf '%s\n' "$CLAIM" | jq '{task: .task, prediction_id: .prediction_id, suggestions: .prediction.suggestions}'

QUESTION_ID=$(printf '%s' "$CLAIM" | jq -r '.task.id')
PREDICTION_ID=$(printf '%s' "$CLAIM" | jq -r '.prediction_id')
TASK_VERSION=$(printf '%s' "$CLAIM" | jq -r '.task.version')
```

`GET /api/v1/tasks/next` 将 `questions.status` 改为 `in_review`、增加 `version` 并设置操作员租约。操作员核对建议后，提交最终标签：

```bash
curl -sS -X POST "http://127.0.0.1:8010/api/v1/tasks/$QUESTION_ID/submit" \
    -H 'Content-Type: application/json' \
    -H 'X-Debug-User: demo-reviewer' \
    -H 'X-Debug-Roles: importer,labeler' \
    -d "{\
        \"prediction_id\": \"$PREDICTION_ID\",\
        \"selected_tags\": [\"answer_not_unique\", \"semantic_equivalence\"],\
        \"note\": \"人工核验：可接受多个合理成语\",\
        \"idempotency_key\": \"demo-spring-submit-001\",\
        \"expected_version\": $TASK_VERSION\
    }"
```

成功返回 `201`，结构为：

```json
{
    "annotation_id": 1,
    "question_id": 1,
    "selected_tags": ["answer_not_unique", "semantic_equivalence"],
    "added_tags": ["semantic_equivalence"],
    "removed_tags": [],
    "operator_id": "demo-reviewer",
    "created_at": "<UTC 时间>"
}
```

这里的 `added_tags`/`removed_tags` 是相对该次预测默认勾选项计算的，所以上例的差异数组只是示意。事务写入 `human_annotations`，同时更新 `questions.status/latest_annotation_id/version` 并写入 `audit_events`。重复使用同一幂等键会返回原标注，不会重复创建。

### 9.5 反馈评测、SFT 导出和 LoRA 训练

人工提交后，评测脚本读取 `human_annotations`、`questions` 和 `label_suggestions`，比较模型默认标签与人工最终标签：

```bash
export DATABASE_URL="sqlite:////tmp/question-labeling-demo.db"
PYTHONPATH=. ../../.venv/bin/python -m app.evaluation.evaluate_feedback \
    --database-url "$DATABASE_URL" \
    --output ../artifacts/demo_feedback_report.json
```

输出 `demo_feedback_report.json` 和 stdout JSON，字段包括 micro/macro F1、exact match、人工修改率、逐标签/逐学科指标及 `GO`/`NO-GO`。小型演示数据通常达不到默认发布门槛，脚本仍会先写报告，然后以退出码 `2` 表示 `NO-GO`；不要为了演示而把它当作生产验收结果。

导出人工最终标签为 SFT train/eval JSONL：

```bash
export DATABASE_URL="sqlite:////tmp/question-labeling-demo.db"
PYTHONPATH=. ../../.venv/bin/python -m app.training.export_sft_dataset \
    --database-url "$DATABASE_URL" \
    --train-output ../artifacts/sft_train.jsonl \
    --eval-output ../artifacts/sft_eval.jsonl \
    --evaluation-percent 20
```

每行输出形如 `{"id":"api-cn-spring-001","messages":[{"role":"system","content":"..."},{"role":"user","content":"..."},{"role":"assistant","content":"{\\"labels\\":[...],\\"warnings\\":[]}"}],"metadata":{"subject":"chinese","source":"manual-demo","source_uri":"","license_name":"project-authored"}}`。训练和评测按 `id` 的稳定 SHA-256 桶划分。两个文件都必须至少有一条记录；单条人工标注通常会导致某个 split 为空并触发 `ValueError`，因此需要足够多的已完成题目，或调整留出比例。

安装独立的 SFT 依赖后，直接把上述两个文件交给配套训练脚本：

```bash
# 在 llm-day1 仓库根目录安装已有的训练依赖清单。
cd /Users/zhaoyonggng/work/llm-day1
source .venv/bin/activate
pip install -r requirements_day29_day31.txt
cd question_labeling_system/backend

PYTHONPATH=. ../../.venv/bin/python -m app.training.train_sft_lora \
    --train-file artifacts/sft_train.jsonl \
    --eval-file artifacts/sft_eval.jsonl \
    --model-id Qwen/Qwen2.5-0.5B-Instruct \
    --output-dir artifacts/demo_sft_lora \
    --epochs 1
```

输出 `artifacts/demo_sft_lora/adapter/`、`tokenizer/` 和 `training_summary.json`。加 `--qlora` 会请求 4-bit QLoRA，需要兼容的 CUDA 环境和 `bitsandbytes`；否则使用 LoRA。adapter 是增量权重，不会自动替换线上 ChatModel；API 当前仍通过 `OPENAI_*` 配置调用在线 LLM。要在线使用该 adapter，还需额外实现并部署对应的模型加载/推理适配器。

### 9.6 异步导入分支

大批导入可用 `predict_now: false`：API 立即返回 `prediction_id: ""` 和 pending 提示，MySQL `prediction_jobs` 中保留待处理记录。另开终端、使用同一份 `DATABASE_URL` 和模型/RAG 环境启动 Worker：

```bash
cd /Users/zhaoyonggng/work/llm-day1/question_labeling_system/backend
PYTHONPATH=. ../../.venv/bin/python -m app.worker \
    --worker-id demo-worker --once
```

`--once` 最多处理一个任务；持续服务时去掉 `--once`。成功后 Worker 写入 `prediction_runs`/`label_suggestions` 并更新 `prediction_jobs`；随后调用 `GET /api/v1/tasks/{question_id}` 可读取完整预测。API 和 Worker 必须连接同一个数据库并使用一致的模型、Embedding、Qdrant 配置。

## 10. 全流程字段血缘与字段含义

下面把训练样本、在线题目、检索证据、预测、人工反馈和两种离线评测/训练产物连起来。箭头表示字段被复制、转换或作为下游计算输入，不表示同名字段一定具有相同含义。

```mermaid
flowchart TD
    Raw[历史训练 JSONL 或 MySQL<br/>external_id / subject / stem / answer / selected_tags]
    Raw --> HistTrain[train_historical_model.py<br/>question text + selected_tags]
    HistTrain --> HistArtifact[Joblib + manifest<br/>vectorizer / classifiers / metrics]
    HistArtifact --> HistScore[historical_scores<br/>每个 tag 的 predict_proba]

    NewQuestion[POST /api/v1/questions<br/>QuestionInput + predict_now] --> QuestionDB[(questions<br/>题目 / status / version / claim lease)]
    NewQuestion --> JobDB[(prediction_jobs<br/>pending / attempts / lease)]
    QuestionDB --> Retrieval[题目 subject + stem + answer]
    Retrieval --> Sparse[MySQL FULLTEXT<br/>questions JOIN latest human_annotations]
    Retrieval --> Dense[Qdrant Dense search<br/>subject filter + embedding]
    Sparse --> Evidence[RRF -> RetrievedEvidence<br/>score / human_labels / source / license]
    Dense --> Evidence
    Evidence --> LLMInput[LLM prompt evidence_json]
    Evidence --> RagScore[RAG label scores]
    HistScore --> Fusion[ensemble weighted scores]
    RagScore --> Fusion
    LLMInput --> Fusion
    Fusion --> Suggest[LabelSuggestion<br/>confidence / selected_by_default / evidence_ids]
    Suggest --> PredictionDB[(prediction_runs<br/>result_json / version / latency)]
    Suggest --> SuggestDB[(label_suggestions<br/>tag_code / confidence / default / source_scores)]

    Claim[GET /api/v1/tasks/next<br/>subject filter] --> QuestionDB
    Claim --> Lease[questions.status / claimed_by / claimed_until / version]
    Suggest --> Review[人工核对]
    Review --> Submit["POST /tasks/{id}/submit<br/>selected_tags / expected_version / idempotency_key"]
    Submit --> AnnotationDB[(human_annotations<br/>selected / added / removed / model_version)]
    Submit --> QuestionDB
    AnnotationDB --> FeedbackEval[evaluate_feedback.py]
    SuggestDB --> FeedbackEval
    FeedbackEval --> EvalReport[evaluation_report.json<br/>F1 / exact match / change rate / gates]
    AnnotationDB --> SFTExport[export_sft_dataset.py]
    SFTExport --> SFTFiles[train/eval JSONL<br/>id / messages / metadata]
    SFTFiles --> SFTTrain[train_sft_lora.py<br/>tokenizer template + TRL / PEFT]
    SFTTrain --> Adapter[LoRA adapter + training_summary.json]
```

### 10.1 题目与任务生命周期字段

| 字段 | 出现位置/用途 | 值变化怎么理解 |
| --- | --- | --- |
| `external_id` | 导入、训练 JSONL；上游题目稳定业务 ID | 用于导入幂等和稳定哈希切分；它是标识符，不是质量分数，值大小没有好坏含义 |
| `subject` | `QuestionInput`、MySQL、RAG 查询 | `chinese`/`math`/`english`；控制标签集合和检索过滤，不存在大小方向 |
| `stem`、`reference_answer`、`analysis` | `QuestionInput`；分类器文本、Sparse/Dense 查询和 Prompt 输入 | 是内容，不是指标。答案/解析变动会改变分类特征和检索结果 |
| `source`、`source_uri`、`license_name` | 题目和证据的溯源/授权元数据 | 用于追溯与治理；字段非空或某个数值大小不代表模型质量更高 |
| `predict_now` | `POST /api/v1/questions` 请求，默认 `true` | `true` 同步执行预测；`false` 留给 `prediction_jobs`/Worker 异步消费。它是路由开关，不是置信度 |
| `task.id` / `question_id` | API 路径和表外键 | 数据库 ID，仅用于定位题目；值大小没有业务优劣 |
| `status` | `questions.status` | 常见流转为 `pending -> in_review -> completed`；表示工作状态，不代表模型预测质量 |
| `version` / `expected_version` | 领取响应、提交请求 | 领取/提交时递增；提交必须匹配读取到的版本。值更大只表示状态版本更新，不表示标签更准确 |
| `claimed_by`、`claimed_until` | `questions` 任务租约 | 记录领取人和租约截止时间；过期后可重新领取。时间更晚不代表质量更好 |
| `prediction_jobs.status` | Worker 数据库队列 | `pending -> running -> completed`，超过重试上限则 `failed`；`attempts` 增加表示重试次数增加，不代表预测变好 |
| `prediction_id` | 一次 `prediction_runs` 快照的 ID；提交时必须带回 | 用于绑定“人工核对的是哪一次模型结果”；是 UUID，不看大小 |
| `idempotency_key` | 人工提交请求 | 同一 key 重试会返回同一 annotation，避免重复写入；它不是结果特征 |
| `operator_id`、`request_id` | 人员审计和请求追踪 | 用于追责/排障，不进入预测特征，也不应作为 Prompt 标签信号 |

### 10.2 RAG 查询字段与证据字段

| 字段/参数 | 用途 | 升高/降低或取值变化的含义 |
| --- | --- | --- |
| 查询 `subject` | Dense 侧 Qdrant payload 精确过滤；Sparse 侧限定同学科已完成题目 | 改学科会改变候选空间，不是分数变化；当前代码不跨学科召回 |
| 查询文本 `stem + reference_answer` | MySQL FULLTEXT 和 Qdrant `embed_query()` 的查询内容 | 文本内容变化会改变召回结果；不是单调的“越长越好” |
| `RAG_LIMIT` | 最终证据条数上限，默认 `6`；RRF 候选池为 `max(limit*3, 20)` | 增大可提高召回覆盖，但也会增加噪声、Prompt 长度和时延；不保证准确率上升 |
| `score` | `RetrievedEvidence.score`，memory 模式是 Jaccard 相似度，hybrid 模式是归一化 RRF 排名分 | 越高表示在当前检索算法下越相似/排名越靠前；不等于标签正确概率或授权可信度 |
| `human_labels` | 证据原题经人工确认的标签 | 是可追溯标签证据；不代表当前题一定应使用相同标签 |
| `evidence_id`、`question_id` | 证据引用和回溯原题 | 标识符，无大小方向 |
| `source`、`source_uri`、`license_name` | 表示证据来源、URI 和授权 | 用于审查来源和使用权限，不是相关性分数 |
| RRF `sparse_weight` / `dense_weight` | 默认 `0.45` / `0.55`，控制两路排名贡献 | 提高某一路权重会提高该路排名对最终排序的影响，不代表召回质量必然提升 |
| RRF `k` | 默认 `60`，平滑高低名次的差异 | 增大时名次差带来的分数差通常更小；它是融合超参数，不是质量指标 |

### 10.3 预测建议字段

| 字段 | 含义 | 值的方向/解读 |
| --- | --- | --- |
| `suggestions[].code` / `name` | 标签稳定编码及展示名称 | 类别字段，不是分数 |
| `source_scores.rule` | 规则命中强度 | 越高表示当前规则的匹配分更高，不是统计校准概率 |
| `source_scores.historical_model` | sklearn 对该标签的 `predict_proba()` 输出 | 越高表示分类器更倾向该标签；未做概率校准时不能直接解释为“有 x% 真实正确率” |
| `source_scores.rag` | 相似人工题带来的标签分数，按证据分数和排名衰减计算 | 越高表示当前 RAG 信号更支持该标签，不是独立模型准确率 |
| `source_scores.llm` | LLM 对标签给出的 `ReasonedLabel.confidence` | 越高表示 LLM 自报置信度更高；不是统计保证的正确概率 |
| `confidence` | ensemble 对可用信号按权重归一化后的最终分数 | 越高越容易默认勾选；它是融合分数，不应当直接当作校准概率 |
| `selected_by_default` | `confidence >= SELECTION_THRESHOLD`，默认阈值 `0.58` | `true` 表示页面预选；阈值调高会减少默认选择，通常提高精确率倾向但可能增加漏标，必须评测确认 |
| `high_risk` | taxonomy 标记的风险属性 | `true` 意味着应重点人工核验，不表示模型分数低 |
| `reason` | 面向补录人员的理由；LLM 不可用时使用固定回退文案 | 文字说明，不适合按长短评质量 |
| `evidence_ids` | 支持此标签的 RAG/LLM 证据引用 | 数量多不等于质量高；应检查证据相关性与标签一致性 |
| `model_version` | 历史分类器、Reasoner 和 ensemble 版本组合 | 用于追踪/切片评测；版本字符串不同不直接代表模型更优 |
| `warnings` | RAG/LLM 降级、证据问题等信息 | 出现 warning 表示某组件降级或提示不确定性；数量减少不一定意味着真实质量提升，也需看监控覆盖 |

### 10.4 补录申领与提交字段

| 字段 | 阶段 | 含义与方向 |
| --- | --- | --- |
| `subject` 查询参数 | `GET /api/v1/tasks/next` | 可选学科队列过滤；不传表示不按学科筛选 |
| `claimed_by` / `claimed_until` | 申领响应、`questions` | 当前领取人和租约到期时间；租约到期可被重新领取 |
| `expected_version` | 提交请求 | 必须等于当前 `questions.version`；不匹配表示页面陈旧，返回冲突，而不是强行覆盖 |
| `selected_tags` | 提交请求和 `human_annotations` | 人工最终标签集合；这是训练/评测的主要真值字段，允许空数组表示确认无标签 |
| `note` | 提交请求和 `human_annotations` | 人工备注，最多 2,000 字符；描述上下文，不作为当前模型特征 |
| `added_tags` | 提交响应和 `human_annotations` | `selected_tags - 默认选中标签`；越多通常表示模型漏标更多，但也受题目复杂度和操作员习惯影响 |
| `removed_tags` | 提交响应和 `human_annotations` | `默认选中标签 - selected_tags`；越多通常表示模型误报更多，同样要按样本量和人群切片分析 |
| `annotation_id` | 提交响应 | 人工记录 ID，仅用于定位记录 |
| `created_at` | 提交响应、数据库 | 人工确认时间；可用于时间漂移分析，不直接表示标签质量 |

### 10.5 历史模型训练与评测字段

历史模型训练输入 JSONL 每行关键字段：

| 字段 | 含义 | 变化影响 |
| --- | --- | --- |
| `external_id` | 稳定题目 ID | SHA-256 决定固定 train/eval 桶；改 ID 会改变切分，不是内容质量提升 |
| `subject` / `stem` / `reference_answer` / `analysis` | 题目模型输入字段 | 内容改变会改变特征和预测；文本本身无大小方向 |
| `selected_tags` | 人工标签编码数组 | 决定各标签 0/1 监督目标；缺标或错标会直接污染训练真值 |
| `source` / `source_uri` / `license_name` | 数据治理元数据 | 保留来源、授权和追溯信息；不直接参与 `question_to_model_text()` 特征 |

训练参数及方向：

| 参数 | 默认/范围 | 增大或减小意味着什么 |
| --- | --- | --- |
| `epochs` | 默认 `2` | 更多轮次增加重复训练次数和耗时；可能收敛，也可能过拟合 |
| `batch_size` | 默认 `2048` | 更大通常需要更多峰值内存、减少更新步数；不单调提升质量 |
| `evaluation_percent` | CLI 默认 `10`，允许 `1..40` | 增大后固定留出样本增多、可用于训练的样本减少 |
| `n_features` | 默认 `2**18` | 增大可减少 Hashing 冲突但增加分类器存储/计算成本；需实测 |
| `positive_weight` | 默认 `3.0` | 增大正例权重通常让模型更愿意报正类，可能提高 recall、同时增加 false positives；需看逐标签指标 |
| `threshold` | `evaluate_model()` 默认 `0.5` | 提高预测正类门槛通常减少正类预测、precision 倾向提高而 recall 倾向降低；不是 CLI 参数 |
| `trained_examples_with_repeats` | 每轮训练的样本数累加 | 这是“处理次数”，约等于训练样本数 × epochs，不是唯一题目数；变大不代表数据集变大 |
| `unknown_labels` | 未出现在当前 `TAG_DEFINITIONS` 的历史标签计数 | 增大表示标签字典/历史数据不一致或旧标签未治理，不是模型质量分 |

训练留出集结果写入 manifest：

| 指标字段 | 含义 | 越大/越小如何解读 |
| --- | --- | --- |
| `evaluation_examples` | 实际留出题数量 | 是样本量，不是质量分；样本太少时指标波动很大 |
| `micro_precision` | 汇总所有标签的 TP/(TP+FP) | 越高表示被预测为正的标签中正确比例越高 |
| `micro_recall` | 汇总所有标签的 TP/(TP+FN) | 越高表示人工真值标签被找回得越多 |
| `micro_f1` | micro precision/recall 调和平均 | 越高通常越好，但需同时看 precision/recall 和各标签分布 |
| `macro_f1` | 各标签 F1 的平均 | 越高通常越好，较能暴露稀有标签表现；当前零样本标签会以 0 计入 |
| `exact_match_rate` | 整题预测标签集合与真值完全相同的比例 | 越高表示无需改标签的题比例越高；严格指标，常低于 micro F1 |

### 10.6 反馈评测报告字段

`evaluate_feedback.py` 的 `predicted_tags` 是人工提交时那次预测中 `selected_by_default=true` 的标签；`actual_tags` 是人工 `selected_tags`。它评的是**历史线上预测与人工最终选择的一致程度**，不是重新运行候选分类器。

| 报告字段 | 含义 | 越大/越小如何解读 |
| --- | --- | --- |
| `examples` | 参与评测的人工记录数 | 样本越多通常估计越稳定；不代表模型更好 |
| `versions` | 被评测的模型版本及样本数 | 用于按版本切片比较，不比较字符串大小 |
| `micro.precision` / `recall` / `f1` | 跨标签汇总的分类指标 | 一般越高越好；precision 高但 recall 低可能表示漏标多 |
| `macro_f1` | taxonomy 各标签 F1 的均值 | 一般越高越好；稀有标签低分会拉低它 |
| `exact_match_rate` | 预测默认集合与人工最终集合完全相同的题比例 | 越高越好，表示整题建议无需修改的比例更高 |
| `human_change_rate` | 至少新增或移除一个标签的题目比例 | 越低通常越好，表示更多题无需修改；需同时关注标签/学科和审核规范 |
| `labels_added_by_humans` | 人工新增标签总数，等于 FN 总量 | 越高通常说明漏标更多，但也可能受标签规范变化影响 |
| `labels_removed_by_humans` | 人工移除标签总数，等于 FP 总量 | 越高通常说明误报更多 |
| `per_label` / `per_subject` | 按标签/学科拆分的 precision、recall、F1 和 TP/FP/FN | 用于定位退化类别；分数高低需结合每组样本量 |
| `gates` | `micro_f1`、`exact_match_rate`、`human_change_rate` 是否达阈值 | 全部 true 才是 `GO`；门槛不是指标本身 |
| `release_decision` | 总门禁 | `GO` 表示三个 gate 全过；否则 `NO-GO`，脚本退出码 `2` |

### 10.7 SFT JSONL 与 LoRA 训练字段

SFT 导出行字段：

| 字段 | 含义 | 变化方向/注意事项 |
| --- | --- | --- |
| `id` | `external_id`，稳定样本 ID | 决定固定哈希切分；仅是 ID |
| `messages[].role` | `system` / `user` / `assistant` | 消息角色，不是指标 |
| `messages[].content` | system 任务约束、user 题目文本、assistant 目标 JSON | assistant 标签是监督目标；其中 `reason` 来自 taxonomy description，不是人工逐题撰写理由 |
| `metadata.subject/source/source_uri/license_name` | 学科、来源、URI、授权元数据 | 保留来源治理信息；当前训练脚本主要把 `messages` 转成文本，metadata 不作为模型输入 |
| `evaluation_percent` | SHA-256 固定留出的 eval 比例，范围 `1..40` | 增大后 eval 样本增多、train 样本减少；小数据可能导致某个 split 为空 |

LoRA 训练参数/摘要字段：

| 字段 | 含义 | 增大/降低如何解读 |
| --- | --- | --- |
| `model_id` | 基础 instruct/chat LLM | 不存在通用“越大越好”；更大模型通常需要更多显存、成本和时延 |
| `max_seq_len` | 单样本最大 token 长度 | 增大可容纳更长题目/对话，但增加显存和计算；超长样本仍受模型/Trainer 截断策略影响 |
| `epochs` / `max_steps` | 数据遍历轮数/最大优化步数 | 增加训练量和耗时，也可能过拟合；`max_steps=-1` 表示不额外限制步数 |
| `batch_size` / `grad_accum` | 单设备批量/梯度累积步数 | 增大有效 batch 通常提升吞吐稳定性但消耗更多内存或训练时间；不保证效果提升 |
| `learning_rate` | 优化步长 | 过高可能训练不稳定，过低可能收敛慢；不能简单按越大越好理解 |
| `lora_r` / `lora_alpha` | Adapter 秩/缩放参数 | 增大 rank 通常提高可训练容量和资源消耗；alpha 改变 LoRA 更新缩放，不是质量指标 |
| `lora_dropout` | Adapter dropout | 适当增加可能抑制过拟合，过大可能欠拟合 |
| `method` | `lora` 或 `qlora` | 记录训练方式；QLoRA 4-bit 主要节省显存，不代表模型质量更高 |
| `train_examples` / `eval_examples` | 实际加载的训练/评测样本数 | 样本更多有助于稳定性但不保证质量；要看数据质量与去重 |
| `train_metrics` / `eval_metrics` | TRL Trainer 返回的训练/评测指标字典 | `train_loss`/`eval_loss` 越低代表 token 预测损失越低；eval loss 上升可能是过拟合，但它们不是标签 F1 或人工准确率 |
| `adapter_dir` | LoRA adapter 目录 | 文件位置，不是指标；运行推理还需配套基础模型 |

SFT loss 与历史分类器的 F1、`evaluate_feedback.py` 的发布门禁**不是同一指标**，不能直接比较数值大小。要判断业务标签质量，需把 SFT 模型接入推理、在独立固定集生成预测，再用任务级指标和人工修改率评估。
