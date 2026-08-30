# React 19 对应用法学习指南

本指南不从计数器示例开始，而是沿着真实的题目标注流程解释 React。建议一边阅读，一边打开 `frontend-react/src` 中对应文件。

## 1. 先建立后端开发者的心智模型

可以把 React 页面理解成一个反复执行的纯视图函数：

$$
UI = render(state, props)
$$

状态改变后，React 再次调用函数组件，得到新的 JSX 树，并把必要差异提交到真实 DOM。组件不是长生命周期 Java Bean；函数体中的普通局部变量会在下一次渲染重新创建。

| React 概念 | 后端类比 | 关键区别 |
| --- | --- | --- |
| Props | 只读方法参数或 DTO | 父组件每次渲染都可能传入新快照 |
| State | 聚合当前状态 | 必须通过 React 更新入口改变 |
| Reducer | 领域状态转换函数 | 必须纯净，不能调用网络或修改旧状态 |
| Effect | 与外部系统同步的生命周期任务 | 不是普通业务事件处理器 |
| Ref | 不参与渲染的可变实例字段 | 修改 `.current` 不会重渲染 |
| Custom Hook | 可组合应用服务 | 可组合状态和生命周期，不是单例容器 |

## 2. 应用如何启动

入口 `frontend-react/src/main.tsx` 完成三件事：

```tsx
const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('React root element #root was not found')
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

1. 从 `index.html` 找到唯一根节点。
2. 根节点缺失时快速失败，避免静默白屏。
3. 在开发环境用 `StrictMode` 检查不纯渲染和 Effect 清理。

开发模式下 Strict Mode 会额外执行一次 Effect 的“建立 -> 清理 -> 建立”检查。页面必须能取消第一次请求，不能假定挂载逻辑只运行一次。

## 3. 函数组件与 TSX

`App.tsx` 是普通 TypeScript 函数：

```tsx
function App() {
  const workbench = useLabelingWorkbench()
  return <main className="workspace">...</main>
}
```

TSX 允许在 TypeScript 中直接描述 UI：

- HTML 的 `class` 在 JSX 中写作 `className`。
- JavaScript 表达式放入 `{}`。
- 组件名必须大写，如 `<EvidencePanel />`。
- 自定义组件的属性由 TypeScript Props 接口检查。
- JSX 最终是 JavaScript，不是浏览器原生模板语法。

渲染期间不要发送请求、修改状态或生成随机业务值。React 可能为了并发调度或开发检查多次调用组件函数。

## 4. 组件拆分：按职责，不按标签数量

React 版拆成五个子组件：

| 组件 | 输入 | 输出事件 | 是否拥有业务状态 |
| --- | --- | --- | --- |
| `SubjectSidebar` | 当前学科 | `onSubjectChange` | 否 |
| `QuestionDetail` | 任务快照 | 无 | 否 |
| `LabelReviewList` | 标签行和统计 | `onToggle` | 否 |
| `EvidencePanel` | RAG 证据和标签字典 | 无 | 否 |
| `SubmissionBar` | 备注和请求状态 | `onNoteChange/onRefresh/onSubmit` | 否 |

业务状态统一属于 `useLabelingWorkbench`。这样子组件可用固定 Props 单独测试，也不会出现多个组件各自保存一份已选标签。

不要为了“组件化”给每个 `<div>` 建文件。一个组件应当至少拥有清晰职责、稳定输入输出、复用价值或独立测试价值之一。

## 5. Props 向下，事件向上

父组件把只读数据和回调传给子组件：

```tsx
<LabelReviewList
  rows={workbench.labelRows}
  selectedCount={workbench.selectedTags.length}
  changedCount={workbench.changedCount}
  onToggle={workbench.toggleTag}
/>
```

子组件只上报“哪个标签被点击”：

```tsx
interface LabelReviewListProps {
  rows: LabelRow[]
  onToggle: (code: string) => void
}

