// startTransition 将较大的任务视图更新标记为非紧急渲染。
import { startTransition, useEffect, useEffectEvent, useReducer, useRef } from 'react'
// 导入类型化 API 和错误类型。
import {
  ApiRequestError,
  claimNextTask,
  fetchCurrentUser,
  fetchTaxonomy,
  refreshPrediction,
  submitAnnotation,
} from '../api'
// 导入页面需要的业务类型。
import type {
  LabelSuggestion,
  PrincipalResponse,
  Subject,
  TagDefinition,
  TaskBundleResponse,
} from '../types'

// LabelRow 合并服务端标签定义、模型建议和人工选择状态。
export interface LabelRow {
  // 受控标签定义。
  definition: TagDefinition
  // 当前模型建议；未给分时为空。
  suggestion?: LabelSuggestion
  // 当前人工是否选中。
  selected: boolean
}

// WorkbenchState 将多个相关状态放入一个显式状态机。
interface WorkbenchState {
  // 当前学科队列。
  subject: Subject
  // 服务端认证主体。
  principal: PrincipalResponse | null
  // 当前学科标签字典。
  taxonomy: TagDefinition[]
  // 当前题目和预测。
  bundle: TaskBundleResponse | null
  // 人工当前选择标签。
  selectedTags: string[]
  // 人工复核备注。
  note: string
  // 是否正在领取任务。
  loading: boolean
  // 是否正在重跑模型。
  refreshing: boolean
  // 是否正在提交。
  submitting: boolean
  // 当前错误消息。
  errorMessage: string
  // 当前成功消息。
  successMessage: string
}

// WorkbenchAction 列出页面允许发生的全部状态变化。
type WorkbenchAction =
  // 开始加载一个学科队列。
  | { type: 'loadStarted'; subject: Subject }
  // 标签字典、身份和任务加载成功。
  | { type: 'loadSucceeded'; taxonomy: TagDefinition[]; principal: PrincipalResponse; bundle: TaskBundleResponse | null }
  // 加载失败。
  | { type: 'loadFailed'; message: string }
  // 人工切换一个标签。
  | { type: 'tagToggled'; code: string }
  // 人工更新备注。
  | { type: 'noteChanged'; note: string }
  // 开始刷新模型建议。
  | { type: 'refreshStarted' }
  // 模型建议刷新成功。
  | { type: 'refreshSucceeded'; bundle: TaskBundleResponse }
  // 模型建议刷新失败。
  | { type: 'refreshFailed'; message: string }
  // 开始提交人工结果。
  | { type: 'submitStarted' }
  // 人工提交成功。
  | { type: 'submitSucceeded'; message: string }
  // 人工提交失败。
  | { type: 'submitFailed'; message: string }

// initialState 是 Reducer 的唯一初始状态。
const initialState: WorkbenchState = {
  // 默认进入语文队列。
  subject: 'chinese',
  // 首次请求前没有可信主体。
  principal: null,
  // 首次请求前没有标签字典。
  taxonomy: [],
  // 首次请求前没有任务。
  bundle: null,
  // 首次请求前没有人工选择。
  selectedTags: [],
  // 首次请求前备注为空。
  note: '',
  // 挂载 Effect 会立即进入加载态。
  loading: false,
  // 默认没有刷新模型。
  refreshing: false,
  // 默认没有提交。
  submitting: false,
  // 默认没有错误。
  errorMessage: '',
  // 默认没有成功提示。
  successMessage: '',
}

// defaultTags 从模型快照提取页面初始勾选项。
function defaultTags(bundle: TaskBundleResponse | null): string[] {
  // 仅选择后端明确标记 selected_by_default 的标签。
  return (bundle?.prediction.suggestions ?? [])
    // 过滤默认建议。
    .filter((item) => item.selected_by_default)
    // 只保存稳定编码。
    .map((item) => item.code)
}

