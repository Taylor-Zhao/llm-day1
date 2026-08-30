// presentation.ts 集中保存不依赖 React 状态的展示纯函数。

// percent 将后端 0 到 1 分数转换为整数百分比。
export function percent(value: number | undefined): string {
  // undefined 表示当前信号源没有给该标签打分。
  return `${Math.round((value ?? 0) * 100)}%`
}

// formatLease 将后端 ISO 时间转换为操作员本地时分。
export function formatLease(value: string | null): string {
  // 空值表示任务没有租约。
  if (!value) {
    // 返回明确占位文本。
    return '未设置'
  }
  // Intl.DateTimeFormat 会使用浏览器本地时区。
  return new Intl.DateTimeFormat('zh-CN', {
    // 显示两位小时。
    hour: '2-digit',
    // 显示两位分钟。
    minute: '2-digit',
  }).format(new Date(value))
}

// sourceLabel 将内部信号编码转换为业务人员可读名称。
export function sourceLabel(source: string): string {
  // 映射键是后端 ensemble 节点使用的稳定来源编码。
  const labels: Record<string, string> = {
    // 确定性规则分数。
    rule: '规则',
    // 百万人工样本训练模型分数。
    historical_model: '历史模型',
    // Dense/Sparse RAG 分数。
    rag: '相似题',
    // LangChain LLM 结构化判断分数。
    llm: 'LLM',
  }
  // 未知来源保留原编码，方便发现新后端信号。
  return labels[source] ?? source
}