<button onClick={() => onToggle(row.definition.code)} />
```

子组件不能修改 Props。真正的状态变化由父 Hook 的 Reducer 完成，这和“Controller 接收 Command，Aggregate 决定状态”很相似。

## 6. `useReducer`：显式页面状态机

工作台有加载、刷新、提交、错误、成功和人工编辑等相关状态。与多个独立 `useState` 相比，Reducer 更容易表达合法转换：

```ts
type WorkbenchAction =
  | { type: 'loadStarted'; subject: Subject }
  | { type: 'loadSucceeded'; taxonomy: TagDefinition[]; principal: PrincipalResponse; bundle: TaskBundleResponse | null }
  | { type: 'tagToggled'; code: string }
  | { type: 'submitFailed'; message: string }
```

Reducer 只根据旧状态和 Action 返回新状态：

```ts
case 'tagToggled':
  return {
    ...state,
    selectedTags: state.selectedTags.includes(action.code)
      ? state.selectedTags.filter((item) => item !== action.code)
      : [...state.selectedTags, action.code],
  }
```

它不能执行以下操作：

- 调用 Fetch。
- 读取当前时间或生成 UUID。
- 修改 `state.selectedTags` 原数组。
- 操作 DOM。

纯 Reducer 可以独立测试，也让异步流程的开始、成功和失败状态一一对应。

## 7. 为什么 React 强调不可变更新

React 把每次渲染的 State 当成快照。下面的写法修改旧数组，既破坏历史快照，也可能无法触发预期更新：

```ts
// 错误示例
state.selectedTags.push(code)
return state
```

正确做法是创建新对象和新数组：

```ts
return {
  ...state,
  selectedTags: [...state.selectedTags, code],
}
```

可以把对象引用理解成版本号：新引用告诉 React “这个分支产生了新快照”。

## 8. Custom Hook：组合一个页面用例

`useLabelingWorkbench()` 封装：

- Reducer 状态。
- API 调用。
- 请求取消。
- 默认标签和人工差异等派生值。
- 领取、切学科、刷新、提交命令。

Hook 必须遵守两条基本规则：

1. 只在函数组件或其他 Hook 顶层调用 Hook。
2. 不在条件、循环、事件回调内部调用 Hook。

这是因为 React 按调用顺序关联每个 Hook 槽位，而不是按变量名查找。

Custom Hook 不是全局状态。每调用一次 `useLabelingWorkbench()`，都会创建一套独立状态；当前页面只调用一次，所以无需引入 Redux 或 Zustand。

## 9. 派生状态：能算出来就不要再保存

标签行由标签字典、模型建议和人工选择合并：

```ts
const labelRows = state.taxonomy.map((definition) => ({
  definition,
  suggestion: suggestionByCode[definition.code],
  selected: state.selectedTags.includes(definition.code),
}))
```

`labelRows`、`suggestedCount` 和 `changedCount` 都没有单独存入 State。否则每次任务或选择变化时都要手工同步多份数据，很容易出现旧统计。

当前数组很小，直接计算比 `useMemo` 更清楚。`useMemo` 是性能优化，不是语义保证；只有昂贵计算或引用稳定确实影响下游时才应加入，并用性能测量证明。

## 10. `useEffect`：只同步外部系统

工作台挂载时需要领取第一题，因此使用 Effect：

```tsx
useEffect(() => {
  loadInitialTask()
  return () => {
    activeControllerRef.current?.abort()
  }
}, [])
```

Effect 的职责是把 React 生命周期与网络请求同步。按钮点击已经是明确事件，不需要先改一个布尔 State，再让 Effect 观察该 State 发请求。

常见误区：

- 用 Effect 计算可以在渲染期直接得到的值。
- 忽略依赖数组导致读取旧闭包。
- 用空依赖数组掩盖实际依赖。
- 没有清理订阅、Timer 或请求。

## 11. `useEffectEvent`：让 Effect 读取最新逻辑

React 19.2 的 `useEffectEvent` 把“由 Effect 触发、但不应成为 Effect 依赖”的逻辑声明为 Effect Event：

```tsx
const loadInitialTask = useEffectEvent(() => {
  void loadSubject('chinese')
})
```

它能读取最新函数实现，又不会因为函数引用变化让挂载 Effect 重跑。它只能从 Effect 内调用，不是代替普通点击事件或规避依赖检查的通用工具。

## 12. `useRef`：保存不参与渲染的可变值

当前 Hook 有两个 Ref：

```ts
const activeControllerRef = useRef<AbortController | null>(null)
const submitKeyRef = useRef<string | null>(null)
```

它们不属于可见页面状态：

- `AbortController` 是当前网络操作句柄。
- 幂等键是同一次人工提交动作的协议身份。

修改 `ref.current` 不会触发渲染。如果页面需要立即展示某个值，就不应只把它放进 Ref。

幂等键必须在第一次提交时创建，明确成功后清空，不确定失败后继续复用。若每次重试都生成新 UUID，服务端无法识别“同一操作重放”。

## 13. 异步竞争与 `AbortController`

用户可能快速点击“语文 -> 数学 -> 英语”。如果三个请求都继续运行，最慢返回的旧请求可能覆盖当前学科。

```ts
activeControllerRef.current?.abort()
const controller = new AbortController()
activeControllerRef.current = controller

