// 将后端 Subject 枚举映射为 TypeScript 字面量联合，编译期阻止未知学科。
export type Subject = 'chinese' | 'math' | 'english'

// 定义题目输入，与后端 QuestionInput 保持字段一致。
export interface QuestionInput {
  // 保存上游题库的稳定业务 ID。
  external_id: string
  // 保存题目所属学科。
  subject: Subject
  // 保存题干原文。
  stem: string
  // 保存标准答案或可接受答案集合。
  reference_answer: string
  // 保存可选题目解析。
  analysis: string
  // 保存题目数据来源。
  source: string
  // 保存可追溯的内部记录或公开数据集地址。
  source_uri: string
  // 保存内部授权或公开许可证名称。
  license_name: string
}

// 定义服务端受控标签字典项。
export interface TagDefinition {
  // 保存稳定机器编码。
  code: string
  // 保存中文展示名称。
  name: string
  // 保存业务判定说明。
  description: string
  // 保存标签适用学科。
  subjects: Subject[]
  // 标记需要重点人工复核的属性。
  high_risk: boolean
}

// 定义 RAG 返回的一条人工确认相似题。
export interface RetrievedEvidence {
  // 保存证据追踪 ID。
  evidence_id: string
  // 保存历史题业务 ID。
  question_id: string
  // 保存历史题学科。
  subject: Subject
  // 保存历史题题干。
  stem: string
  // 保存历史题标准答案。
  reference_answer: string
  // 保存历史题人工标签。
  human_labels: string[]
  // 保存归一化融合分数。
  score: number
  // 保存内部题库或其他合规来源。
  source: string
  // 保存证据来源地址。
  source_uri: string
  // 保存证据许可证或内部授权名称。
  license_name: string
}

// 定义模型链对单个标签的最终建议。
export interface LabelSuggestion {
  // 保存标签机器编码。
  code: string
  // 保存标签中文名称。
  name: string
  // 保存融合置信度。
  confidence: number
  // 表示页面初始是否勾选。
  selected_by_default: boolean
  // 标记高风险标签。
  high_risk: boolean
  // 保存面向人工的判断依据。
  reason: string
  // 保存规则、监督模型、RAG 和 LLM 的分数。
  source_scores: Record<string, number>
  // 保存关联证据 ID。
  evidence_ids: string[]
}

// 定义一次完整预测运行。
export interface PredictionResult {
  // 保存预测使用的题目快照。
  question: QuestionInput
  // 保存当前学科所有标签建议。
  suggestions: LabelSuggestion[]
  // 保存 RAG 相似题证据。
  evidence: RetrievedEvidence[]
  // 表示模型结果不能绕过人工审核。
  requires_human_review: boolean
  // 保存完整模型链版本。
  model_version: string
  // 保存降级或数据质量警告。
  warnings: string[]
}

// 定义任务租约和乐观锁字段。
export interface TaskView {
  // 保存数据库任务 ID。
  id: number
  // 保存题目内容。
  question: QuestionInput
  // 保存 pending、in_review 或 completed。
  status: string
  // 保存提交时必须回传的版本号。
  version: number
  // 保存当前领取人。
  claimed_by: string | null
  // 保存租约到期 ISO 时间。
  claimed_until: string | null
}

// 定义补录页面一次请求需要的完整数据。
export interface TaskBundleResponse {
  // 保存任务状态。
  task: TaskView
  // 保存预测运行 UUID。
  prediction_id: string
  // 保存模型标签与证据。
  prediction: PredictionResult
}

// 定义按学科返回的标签字典。
export interface TaxonomyResponse {
  // 保存请求学科。
  subject: Subject
  // 保存全部可选标签。
  tags: TagDefinition[]
}

// 定义服务端验证后的当前操作员身份。
export interface PrincipalResponse {
  // 保存 JWT subject 或开发用户 ID。
  user_id: string
  // 保存服务端确认的角色集合。
  roles: string[]
}

// 定义人工提交请求。
export interface SubmitAnnotationRequest {
  // 指明人工核对的预测版本。
  prediction_id: string
  // 保存人工最终选择标签。
  selected_tags: string[]
  // 保存可选复核备注。
  note: string
  // 保存客户端生成的幂等键。
  idempotency_key: string
  // 保存页面读取时的任务版本。
  expected_version: number
}

// 定义人工提交响应。
export interface AnnotationResponse {
  // 保存人工标注 ID。
  annotation_id: number
  // 保存题目 ID。
  question_id: number
  // 保存最终标签。
  selected_tags: string[]
  // 保存相对模型新增标签。
  added_tags: string[]
  // 保存相对模型移除标签。
  removed_tags: string[]
  // 保存提交操作员 ID。
  operator_id: string
  // 保存提交时间。
  created_at: string
}

// 定义后端统一错误体。
export interface ApiErrorPayload {
  // 保存稳定错误码。
  code?: string
  // 保存面向用户的错误信息。
  message?: string
  // FastAPI 默认验证错误可能使用 detail。
  detail?: string | unknown[]
  // 保存排查请求 ID。
  request_id?: string
}
