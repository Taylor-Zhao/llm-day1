// Subject 使用字面量联合，对应后端 Subject 枚举。
export type Subject = 'chinese' | 'math' | 'english'

// QuestionInput 与 FastAPI 的 QuestionInput 模型保持一致。
export interface QuestionInput {
  // 上游题库的稳定业务 ID。
  external_id: string
  // 语文、数学或英语。
  subject: Subject
  // 填空题题干。
  stem: string
  // 标准答案或可接受答案集合。
  reference_answer: string
  // 可选题目解析。
  analysis: string
  // 数据源短名称。
  source: string
  // 内部记录或公开数据集页面地址。
  source_uri: string
  // 内部授权或公开许可证名称。
  license_name: string
}

// TagDefinition 是服务端管理的受控标签合同。
export interface TagDefinition {
  // 数据库和模型使用的稳定编码。
  code: string
  // 业务人员看到的中文名称。
  name: string
  // 标签判定标准。
  description: string
  // 标签可用于哪些学科。
  subjects: Subject[]
  // 是否需要业务人员重点复核。
  high_risk: boolean
}

// RetrievedEvidence 表示 Hybrid RAG 召回的人工确认相似题。
export interface RetrievedEvidence {
  // 本次引用使用的证据 ID。
  evidence_id: string
  // 历史题业务 ID。
  question_id: string
  // 历史题学科。
  subject: Subject
  // 历史题题干。
  stem: string
  // 历史题人工答案。
  reference_answer: string
  // 历史题最终人工标签。
  human_labels: string[]
  // RRF 归一化相似度。
  score: number
  // 内部题库或合规外部来源。
  source: string
  // 可追溯来源地址。
  source_uri: string
  // 证据许可证或内部授权。
  license_name: string
}

// LabelSuggestion 是四路信号融合后的单标签建议。
export interface LabelSuggestion {
  // 标签编码。
  code: string
  // 标签中文名。
  name: string
  // 0 到 1 的融合置信度。
  confidence: number
  // 页面初始是否勾选。
  selected_by_default: boolean
  // 是否需要重点人工复核。
  high_risk: boolean
  // 面向业务人员的判断理由。
  reason: string
  // 规则、历史模型、RAG 和 LLM 的独立分数。
  source_scores: Record<string, number>
  // 支持该标签的证据 ID。
  evidence_ids: string[]
}

// PredictionResult 是一次可审计模型链运行。
export interface PredictionResult {
  // 推理使用的题目快照。
  question: QuestionInput
  // 当前学科所有可选标签及其分数。
  suggestions: LabelSuggestion[]
  // RAG 相似题证据。
  evidence: RetrievedEvidence[]
  // 生产策略要求所有结果人工终审。
  requires_human_review: boolean
  // 监督模型、Prompt 和融合策略的组合版本。
  model_version: string
  // RAG/LLM 降级或数据质量警告。
  warnings: string[]
}

// TaskView 表示人工任务租约与乐观锁状态。
export interface TaskView {
  // 数据库任务主键。
  id: number
  // 题目内容。
  question: QuestionInput
  // pending、in_review 或 completed。
  status: string
  // 提交时必须回传的乐观锁版本。
  version: number
  // 当前领取人。
  claimed_by: string | null
  // 租约到期 ISO 时间。
  claimed_until: string | null
}

// TaskBundleResponse 是补录页面一次需要的完整快照。
export interface TaskBundleResponse {
  // 任务和版本状态。
  task: TaskView
  // 人工正在核验的预测 UUID。
  prediction_id: string
  // 标签建议与 RAG 证据。
  prediction: PredictionResult
}

// TaxonomyResponse 是按学科过滤的标签字典。
export interface TaxonomyResponse {
  // 请求学科。
  subject: Subject
  // 该学科全部标签。
  tags: TagDefinition[]
}

// PrincipalResponse 是服务端验证后的当前操作员。
export interface PrincipalResponse {
  // JWT subject 或本地调试用户 ID。
  user_id: string
  // 服务端确认的角色集合。
  roles: string[]
}

// SubmitAnnotationRequest 是人工最终提交命令。
export interface SubmitAnnotationRequest {
  // 人工看到的模型预测版本。
  prediction_id: string
  // 人工最终选择的标签。
  selected_tags: string[]
  // 可选复核备注。
  note: string
  // 网络重试必须复用的提交幂等键。
  idempotency_key: string
  // 页面领取时的任务版本。
  expected_version: number
}

// AnnotationResponse 是人工提交结果与模型差异。
export interface AnnotationResponse {
  // 人工标注记录 ID。
  annotation_id: number
  // 题目数据库 ID。
  question_id: number
  // 人工最终标签。
  selected_tags: string[]
  // 相对模型新增标签。
  added_tags: string[]
  // 相对模型取消标签。
  removed_tags: string[]
  // 服务端认证操作员 ID。
  operator_id: string
  // 提交时间。
  created_at: string
}

// ApiErrorPayload 兼容业务错误和 FastAPI 默认验证错误。
export interface ApiErrorPayload {
  // 稳定机器错误码。
  code?: string
  // 面向业务人员的错误信息。
  message?: string
  // FastAPI 默认 detail 可能是文本或验证错误数组。
  detail?: string | unknown[]
  // 跨前后端排查请求 ID。
  request_id?: string
}
