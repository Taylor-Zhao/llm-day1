// 导入标签状态图标。
import { Check, Sparkles } from 'lucide-react'
// 导入 Hook 暴露的标签行类型。
import type { LabelRow } from '../hooks/useLabelingWorkbench'
// 导入纯展示格式化函数。
import { percent, sourceLabel } from '../presentation'

// LabelReviewListProps 描述标签区域的输入和事件。
interface LabelReviewListProps {
  // 合并后的全部标签行。
  rows: LabelRow[]
  // 人工当前选择数量。
  selectedCount: number
  // 人工相对模型调整数量。
  changedCount: number
  // 点击标签时由父 Hook 更新状态。
  onToggle: (code: string) => void
}

// LabelReviewList 渲染标签复选列表与各信号分数。
export function LabelReviewList({ rows, selectedCount, changedCount, onToggle }: LabelReviewListProps) {
  // Fragment 允许返回相邻标题区和列表而不增加多余 DOM。
  return (
    <>
      {/* 标签区域标题和人工修改统计。 */}
      <div className="labels-heading">
        {/* 左侧标题。 */}
        <div>
          {/* 强调最终决策属于人工。 */}
          <span className="eyebrow">人工终审</span>
          {/* 区块主标题。 */}
          <h2>标签选择</h2>
        </div>
        {/* 当前选择与模型差异统计。 */}
        <div className="selection-summary">
          {/* 已选标签数量。 */}
          <strong>{selectedCount}</strong>
          {/* 数量含义。 */}
          <span>已选</span>
          {/* 视觉分隔线不承载语义。 */}
          <i aria-hidden="true" />
          {/* 人工调整数量。 */}
          <strong>{changedCount}</strong>
          {/* 数量含义。 */}
          <span>调整</span>
        </div>
      </div>
      {/* role=group 将多个开关组织为一个可访问控件组。 */}
      <div className="label-list" role="group" aria-label="题目标签">
        {/* map 将每个标签对象转换成一个按钮节点。 */}
        {rows.map((row) => (
          <button
            // 标签编码是稳定 key，不能使用数组 index。
            key={row.definition.code}
            // 明确普通按钮行为。
            type="button"
            // 根据人工选择和 AI 默认建议组合 CSS 状态。
            className={`label-row${row.selected ? ' selected' : ''}${row.suggestion?.selected_by_default ? ' suggested' : ''}`}
            // aria-pressed 让按钮具备开关语义。
            aria-pressed={row.selected}
            // 点击时只上报标签编码，组件不直接修改数组。
            onClick={() => onToggle(row.definition.code)}
          >
            {/* 固定尺寸选择框防止文字变化移动布局。 */}
            <span className="selection-box" aria-hidden="true">
              {/* 三元/&& 条件仅在选中时渲染勾号。 */}
              {row.selected && <Check size={15} />}
            </span>
            {/* 标签说明占用中间弹性列。 */}
            <span className="label-copy">
              {/* 名称与状态标记可以在窄屏换行。 */}
              <span className="label-title-line">
                {/* 中文标签名称。 */}
                <strong>{row.definition.name}</strong>
                {/* 模型默认勾选时显示 AI 建议标记。 */}
                {row.suggestion?.selected_by_default && (
                  <em className="ai-badge"><Sparkles size={12} /> AI 建议</em>
                )}
                {/* 高风险标签始终提醒人工重点复核。 */}
                {row.definition.high_risk && <em className="risk-badge">重点复核</em>}
              </span>
              {/* 有本次推理理由时优先展示，否则展示标签字典说明。 */}
              <span className="label-reason">{row.suggestion?.reason ?? row.definition.description}</span>
              {/* Object.entries 将分数字典转换成可 map 的键值数组。 */}
              {Object.keys(row.suggestion?.source_scores ?? {}).length > 0 && (
                <span className="source-scores">
                  {/* 各信号分数使用 sourceName 作为稳定 key。 */}
                  {Object.entries(row.suggestion?.source_scores ?? {}).map(([sourceName, score]) => (
                    <span key={sourceName}>
                      {/* 格式化内部来源编码和分数。 */}
                      {sourceLabel(sourceName)} <b>{percent(score)}</b>
                    </span>
                  ))}
                </span>
              )}
            </span>
            {/* 最右列展示融合置信度。 */}
            <span className="confidence-block">
              {/* 没有模型分数时显示 0%。 */}
              <strong>{percent(row.suggestion?.confidence)}</strong>
              {/* 原生 progress 提供可访问数值语义。 */}
              <progress value={row.suggestion?.confidence ?? 0} max={1} />
            </span>
          </button>
        ))}
      </div>
    </>
  )
}
