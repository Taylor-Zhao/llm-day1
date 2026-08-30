// ChangeEvent 为受控 textarea 事件提供精确类型。
import type { ChangeEvent } from 'react'
// 导入刷新和提交图标。
import { RefreshCw, Send } from 'lucide-react'

// SubmissionBarProps 定义受控表单值、状态和命令。
interface SubmissionBarProps {
  // 当前人工备注。
  note: string
  // 是否正在刷新模型。
  refreshing: boolean
  // 是否正在提交人工结果。
  submitting: boolean
  // 备注变化时上报新字符串。
  onNoteChange: (note: string) => void
  // 请求重跑模型建议。
  onRefresh: () => void
  // 提交人工最终结果。
  onSubmit: () => void
}

// SubmissionBar 是固定在视口底部的高频操作区。
export function SubmissionBar({ note, refreshing, submitting, onNoteChange, onRefresh, onSubmit }: SubmissionBarProps) {
  // handleNoteChange 从 React 合成事件读取 textarea 值。
  function handleNoteChange(event: ChangeEvent<HTMLTextAreaElement>): void {
    // 父 Hook 是备注状态的唯一所有者。
    onNoteChange(event.target.value)
  }

  // 返回受控输入和两个命令按钮。
  return (
    <footer className="submission-bar">
      {/* label 包裹输入，使点击字段名也能聚焦 textarea。 */}
      <label className="note-field">
        {/* 输入名称。 */}
        <span>复核备注</span>
        {/* value + onChange 构成 React 受控输入，对应 Vue 的 v-model。 */}
        <textarea
          // 输入值始终来自 Reducer state。
          value={note}
          // 每次输入都发送 noteChanged 动作。
          onChange={handleNoteChange}
          // 与后端 max_length 保持一致。
          maxLength={2000}
          // 设置初始可见行数。
          rows={2}
          // 提示可记录的信息。
          placeholder="可选：记录模型误报、漏标或特殊判定依据"
        />
      </label>
      {/* 两个命令共享稳定按钮区域。 */}
      <div className="submission-actions">
        {/* 重跑模型不会保存人工最终标签。 */}
        <button type="button" className="secondary-button" disabled={refreshing || submitting} onClick={onRefresh}>
          {/* 刷新期间只旋转图标，不改变按钮尺寸。 */}
          <RefreshCw size={17} className={refreshing ? 'spinning' : undefined} aria-hidden="true" />
          {/* 命令文本。 */}
          更新建议
        </button>
        {/* 提交按钮将当前人工选择写入事实表。 */}
        <button type="button" className="primary-button" disabled={submitting || refreshing} onClick={onSubmit}>
          {/* 装饰图标。 */}
          <Send size={17} aria-hidden="true" />
          {/* 提交中显示稳定反馈。 */}
          {submitting ? '提交中' : '确认并提交'}
        </button>
      </div>
    </footer>
  )
}
