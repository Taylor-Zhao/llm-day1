<script setup lang="ts">
// 导入 Vue 计算属性，用于从补录状态派生展示数据。
import { computed } from 'vue'
// 导入当前官方 Lucide Vue 图标组件，避免手写 SVG。
import {
  AlertTriangle,
  BookOpenCheck,
  Calculator,
  Check,
  CheckCircle2,
  Clock3,
  Database,
  Inbox,
  Info,
  Languages,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  UserRound,
} from '@lucide/vue'
// 导入封装业务状态与动作的 Composition API 组合函数。
import { useLabelingWorkbench } from './composables/useLabelingWorkbench'
// 导入学科字面量类型，确保导航配置合法。
import type { Subject } from './types'

// 定义三个学科导航项及其图标。
const subjects = [
  // 配置语文队列入口。
  { code: 'chinese', label: '语文', caption: '阅读与表达', icon: BookOpenCheck },
  // 配置数学队列入口。
  { code: 'math', label: '数学', caption: '计算与推理', icon: Calculator },
  // 配置英语队列入口。
  { code: 'english', label: '英语', caption: '语法与语境', icon: Languages },
] satisfies Array<{ code: Subject; label: string; caption: string; icon: typeof BookOpenCheck }>

// 解构补录工作台需要的响应式状态与用户动作。
const {
  subject,
  principal,
  taxonomy,
  bundle,
  selectedTags,
  note,
  loading,
  refreshing,
  submitting,
  errorMessage,
  successMessage,
  labelRows,
  suggestedCount,
  changedCount,
  loadNext,
  changeSubject,
  toggleTag,
  rerunPrediction,
  submitReview,
} = useLabelingWorkbench()

// 统计人工当前选中的标签数量。
const selectedCount = computed(() => selectedTags.value.length)
// 读取当前模型链版本并提供空状态回退。
const modelVersion = computed(() => bundle.value?.prediction.model_version ?? '等待任务')

// 将 0 到 1 的分数转换为整数百分比。
function percent(value: number | undefined): string {
  // 缺少分数时显示 0%，保持布局稳定。
  return `${Math.round((value ?? 0) * 100)}%`
}

