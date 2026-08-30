// 导入链路状态使用的 Lucide React 图标。
import { CheckCircle2, Database, ShieldCheck } from 'lucide-react'
// 导入独立学科配置，保持组件文件只导出组件以支持 Fast Refresh。
import { SUBJECT_OPTIONS } from '../subjects'
// 导入后端学科字面量类型。
import type { Subject } from '../types'

// SubjectSidebarProps 是父组件传入侧栏的最小合同。
interface SubjectSidebarProps {
  // 当前学科决定哪个按钮处于 active 状态。
  subject: Subject
  // 点击其他学科时通知父 Hook。
  onSubjectChange: (subject: Subject) => void
}

// SubjectSidebar 只负责展示导航，不拥有业务状态。
export function SubjectSidebar({ subject, onSubjectChange }: SubjectSidebarProps) {
  // JSX 返回的 aside 会成为页面可访问导航区域。
  return (
    // aria-label 帮助屏幕阅读器识别导航用途。
    <aside className="subject-sidebar" aria-label="学科队列">
      {/* 侧栏小标题。 */}
      <p className="sidebar-label">补录队列</p>
      {/* map 是 React 中最常见的列表渲染方式，对应 Vue 的 v-for。 */}
      {SUBJECT_OPTIONS.map((item) => {
        // 首字母大写变量可以在 JSX 中作为组件渲染。
        const Icon = item.icon
        // 每次迭代返回一个稳定 key 的按钮。
        return (
          <button
            // key 帮助 React 在重渲染时识别同一学科节点。
            key={item.code}
            // 防止按钮在未来放入 form 后默认提交。
            type="button"
            // JSX 使用 className 而不是 HTML class。
            className={`subject-button${subject === item.code ? ' active' : ''}`}
            // aria-current 表达当前导航页。
            aria-current={subject === item.code ? 'page' : undefined}
            // 箭头函数把当前 item.code 传给父组件事件。
            onClick={() => onSubjectChange(item.code)}
          >
            {/* 图标纯装饰，因此对辅助技术隐藏。 */}
            <Icon size={20} aria-hidden="true" />
            {/* 两行学科说明。 */}
            <span>
              {/* 学科主名称。 */}
              <strong>{item.label}</strong>
              {/* 学科次级说明。 */}
              <small>{item.caption}</small>
            </span>
          </button>
        )
      })}
      {/* 分隔操作导航和模型链状态。 */}
      <div className="sidebar-divider" aria-hidden="true" />
      {/* 展示固定生产链路状态。 */}
      <div className="pipeline-state">
        {/* 状态区域标题。 */}
        <p className="sidebar-label">当前链路</p>
        {/* 历史监督模型节点。 */}
        <span><CheckCircle2 size={15} aria-hidden="true" /> 历史模型</span>
        {/* 混合检索节点。 */}
        <span><Database size={15} aria-hidden="true" /> Hybrid RAG</span>
        {/* 最终人工确认节点。 */}
        <span><ShieldCheck size={15} aria-hidden="true" /> 人工终审</span>
      </div>
    </aside>
  )
}