const [taxonomy, task, principal] = await Promise.all([
  fetchTaxonomy(subject, controller.signal),
  claimNextTask(subject, controller.signal),
  fetchCurrentUser(controller.signal),
])
```

新一轮加载先取消旧请求，并把同一个 `signal` 传给本轮三个 Fetch。`Promise.all` 并行执行彼此独立的请求，减少串行等待时间。

捕获 `AbortError` 时直接返回，因为主动取消是正常生命周期事件，不应显示红色错误条。

## 14. State 是当前渲染的快照

`await` 前后可能已经发生新渲染。提交前先捕获当前任务：

```ts
const currentBundle = state.bundle
const result = await submitAnnotation(currentBundle.task.id, {
  expected_version: currentBundle.task.version,
  selected_tags: [...state.selectedTags],
})
```

这能确保 URL、预测 ID 和乐观锁版本来自同一任务快照。后端仍会校验租约、版本和操作员，前端快照不能替代服务端并发控制。

## 15. `startTransition`：标记非紧急更新

任务列表和整页预测替换可能产生较多 DOM 更新，而按钮点击反馈应尽快出现：

```tsx
startTransition(() => {
  dispatch({ type: 'refreshSucceeded', bundle: nextBundle })
})
```

Transition 只降低更新优先级，不会：

- 让 API 请求更快。
- 自动取消旧请求。
- 替代 loading 状态。
- 保证网络顺序。

当前使用是教学和未来列表扩展的准备；页面规模较小时，去掉它也不会改变业务正确性。

## 16. 受控表单

`SubmissionBar` 的备注由父 Hook 保存：

```tsx
<textarea
  value={note}
  onChange={(event) => onNoteChange(event.target.value)}
