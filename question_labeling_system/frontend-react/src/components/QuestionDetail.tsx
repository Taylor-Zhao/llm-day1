// 导入题目任务视图类型。
import type { TaskView } from '../types'

// QuestionDetailProps 明确组件只读取任务，不修改任务。
interface QuestionDetailProps {
  // 当前人工领取的任务快照。
  task: TaskView
}

// QuestionDetail 负责题干、答案、解析和来源展示。
export function QuestionDetail({ task }: QuestionDetailProps) {
  // 为减少 JSX 重复先提取题目对象。
  const question = task.question
  // 返回语义化题目分区。
  return (
    // section 表示独立题目内容区域。
    <section className="question-section">
      {/* 标题行同时展示上游 ID 和来源。 */}
      <div className="section-heading">
        {/* 左侧标题组。 */}
        <div>
          {/* 区块层级提示。 */}
          <span className="eyebrow">题目内容</span>
          {/* 上游业务 ID 比数据库主键更方便题库人员识别。 */}
          <h2>{question.external_id}</h2>
        </div>
        {/* title 在文本截断时仍可查看完整来源。 */}
        <span className="source-chip" title={`${question.source} · ${question.license_name}`}>
          {/* 可见区域保持短名称。 */}
          {question.source}
        </span>
      </div>
      {/* React 使用花括号把 JavaScript 字符串插入 JSX。 */}
      <p className="question-stem">{question.stem}</p>
      {/* 参考答案是人工判定标签的重要事实。 */}
      <div className="answer-band">
        {/* 字段名称。 */}
        <span>参考答案</span>
        {/* 答案原文。 */}
        <strong>{question.reference_answer}</strong>
      </div>
      {/* && 是 React 条件渲染的一种方式，对应 Vue 的 v-if。 */}
      {question.analysis && (
        // 只有解析非空时创建该 DOM。
        <div className="analysis-text">
          {/* 字段名称。 */}
          <span>题目解析</span>
          {/* 解析原文。 */}
          <p>{question.analysis}</p>
        </div>
      )}
    </section>
  )
}
