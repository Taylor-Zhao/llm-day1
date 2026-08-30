// 导入数据库和搜索图标。
import { Database, Search } from 'lucide-react'
// 导入展示函数。
import { percent } from '../presentation'
// 导入 RAG 证据和标签定义类型。
import type { RetrievedEvidence, TagDefinition } from '../types'

// EvidencePanelProps 描述证据面板的只读输入。
interface EvidencePanelProps {
  // Hybrid RAG 按相关性排序后的证据。
  evidence: RetrievedEvidence[]
  // 当前学科标签字典用于把编码转成中文名。
  taxonomy: TagDefinition[]
}

// EvidencePanel 展示相似题、人工答案、标签和数据授权。
export function EvidencePanel({ evidence, taxonomy }: EvidencePanelProps) {
  // tagName 在当前渲染中按标签编码查找中文名。
  function tagName(code: string): string {
    // 找不到时保留编码，帮助发现字典版本不一致。
    return taxonomy.find((item) => item.code === code)?.name ?? code
  }

  // 返回语义化侧栏。
  return (
    <aside className="evidence-panel" aria-label="相似题证据">
      {/* 面板标题和证据数量。 */}
      <div className="evidence-heading">
        {/* 装饰图标。 */}
        <Database size={18} aria-hidden="true" />
        {/* 面板主标题。 */}
        <h2>相似题证据</h2>
        {/* 证据数量。 */}
        <span>{evidence.length}</span>
      </div>
      {/* React 使用三元表达式在证据列表和空状态之间选择。 */}
      {evidence.length > 0 ? (
        // 有证据时渲染按排名排列的文章列表。
        <div className="evidence-list">
          {/* 每条 Evidence 转换为一个 article。 */}
          {evidence.map((item) => (
            <article key={item.evidence_id} className="evidence-item">
              {/* 证据 ID 和融合分数。 */}
              <header>
                {/* title 保留完整 ID。 */}
                <code title={item.evidence_id}>{item.evidence_id}</code>
                {/* 归一化 RRF 分数。 */}
                <strong>{percent(item.score)}</strong>
              </header>
              {/* 历史题题干。 */}
              <p>{item.stem}</p>
              {/* 人工确认答案。 */}
              <div className="evidence-answer">
                {/* 字段名称。 */}
                <span>人工答案</span>
                {/* 答案原文。 */}
                <strong>{item.reference_answer}</strong>
              </div>
              {/* 历史人工标签列表。 */}
              <div className="evidence-tags">
                {/* 同一证据中的标签编码天然唯一。 */}
                {item.human_labels.map((code) => <span key={code}>{tagName(code)}</span>)}
              </div>
              {/* 同时展示来源短名称和许可证。 */}
              <footer>{item.source} · {item.license_name}</footer>
            </article>
          ))}
        </div>
      ) : (
        // 无证据时明确展示降级，不让人工误以为页面故障。
        <div className="evidence-empty">
          {/* 空状态图标。 */}
          <Search size={24} aria-hidden="true" />
          {/* 空状态说明。 */}
          <span>本次未召回相似题</span>
        </div>
      )}
    </aside>
  )
}