// 将 ISO 租约时间转换为本地时分。
function formatLease(value: string | null): string {
  // 空租约显示未领取状态。
  if (!value) {
    // 返回明确占位文本。
    return '未设置'
  }
  // 使用浏览器本地时区格式化时间。
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

// 将模型信号源编码转换为业务人员可读名称。
function sourceLabel(source: string): string {
  // 定义固定低基数显示字典。
  const labels: Record<string, string> = {
    // 确定性题型规则。
    rule: '规则',
    // 百万历史人工数据训练模型。
    historical_model: '历史模型',
    // Dense 与 Sparse 融合检索。
    rag: '相似题',
    // LangChain 结构化 LLM 推理。
    llm: 'LLM',
  }
  // 未知来源保留原编码，便于排错。
  return labels[source] ?? source
}

// 根据标签编码查找中文名称，RAG 证据展示复用。
function tagName(code: string): string {
  // 优先使用当前学科标签字典。
  return taxonomy.value.find((item) => item.code === code)?.name ?? code
}
</script>

<template>
  <!-- 整个应用使用固定顶栏、学科导航和工作区三段布局。 -->
  <div class="app-shell">
    <!-- 顶栏展示产品、当前模型链和登录主体。 -->
    <header class="topbar">
      <!-- 产品标识是第一视口主信号。 -->
      <div class="brand-block">
        <!-- 方形标志使用文字而非额外图片资产。 -->
        <span class="brand-mark" aria-hidden="true">审</span>
        <!-- 展示产品名称与环境。 -->
        <div>
          <!-- 产品名称。 -->
          <strong>题审台</strong>
          <!-- 当前系统定位。 -->
          <small>智能补录工作台</small>
        </div>
      </div>
      <!-- 展示当前模型运行版本，便于人工反馈追溯。 -->
      <div class="model-status" :title="modelVersion">
        <!-- 使用 Sparkles 表示模型建议。 -->
        <Sparkles :size="16" aria-hidden="true" />
        <!-- 显示模型状态标签。 -->
        <span>模型链</span>
        <!-- 长版本字符串自动截断。 -->
        <code>{{ modelVersion }}</code>
      </div>
      <!-- 本地示例显示操作员；生产由 OIDC/BFF 注入。 -->
      <div class="operator-block">
        <!-- 用户图标。 -->
        <UserRound :size="17" aria-hidden="true" />
        <!-- 当前服务端认证操作员。 -->
        <span>{{ principal?.user_id ?? '身份加载中' }}</span>
      </div>
    </header>

    <!-- 页面主体由左侧队列导航和右侧任务工作区组成。 -->
    <div class="shell-body">
      <!-- 学科导航使用图标按钮与明确标签。 -->
      <aside class="subject-sidebar" aria-label="学科队列">
        <!-- 导航标题。 -->
        <p class="sidebar-label">补录队列</p>
        <!-- 遍历三个固定学科。 -->
        <button
          v-for="item in subjects"
          :key="item.code"
          type="button"
          class="subject-button"
          :class="{ active: subject === item.code }"
          :aria-current="subject === item.code ? 'page' : undefined"
          @click="changeSubject(item.code)"
        >
          <!-- 动态渲染对应学科图标。 -->
          <component :is="item.icon" :size="20" aria-hidden="true" />
          <!-- 展示学科名称与内容类型。 -->
          <span>
            <!-- 学科名称。 -->
            <strong>{{ item.label }}</strong>
            <!-- 学科简短描述。 -->
            <small>{{ item.caption }}</small>
          </span>
        </button>
        <!-- 分隔导航与系统状态。 -->
        <div class="sidebar-divider" aria-hidden="true"></div>
        <!-- 展示生产链路状态，不提供教程说明。 -->
        <div class="pipeline-state">
          <!-- 状态标题。 -->
          <p class="sidebar-label">当前链路</p>
          <!-- 历史模型节点。 -->
          <span><CheckCircle2 :size="15" aria-hidden="true" /> 历史模型</span>
          <!-- 混合 RAG 节点。 -->
          <span><Database :size="15" aria-hidden="true" /> Hybrid RAG</span>
          <!-- 人工确认节点。 -->
          <span><ShieldCheck :size="15" aria-hidden="true" /> 人工终审</span>
        </div>
      </aside>

      <!-- 右侧主工作区承载所有动态状态。 -->
      <main class="workspace">
        <!-- 页面操作栏展示队列状态和重新领取命令。 -->
        <div class="workspace-toolbar">
          <!-- 当前队列名称。 -->
          <div>
            <!-- 低层级导航提示。 -->
            <span class="eyebrow">{{ subjects.find((item) => item.code === subject)?.label }} · 填空题</span>
            <!-- 工作台标题使用紧凑字号。 -->
            <h1>标签复核</h1>
          </div>
          <!-- 重新领取按钮使用熟悉的刷新图标。 -->
          <button type="button" class="secondary-button" :disabled="loading" @click="loadNext">
            <!-- 加载时旋转图标但不改变按钮尺寸。 -->
            <RefreshCw :size="17" :class="{ spinning: loading }" aria-hidden="true" />
            <!-- 按钮命令文本。 -->
            重新领取
          </button>
        </div>

        <!-- API 错误在主工作区顶部展示。 -->
        <div v-if="errorMessage" class="message-banner error-message" role="alert">
          <!-- 错误图标。 -->
          <AlertTriangle :size="18" aria-hidden="true" />
          <!-- 错误详情包含可选请求 ID。 -->
          <span>{{ errorMessage }}</span>
        </div>

        <!-- 首次加载使用稳定高度骨架，避免布局跳动。 -->
        <section v-if="loading" class="loading-state" aria-live="polite">
          <!-- 旋转状态图标。 -->
          <RefreshCw :size="26" class="spinning" aria-hidden="true" />
          <!-- 加载状态文本。 -->
          <strong>正在领取任务</strong>
        </section>

        <!-- 提交成功后显示简洁确认和下一题命令。 -->
        <section v-else-if="successMessage && !bundle" class="empty-state" aria-live="polite">
          <!-- 成功状态图标。 -->
          <CheckCircle2 :size="34" aria-hidden="true" />
          <!-- 成功信息。 -->
          <h2>{{ successMessage }}</h2>
          <!-- 加载下一题命令。 -->
          <button type="button" class="primary-button" @click="loadNext">
            <!-- 下一题搜索图标。 -->
            <Search :size="17" aria-hidden="true" />
            <!-- 命令文本。 -->
            领取下一题
          </button>
        </section>

        <!-- 队列为空时展示明确空状态。 -->
        <section v-else-if="!bundle" class="empty-state">
          <!-- 空队列图标。 -->
          <Inbox :size="36" aria-hidden="true" />
          <!-- 空状态标题。 -->
          <h2>当前队列已清空</h2>
          <!-- 空状态上下文。 -->
          <p>暂时没有待复核的{{ subjects.find((item) => item.code === subject)?.label }}题目。</p>
          <!-- 再次检查队列。 -->
          <button type="button" class="secondary-button" @click="loadNext">
            <!-- 刷新图标。 -->
            <RefreshCw :size="17" aria-hidden="true" />
            <!-- 命令文本。 -->
            再次检查
          </button>
        </section>

        <!-- 有任务时展示题目、标签和 RAG 证据三部分。 -->
        <template v-else>
          <!-- 任务元数据条提供版本、租约和模型建议数量。 -->
          <div class="task-meta-strip">
            <!-- 任务编号和状态。 -->
            <span><Info :size="15" aria-hidden="true" /> 任务 #{{ bundle.task.id }} · v{{ bundle.task.version }}</span>
            <!-- 当前租约到期时间。 -->
            <span><Clock3 :size="15" aria-hidden="true" /> 租约至 {{ formatLease(bundle.task.claimed_until) }}</span>
            <!-- 模型默认标签数量。 -->
            <span><Sparkles :size="15" aria-hidden="true" /> 建议 {{ suggestedCount }} 项</span>
          </div>

          <!-- 降级警告逐条展示，人工仍可继续处理。 -->
          <div v-for="warning in bundle.prediction.warnings" :key="warning" class="message-banner warning-message">
            <!-- 警告图标。 -->
            <AlertTriangle :size="17" aria-hidden="true" />
            <!-- 工作流降级信息。 -->
            <span>{{ warning }}</span>
          </div>

          <!-- 主工作面由题目标签区和证据侧栏构成。 -->
          <div class="review-layout">
            <!-- 左侧是题目与标签选择区。 -->
            <section class="review-main">
              <!-- 题目详情使用无卡片分区。 -->
              <div class="question-section">
                <!-- 区块标题。 -->
                <div class="section-heading">
                  <!-- 标题文本。 -->
                  <div>
                    <!-- 小标题。 -->
                    <span class="eyebrow">题目内容</span>
                    <!-- 上游题目 ID。 -->
                    <h2>{{ bundle.task.question.external_id }}</h2>
                  </div>
                  <!-- 数据来源。 -->
                  <span class="source-chip">{{ bundle.task.question.source }}</span>
                </div>
                <!-- 题干原文。 -->
                <p class="question-stem">{{ bundle.task.question.stem }}</p>
                <!-- 标准答案使用强调带展示。 -->
                <div class="answer-band">
                  <!-- 答案字段名。 -->
                  <span>参考答案</span>
                  <!-- 答案原文。 -->
                  <strong>{{ bundle.task.question.reference_answer }}</strong>
                </div>
                <!-- 有解析时展示解析。 -->
                <div v-if="bundle.task.question.analysis" class="analysis-text">
                  <!-- 解析字段名。 -->
                  <span>题目解析</span>
                  <!-- 解析原文。 -->
                  <p>{{ bundle.task.question.analysis }}</p>
                </div>
              </div>

              <!-- 标签标题区展示人工选择变化。 -->
              <div class="labels-heading">
                <!-- 标签区标题。 -->
                <div>
                  <!-- 小标题。 -->
                  <span class="eyebrow">人工终审</span>
                  <!-- 主标题。 -->
                  <h2>标签选择</h2>
                </div>
                <!-- 选择和调整计数。 -->
                <div class="selection-summary">
                  <!-- 当前选择数量。 -->
                  <strong>{{ selectedCount }}</strong>
                  <!-- 计数标签。 -->
                  <span>已选</span>
                  <!-- 分隔符。 -->
                  <i aria-hidden="true"></i>
                  <!-- 人工修改数量。 -->
                  <strong>{{ changedCount }}</strong>
                  <!-- 计数标签。 -->
                  <span>调整</span>
                </div>
              </div>

              <!-- 每个标签都是可点击的稳定高度复选行。 -->
              <div class="label-list" role="group" aria-label="题目标签">
                <!-- 遍历当前学科标签字典。 -->
                <button
                  v-for="row in labelRows"
                  :key="row.definition.code"
                  type="button"
                  class="label-row"
                  :class="{ selected: row.selected, suggested: row.suggestion?.selected_by_default }"
                  :aria-pressed="row.selected"
                  @click="toggleTag(row.definition.code)"
                >
                  <!-- 自定义选中指示器保持固定尺寸。 -->
                  <span class="selection-box" aria-hidden="true">
                    <!-- 选中时展示勾号。 -->
                    <Check v-if="row.selected" :size="15" />
                  </span>
                  <!-- 标签文字、解释和信号源。 -->
                  <span class="label-copy">
                    <!-- 标签名称与状态标记。 -->
                    <span class="label-title-line">
                      <!-- 中文标签名称。 -->
                      <strong>{{ row.definition.name }}</strong>
                      <!-- 模型默认选中标记。 -->
                      <em v-if="row.suggestion?.selected_by_default" class="ai-badge"><Sparkles :size="12" /> AI 建议</em>
                      <!-- 高风险标记。 -->
                      <em v-if="row.definition.high_risk" class="risk-badge">重点复核</em>
                    </span>
                    <!-- 优先展示本次模型理由，否则展示字典定义。 -->
                    <span class="label-reason">{{ row.suggestion?.reason || row.definition.description }}</span>
                    <!-- 展示实际可用信号源分数。 -->
                    <span v-if="Object.keys(row.suggestion?.source_scores ?? {}).length" class="source-scores">
                      <!-- 遍历规则、历史模型、RAG 和 LLM 分数。 -->
                      <span v-for="(score, sourceName) in row.suggestion?.source_scores" :key="sourceName">
                        <!-- 信号源中文名和分数。 -->
                        {{ sourceLabel(String(sourceName)) }} <b>{{ percent(score) }}</b>
                      </span>
                    </span>
                  </span>
                  <!-- 标签融合置信度固定在右侧。 -->
                  <span class="confidence-block">
                    <!-- 百分比文本。 -->
                    <strong>{{ percent(row.suggestion?.confidence) }}</strong>
                    <!-- 原生进度条提供可访问语义。 -->
                    <progress :value="row.suggestion?.confidence ?? 0" max="1"></progress>
                  </span>
                </button>
              </div>
            </section>

            <!-- 右侧展示 RAG 证据及其人工标签。 -->
            <aside class="evidence-panel" aria-label="相似题证据">
              <!-- 证据标题与数量。 -->
              <div class="evidence-heading">
                <!-- 标题图标。 -->
                <Database :size="18" aria-hidden="true" />
                <!-- 标题文本。 -->
                <h2>相似题证据</h2>
                <!-- 证据数量。 -->
                <span>{{ bundle.prediction.evidence.length }}</span>
              </div>
              <!-- 有证据时按融合排名展示。 -->
              <div v-if="bundle.prediction.evidence.length" class="evidence-list">
                <!-- 遍历 RAG 证据。 -->
                <article v-for="evidence in bundle.prediction.evidence" :key="evidence.evidence_id" class="evidence-item">
                  <!-- 证据 ID、来源和相似度。 -->
                  <header>
                    <!-- 证据 ID。 -->
                    <code>{{ evidence.evidence_id }}</code>
                    <!-- 相似度。 -->
                    <strong>{{ percent(evidence.score) }}</strong>
                  </header>
                  <!-- 历史题题干。 -->
                  <p>{{ evidence.stem }}</p>
                  <!-- 历史题人工答案。 -->
                  <div class="evidence-answer">
                    <!-- 字段名。 -->
                    <span>人工答案</span>
                    <!-- 答案内容。 -->
                    <strong>{{ evidence.reference_answer }}</strong>
                  </div>
                  <!-- 历史人工标签。 -->
                  <div class="evidence-tags">
                    <!-- 遍历证据标签。 -->
                    <span v-for="code in evidence.human_labels" :key="code">{{ tagName(code) }}</span>
                  </div>
                  <!-- 证据来源和许可证。 -->
                  <footer>{{ evidence.source }} · {{ evidence.license_name }}</footer>
                </article>
              </div>
              <!-- 无证据时明确显示降级状态。 -->
              <div v-else class="evidence-empty">
                <!-- 空证据图标。 -->
                <Search :size="24" aria-hidden="true" />
                <!-- 空证据状态。 -->
                <span>本次未召回相似题</span>
              </div>
            </aside>
          </div>

          <!-- 底部操作条保持可见，减少长标签列表的滚动成本。 -->
          <footer class="submission-bar">
            <!-- 人工备注输入区。 -->
            <label class="note-field">
              <!-- 输入标签。 -->
              <span>复核备注</span>
              <!-- 备注内容。 -->
              <textarea v-model="note" maxlength="2000" rows="2" placeholder="可选：记录模型误报、漏标或特殊判定依据"></textarea>
            </label>
            <!-- 模型刷新和人工提交命令。 -->
            <div class="submission-actions">
              <!-- 强制重跑模型链。 -->
              <button type="button" class="secondary-button" :disabled="refreshing || submitting" @click="rerunPrediction">
                <!-- 刷新状态图标。 -->
                <RefreshCw :size="17" :class="{ spinning: refreshing }" aria-hidden="true" />
                <!-- 命令文本。 -->
                更新建议
              </button>
              <!-- 提交人工最终标签。 -->
              <button type="button" class="primary-button" :disabled="submitting || refreshing" @click="submitReview">
                <!-- 提交图标。 -->
                <Send :size="17" aria-hidden="true" />
                <!-- 动态提交状态。 -->
                {{ submitting ? '提交中' : '确认并提交' }}
              </button>
            </div>
          </footer>
        </template>
      </main>
    </div>
  </div>
</template>