// reducer 是纯函数：相同 state/action 永远得到相同新状态。
function reducer(state: WorkbenchState, action: WorkbenchAction): WorkbenchState {
  // 根据动作类型执行唯一状态转换。
  switch (action.type) {
    case 'loadStarted':
      // 切学科时立即清空旧任务，避免展示错误学科内容。
      return {
        ...state,
        subject: action.subject,
        bundle: null,
        selectedTags: [],
        note: '',
        loading: true,
        errorMessage: '',
        successMessage: '',
      }
    case 'loadSucceeded':
      // 一次性提交同一请求批次的标签、身份和任务，避免中间不一致 UI。
      return {
        ...state,
        taxonomy: action.taxonomy,
        principal: action.principal,
        bundle: action.bundle,
        selectedTags: defaultTags(action.bundle),
        note: '',
        loading: false,
      }
    case 'loadFailed':
      // 保留当前学科，同时结束加载并显示可排查错误。
      return { ...state, loading: false, errorMessage: action.message }
    case 'tagToggled':
      // React 状态必须创建新数组，不能原地 push/splice。
      return {
        ...state,
        selectedTags: state.selectedTags.includes(action.code)
          ? state.selectedTags.filter((item) => item !== action.code)
          : [...state.selectedTags, action.code],
      }
    case 'noteChanged':
      // 受控 textarea 的值始终来自 React state。
      return { ...state, note: action.note }
    case 'refreshStarted':
      // 刷新模型期间禁用提交并清空旧错误。
      return { ...state, refreshing: true, errorMessage: '' }
    case 'refreshSucceeded':
      // 新预测到达后重新应用模型默认标签并清空旧备注。
      return {
        ...state,
        bundle: action.bundle,
        selectedTags: defaultTags(action.bundle),
        note: '',
        refreshing: false,
      }
    case 'refreshFailed':
      // 保留当前人工选择，只结束刷新并展示错误。
      return { ...state, refreshing: false, errorMessage: action.message }
    case 'submitStarted':
      // 提交期间禁用刷新和重复点击。
      return { ...state, submitting: true, errorMessage: '' }
    case 'submitSucceeded':
      // 提交后清空任务并保留成功确认，等待人工主动领取下一题。
      return {
        ...state,
        bundle: null,
        selectedTags: [],
        note: '',
        submitting: false,
        successMessage: action.message,
      }
    case 'submitFailed':
      // 提交失败时保留全部人工输入，便于重试。
      return { ...state, submitting: false, errorMessage: action.message }
  }
}

// describeError 将 unknown 安全转换为面向操作员的文本。
function describeError(error: unknown): string {
  // API 错误附加 request ID，方便联系后端排查。
  if (error instanceof ApiRequestError) {
    // 仅在存在请求 ID 时追加。
    return `${error.message}${error.requestId ? ` · 请求 ID ${error.requestId}` : ''}`
  }
  // 普通 JavaScript Error 返回 message。
  if (error instanceof Error) {
    // 返回可读消息。
    return error.message
  }
  // 非 Error 值使用保守回退。
  return '发生未知错误，请稍后重试。'
}