/>
```

浏览器输入值和 React State 始终一致，这叫受控组件。它对应 Vue 的 `v-model` 展开形式。

优点是校验、重置和提交都读取同一状态；代价是每次输入都会触发状态更新。普通运营表单优先选择受控模式，大型富文本或极高频输入再评估非受控 Ref 和专用表单库。

## 17. 条件渲染与类型收窄

React 使用 JavaScript 表达式表达页面状态：

```tsx
{loading ? (
  <LoadingState />
) : !bundle ? (
  <EmptyState />
) : (
  <QuestionDetail task={bundle.task} />
)}
```

在 `bundle` 非空分支中，TypeScript 能收窄它的类型。复杂条件应先计算有业务名称的布尔变量，或抽成独立组件，避免多层三元表达式难以阅读。

`condition && <Component />` 适合简单可选区域，如错误条和题目解析。

## 18. 列表渲染与 `key`

React 用数组 `map` 渲染列表：

```tsx
rows.map((row) => (
  <button key={row.definition.code}>...</button>
))
```

`key` 用于识别同一个业务节点。标签使用稳定编码，证据使用 `evidence_id`。不要使用数组索引，因为插入、删除或排序后，索引不再代表同一个项目，可能导致输入状态错位。

`key` 只供 React 协调树使用，不会自动成为组件 Props。

## 19. TypeScript 合同与空值

`types.ts` 用字面量联合约束学科：

```ts
export type Subject = 'chinese' | 'math' | 'english'
```

接口和 FastAPI Pydantic 响应保持一致。`TaskBundleResponse | null` 明确表达 204 空队列，而不是伪造空对象。

需要注意：TypeScript 只检查编译期，不能证明网络返回值真实符合接口。当前客户端信任自有 FastAPI；面向不受控第三方 API 时，应增加 Zod 等运行时 Schema 校验。

## 20. API 客户端与安全边界

`api.ts` 集中处理：

- `VITE_API_BASE_URL`。
- JSON Header 和 Cookie 凭据。
- 内存 Bearer Token。
- 开发环境调试身份。
- 204 空响应。
- HTTP 错误码和 `X-Request-ID`。

组件不重复写 Fetch，避免认证和错误逻辑漂移。令牌不写 `localStorage`，降低 XSS 后持久令牌泄漏风险；正式环境可接 OIDC 内存令牌或同域 BFF HttpOnly Cookie。

前端权限控制只改善交互，不能构成安全边界。每个 FastAPI 路由仍必须验证 JWT 和角色。

## 21. 可访问性不是额外插件

页面使用：

- `button type="button"`，避免未来进入表单后意外提交。
- `aria-pressed` 表达标签按钮的开关状态。
- `aria-current="page"` 表达当前学科。
- `role="alert"` 让错误被辅助技术及时感知。
- `label` 包裹 textarea，形成字段名关联。
- 装饰性图标使用 `aria-hidden="true"`。
- 原生 `progress` 表达置信度数值。

键盘访问、焦点可见性和语义化元素应在组件编写时完成，而不是上线前补丁。

## 22. CSS 与响应式布局

React 和 Vue 共享同一视觉规范，但 CSS 文件分别存在，便于两个工程独立构建：

- 大屏：左侧学科导航，中间题目/标签，右侧证据。
- 小于 1080px：证据移到主区下方。
- 小于 760px：学科导航变为顶部三段栏，提交动作纵向排列。
- 固定提交栏使用安全区域和稳定尺寸。
- 390px E2E 检查 `scrollWidth <= clientWidth + 1`。

图标来自 `lucide-react`，没有手绘 SVG。按钮、复选框、进度条和工具栏都定义稳定尺寸，动态文本不会无意改变整体布局。

## 23. E2E 如何验证真实业务

`e2e/react-workbench.spec.ts` 不 Mock API：

1. 通过真实导入接口创建唯一题目并同步预测。
2. 在创建响应上验证开放题存在默认建议。
3. 打开页面，通过 FIFO 领取任意合法任务。
4. 验证身份、题目、标签、RAG 证据和提交栏。
5. 人工增加标签、填写备注并真实提交。
6. 在 1440px 和 390px 视口检查布局。

“模型会给建议”和“页面能处理当前 FIFO 任务”被分开断言，因为队列中的旧任务可能合法地有零个默认建议。测试不应依赖偶然数据顺序。

运行：

```bash
cd question_labeling_system/frontend-react
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

Playwright 配置会自动启动或复用 FastAPI 8010 和 React 5174，并使用单 Worker 防止测试互相争抢队列。

## 24. 为什么当前没有更多库

当前页面有意不引入：

- React Router：只有一个工作台路由。
- Redux/Zustand：没有跨页面客户端状态。
- TanStack Query：请求量小，取消、状态和事务语义适合直接教学。
- 表单库：只有一个 textarea 和标签集合。
- React Compiler：当前组件没有需要自动记忆化解决的性能瓶颈。

库应解决已经出现的复杂度。页面增加缓存、分页、后台刷新和多个路由后，再分别评估 Query、Router 和全局状态方案。

## 25. 推荐阅读顺序

1. `types.ts`：先理解后端合同。
2. `api.ts`：看一次请求如何处理认证、204 和错误。
3. `hooks/useLabelingWorkbench.ts`：沿 Action 阅读状态机。
4. `components/SubmissionBar.tsx`：理解受控输入和回调。
5. `components/LabelReviewList.tsx`：理解 Props、列表和条件渲染。
6. `App.tsx`：理解页面互斥状态和组件编排。
7. `main.tsx`：理解根节点和 Strict Mode。
8. `e2e/react-workbench.spec.ts`：看用户行为怎样成为可执行验收标准。

随后阅读 [Vue 3 与 React 19 对照及选型](VUE_VS_REACT.md)，把相同业务动作逐项映射到 Vue Composition API。