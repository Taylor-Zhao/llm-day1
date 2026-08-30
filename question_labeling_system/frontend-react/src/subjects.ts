// 导入三个学科使用的 Lucide React 图标和图标组件类型。
import { BookOpenCheck, Calculator, Languages, type LucideIcon } from 'lucide-react'
// 导入后端学科字面量类型。
import type { Subject } from './types'

// SubjectOption 描述一项稳定学科导航配置。
export interface SubjectOption {
  // 后端识别的学科编码。
  code: Subject
  // 业务人员看到的名称。
  label: string
  // 学科内容简述。
  caption: string
  // React 组件可以作为普通值保存在配置中。
  icon: LucideIcon
}

// SUBJECT_OPTIONS 是应用级只读导航配置。
export const SUBJECT_OPTIONS: SubjectOption[] = [
  // 语文队列。
  { code: 'chinese', label: '语文', caption: '阅读与表达', icon: BookOpenCheck },
  // 数学队列。
  { code: 'math', label: '数学', caption: '计算与推理', icon: Calculator },
  // 英语队列。
  { code: 'english', label: '英语', caption: '语法与语境', icon: Languages },
]