// useLabelingWorkbench 是 React 版页面状态与用例入口。
export function useLabelingWorkbench() {
  // useReducer 适合状态字段相关、动作边界明确的工作台。
  const [state, dispatch] = useReducer(reducer, initialState)
  // AbortController 属于可变运行句柄，变化不应触发渲染，因此使用 useRef。
  const activeControllerRef = useRef<AbortController | null>(null)
  // 幂等键在不确定失败后必须复用，也不参与 UI 渲染，因此使用 useRef。
  const submitKeyRef = useRef<string | null>(null)

  // loadSubject 领取指定学科任务并加载标签和可信身份。
  async function loadSubject(targetSubject: Subject): Promise<void> {
    // 中止上一轮未完成请求，防止旧响应覆盖新学科。
    activeControllerRef.current?.abort()
    // 创建本轮请求控制器。
    const controller = new AbortController()
    // 保存句柄供下次请求和卸载清理。
    activeControllerRef.current = controller
    // 新任务必须使用新的提交幂等键。
    submitKeyRef.current = null
    // 先同步展示加载状态，让点击立即有反馈。
    dispatch({ type: 'loadStarted', subject: targetSubject })
    try {
      // 三个互不依赖的请求并行执行，减少页面等待时间。
      const [taxonomyResult, taskResult, principalResult] = await Promise.all([
        // 加载当前学科标签字典。
        fetchTaxonomy(targetSubject, controller.signal),
        // 原子领取当前学科任务。
        claimNextTask(targetSubject, controller.signal),
        // 获取服务端验证后的操作员身份。
        fetchCurrentUser(controller.signal),
      ])
      // 任务列表更新不是输入回显等紧急更新，使用 transition 保持交互响应。
      startTransition(() => {
        // 原子应用同一批请求结果。
        dispatch({
          type: 'loadSucceeded',
          taxonomy: taxonomyResult.tags,
          principal: principalResult,
          bundle: taskResult,
        })
      })
    } catch (error) {
      // 主动取消是预期生命周期事件，不显示错误。
      if (error instanceof DOMException && error.name === 'AbortError') {
        // 结束当前过期任务。
        return
      }
      // 将异常转换为页面状态。
      dispatch({ type: 'loadFailed', message: describeError(error) })
    }
  }

  // useEffectEvent 创建只供 Effect 调用、始终读取最新函数实现的事件。
  const loadInitialTask = useEffectEvent(() => {
    // 首次挂载固定加载默认语文学科，避免 Effect 依赖业务状态。
    void loadSubject('chinese')
  })

  // useEffect 负责组件与外部系统的生命周期同步。
  useEffect(() => {
    // 挂载时领取第一条任务。
    loadInitialTask()
    // 清理函数在卸载和开发 StrictMode 检查时执行。
    return () => {
      // 中止未完成网络请求，避免卸载后更新状态。
      activeControllerRef.current?.abort()
    }
  }, [])

  // suggestionByCode 是轻量派生值，直接计算即可，不滥用 useMemo。
  const suggestionByCode = Object.fromEntries(
    // 将建议数组转换为按编码查找的对象。
    (state.bundle?.prediction.suggestions ?? []).map((item) => [item.code, item]),
  ) as Record<string, LabelSuggestion>

  // labelRows 合并标签字典、模型建议和人工选择。
  const labelRows: LabelRow[] = state.taxonomy.map((definition) => ({
    // 保留服务端标签定义。
    definition,
    // 关联同编码模型建议。
    suggestion: suggestionByCode[definition.code],
    // 判断人工当前选择。
    selected: state.selectedTags.includes(definition.code),
  }))

  // suggestedCount 统计模型默认勾选数量。
  const suggestedCount = (state.bundle?.prediction.suggestions ?? [])
    // 仅保留默认建议。
    .filter((item) => item.selected_by_default).length

  // defaults 用于计算人工相对模型的修改数量。
  const defaults = new Set(
    // 从建议数组提取默认标签。
    (state.bundle?.prediction.suggestions ?? [])
      // 过滤默认选中项。
      .filter((item) => item.selected_by_default)
      // 映射为编码。
      .map((item) => item.code),
  )
  // selected 是当前人工标签集合。
  const selected = new Set(state.selectedTags)
  // changedCount 统计两个集合的对称差。
  const changedCount = [...new Set([...defaults, ...selected])]
    // 只保留仅属于一侧的标签。
    .filter((code) => defaults.has(code) !== selected.has(code)).length

  // loadNext 重新领取当前学科任务。
  function loadNext(): void {
    // 事件处理器无需 useCallback；子组件没有依赖引用稳定性的 memo。
    void loadSubject(state.subject)
  }

  // changeSubject 切换学科并立即加载对应队列。
  function changeSubject(nextSubject: Subject): void {
    // 相同学科不重复领取。
    if (nextSubject === state.subject) {
      // 保持当前任务不变。
      return
    }
    // 使用目标学科发请求，避免等待异步 state 更新。
    void loadSubject(nextSubject)
  }

  // toggleTag 将标签切换动作交给纯 Reducer。
  function toggleTag(code: string): void {
    // Reducer 创建新数组，触发 React 比较和重新渲染。
    dispatch({ type: 'tagToggled', code })
  }

  // changeNote 处理受控 textarea 输入。
  function changeNote(note: string): void {
    // 将最新文本写入状态机。
    dispatch({ type: 'noteChanged', note })
  }

  // rerunPrediction 为当前题目创建新模型运行。
  async function rerunPrediction(): Promise<void> {
    // 没有当前任务时不发请求。
    if (!state.bundle) {
      // 结束空操作。
      return
    }
    // 保存当前任务 ID，避免 await 后读取变化中的 state。
    const questionId = state.bundle.task.id
    // 显示刷新状态。
    dispatch({ type: 'refreshStarted' })
    try {
      // 调用强制预测接口。
      const nextBundle = await refreshPrediction(questionId)
      // 新模型建议属于非紧急列表更新。
      startTransition(() => {
        // 应用预测并重置默认标签。
        dispatch({ type: 'refreshSucceeded', bundle: nextBundle })
      })
      // 新预测需要新的提交幂等键。
      submitKeyRef.current = null
    } catch (error) {
      // 保留当前人工状态并显示错误。
      dispatch({ type: 'refreshFailed', message: describeError(error) })
    }
  }

  // submitReview 提交人工最终标签。
  async function submitReview(): Promise<void> {
    // 预测尚未准备时禁止提交。
    if (!state.bundle?.prediction_id) {
      // 使用统一失败动作展示错误。
      dispatch({ type: 'submitFailed', message: '预测尚未完成，暂时不能提交。' })
      // 结束提交。
      return
    }
    // 捕获当前快照，避免网络等待期间 state 变化。
    const currentBundle = state.bundle
    // 首次点击生成幂等键；不确定失败后再次点击继续复用同一个键。
    submitKeyRef.current ??= crypto.randomUUID()
    // 进入提交状态。
    dispatch({ type: 'submitStarted' })
    try {
      // 向后端发送人工最终标签和一致性保护字段。
      const result = await submitAnnotation(currentBundle.task.id, {
        // 关联人工看到的预测运行。
        prediction_id: currentBundle.prediction_id,
        // 复制数组，避免异步调用持有可变引用。
        selected_tags: [...state.selectedTags],
        // 清理备注首尾空白。
        note: state.note.trim(),
        // 复用当前提交动作的幂等键。
        idempotency_key: submitKeyRef.current,
        // 发送页面读取时的乐观锁版本。
        expected_version: currentBundle.task.version,
      })
      // 明确提交成功后才清除幂等键。
      submitKeyRef.current = null
      // 使用非紧急更新切换成功页面。
      startTransition(() => {
        // 展示记录 ID 和人工调整数量。
        dispatch({
          type: 'submitSucceeded',
          message: `已提交 #${result.annotation_id} · 调整 ${result.added_tags.length + result.removed_tags.length} 项`,
        })
      })
    } catch (error) {
      // 保留幂等键、标签和备注，用户可安全重试。
      dispatch({ type: 'submitFailed', message: describeError(error) })
    }
  }

  // 返回页面读取状态、派生值和命令。
  return {
    // 展开状态方便函数组件按字段读取。
    ...state,
    // 返回合并后的标签行。
    labelRows,
    // 返回模型默认标签数。
    suggestedCount,
    // 返回人工修改数。
    changedCount,
    // 返回重新领取命令。
    loadNext,
    // 返回学科切换命令。
    changeSubject,
    // 返回标签切换命令。
    toggleTag,
    // 返回备注更新命令。
    changeNote,
    // 返回刷新模型命令。
    rerunPrediction,
    // 返回人工提交命令。
    submitReview,
  }
}
