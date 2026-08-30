// 导入 Vue Composition API 响应式原语。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
// 导入类型化 API 客户端。
import { ApiRequestError, claimNextTask, fetchCurrentUser, fetchTaxonomy, refreshPrediction, submitAnnotation } from '../api'
// 导入业务契约类型。
import type { LabelSuggestion, PrincipalResponse, Subject, TagDefinition, TaskBundleResponse } from '../types'

// 定义页面渲染的一行标签数据。
export interface LabelRow {
  // 保存受控标签定义。
  definition: TagDefinition
  // 保存当前预测建议；尚未预测时为空。
  suggestion?: LabelSuggestion
  // 表示人工当前是否勾选。
  selected: boolean
}

// 将异常转换为面向操作员且可排查的字符串。
function describeError(error: unknown): string {
  // API 错误附带机器码和请求 ID。
  if (error instanceof ApiRequestError) {
    // 组合错误信息和可选排查 ID。
    return `${error.message}${error.requestId ? ` · 请求 ID ${error.requestId}` : ''}`
  }
  // 普通 Error 返回其消息。
  if (error instanceof Error) {
    // 返回 JavaScript 错误文本。
    return error.message
  }
  // 未知异常使用保守提示。
  return '发生未知错误，请稍后重试。'
}

// 封装补录页面状态、计算属性和用户动作。
export function useLabelingWorkbench() {
  // 保存当前队列学科。
  const subject = ref<Subject>('chinese')
  // 保存服务端认证后的当前操作员。
  const principal = ref<PrincipalResponse | null>(null)
  // 保存标签字典。
  const taxonomy = ref<TagDefinition[]>([])
  // 保存当前题目和预测。
  const bundle = ref<TaskBundleResponse | null>(null)
  // 保存人工当前选择的标签编码。
  const selectedTags = ref<string[]>([])
  // 保存人工备注。
  const note = ref('')
  // 表示正在领取任务。
  const loading = ref(false)
  // 表示正在刷新模型建议。
  const refreshing = ref(false)
  // 表示正在提交人工结果。
  const submitting = ref(false)
  // 保存阻塞当前操作的错误。
  const errorMessage = ref('')
  // 保存成功反馈。
  const successMessage = ref('')
  // 保存当前可取消网络请求。
  let activeController: AbortController | null = null
  // 保存当前人工提交动作的幂等键；不确定失败后重试必须复用。
  let submitIdempotencyKey: string | null = null

  // 建立预测标签编码到建议对象的映射。
  const suggestionByCode = computed<Record<string, LabelSuggestion>>(() => {
    // 从当前预测生成对象映射。
    return Object.fromEntries((bundle.value?.prediction.suggestions ?? []).map((item) => [item.code, item]))
  })

  // 合并标签字典、预测建议与人工选择状态。
  const labelRows = computed<LabelRow[]>(() => {
    // 为每个受控标签构造页面行。
    return taxonomy.value.map((definition) => ({
      // 保留标签定义。
      definition,
      // 关联同编码模型建议。
      suggestion: suggestionByCode.value[definition.code],
      // 判断人工当前是否选中。
      selected: selectedTags.value.includes(definition.code),
    }))
  })

  // 统计模型默认勾选标签数量。
  const suggestedCount = computed(() => {
    // 过滤预测中的默认选择项。
    return (bundle.value?.prediction.suggestions ?? []).filter((item) => item.selected_by_default).length
  })

  // 统计人工相对模型修改的标签数量。
  const changedCount = computed(() => {
    // 创建模型默认标签集合。
    const defaults = new Set((bundle.value?.prediction.suggestions ?? []).filter((item) => item.selected_by_default).map((item) => item.code))
    // 创建人工当前标签集合。
    const selected = new Set(selectedTags.value)
    // 统计只出现在任一集合中的编码。
    return [...new Set([...defaults, ...selected])].filter((code) => defaults.has(code) !== selected.has(code)).length
  })

  // 将新预测同步为页面默认选择。
  function applyBundle(nextBundle: TaskBundleResponse | null): void {
    // 新任务或新预测必须使用新的提交幂等键。
    submitIdempotencyKey = null
    // 保存当前任务。
    bundle.value = nextBundle
    // 只选择后端明确指定的默认标签。
    selectedTags.value = (nextBundle?.prediction.suggestions ?? []).filter((item) => item.selected_by_default).map((item) => item.code)
    // 新任务清空上一题备注。
    note.value = ''
  }

  // 领取当前学科下一题并加载标签字典。
  async function loadNext(): Promise<void> {
    // 取消上一轮尚未完成的领取请求。
    activeController?.abort()
    // 为本轮请求创建取消控制器。
    activeController = new AbortController()
    // 进入加载状态。
    loading.value = true
    // 清空旧错误。
    errorMessage.value = ''
    // 清空旧成功提示。
    successMessage.value = ''
    try {
      // 并行加载静态标签字典与下一条任务。
      const [taxonomyResult, taskResult, principalResult] = await Promise.all([
        // 获取当前学科可选标签。
        fetchTaxonomy(subject.value, activeController.signal),
        // 原子领取下一题。
        claimNextTask(subject.value, activeController.signal),
        // 首次加载读取服务端认证主体，后续切学科复用当前值。
        principal.value ? Promise.resolve(principal.value) : fetchCurrentUser(activeController.signal),
      ])
      // 保存标签字典。
      taxonomy.value = taxonomyResult.tags
      // 保存服务端确认的操作员身份。
      principal.value = principalResult
      // 应用模型默认选中状态。
      applyBundle(taskResult)
    } catch (error) {
      // 用户切换学科导致的主动取消不显示错误。
      if (error instanceof DOMException && error.name === 'AbortError') {
        // 提前结束当前过期请求。
        return
      }
      // 保存可展示错误。
      errorMessage.value = describeError(error)
    } finally {
      // 结束加载状态。
      loading.value = false
    }
  }

  // 切换学科并领取对应队列任务。
  async function changeSubject(nextSubject: Subject): Promise<void> {
    // 相同学科不重复请求。
    if (nextSubject === subject.value) {
      // 保持当前页面状态。
      return
    }
    // 更新当前学科。
    subject.value = nextSubject
    // 加载新学科任务。
    await loadNext()
  }

  // 切换某个标签的人工选择状态。
  function toggleTag(code: string): void {
    // 已选中时移除标签。
    if (selectedTags.value.includes(code)) {
      // 创建新数组触发 Vue 更新。
      selectedTags.value = selectedTags.value.filter((item) => item !== code)
      // 结束取消路径。
      return
    }
    // 未选中时追加标签。
    selectedTags.value = [...selectedTags.value, code]
  }

  // 重新运行当前题目的模型链。
  async function rerunPrediction(): Promise<void> {
    // 没有当前题目时不发请求。
    if (!bundle.value) {
      // 提前返回。
      return
    }
    // 进入刷新状态。
    refreshing.value = true
    // 清空旧错误。
    errorMessage.value = ''
    try {
      // 强制创建新预测运行。
      const nextBundle = await refreshPrediction(bundle.value.task.id)
      // 应用新模型建议。
      applyBundle(nextBundle)
    } catch (error) {
      // 保存错误供页面展示。
      errorMessage.value = describeError(error)
    } finally {
      // 结束刷新状态。
      refreshing.value = false
    }
  }

  // 提交人工最终标签并保留成功状态。
  async function submitReview(): Promise<void> {
    // 没有任务或预测 ID 时禁止提交。
    if (!bundle.value || !bundle.value.prediction_id) {
      // 提示预测尚未就绪。
      errorMessage.value = '预测尚未完成，暂时不能提交。'
      // 提前结束。
      return
    }
    // 保存提交时的任务快照，防止响应期间页面变化。
    const currentBundle = bundle.value
    // 首次点击生成幂等键；网络失败后再次点击继续复用同一个值。
    submitIdempotencyKey ??= crypto.randomUUID()
    // 进入提交状态。
    submitting.value = true
    // 清空旧错误。
    errorMessage.value = ''
    try {
      // 提交模型版本、人工标签、幂等键和乐观锁版本。
      const result = await submitAnnotation(currentBundle.task.id, {
        // 指明本次人工看到的预测。
        prediction_id: currentBundle.prediction_id,
        // 复制人工标签数组。
        selected_tags: [...selectedTags.value],
        // 保存人工备注。
        note: note.value.trim(),
        // 使用当前人工提交动作的稳定幂等键。
        idempotency_key: submitIdempotencyKey,
        // 防止过期页面覆盖他人结果。
        expected_version: currentBundle.task.version,
      })
      // 只有服务端明确提交成功后才清除幂等键。
      submitIdempotencyKey = null
      // 显示提交记录 ID 和模型修改数量。
      successMessage.value = `已提交 #${result.annotation_id} · 调整 ${result.added_tags.length + result.removed_tags.length} 项`
      // 清空任务但保留成功确认。
      bundle.value = null
      // 清空人工标签。
      selectedTags.value = []
      // 清空备注。
      note.value = ''
    } catch (error) {
      // 版本冲突和权限错误都显示后端提供的信息。
      errorMessage.value = describeError(error)
    } finally {
      // 结束提交状态。
      submitting.value = false
    }
  }

  // 组件挂载后自动领取语文队列第一题。
  onMounted(() => {
    // 异步调用由页面状态自行显示错误。
    void loadNext()
  })

  // 组件卸载时取消未完成请求。
  onBeforeUnmount(() => {
    // 终止当前 Fetch 请求。
    activeController?.abort()
  })

  // 返回模板需要的状态、计算属性与动作。
  return {
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
  }
}
