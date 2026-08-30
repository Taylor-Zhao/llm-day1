// 导入页面状态图标。
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Inbox,
  Info,
  RefreshCw,
  Search,
  Sparkles,
  UserRound,
} from 'lucide-react'
// 导入按职责拆分的函数组件。
import { EvidencePanel } from './components/EvidencePanel'
import { LabelReviewList } from './components/LabelReviewList'
import { QuestionDetail } from './components/QuestionDetail'
import { SubmissionBar } from './components/SubmissionBar'
import { SubjectSidebar } from './components/SubjectSidebar'
// 导入 React 版工作台状态 Hook。
import { useLabelingWorkbench } from './hooks/useLabelingWorkbench'
// 导入纯展示格式化函数。
import { formatLease } from './presentation'
// 导入独立学科配置，供标题和导航共享。
import { SUBJECT_OPTIONS } from './subjects'

// App 是根函数组件，只负责页面编排，不直接发送 HTTP 请求。
function App() {
  // 自定义 Hook 返回当前状态、派生值和用户命令。
  const workbench = useLabelingWorkbench()
  // 根据当前学科查找导航展示配置。
  const currentSubject = SUBJECT_OPTIONS.find((item) => item.code === workbench.subject)
  // 当前没有任务时使用稳定模型状态占位。
  const modelVersion = workbench.bundle?.prediction.model_version ?? '等待任务'

  // JSX 描述当前状态应呈现的 DOM，不直接操作真实 DOM。
  return (
    // 应用外壳包含固定顶栏和主体区域。
    <div className="app-shell">
      {/* 顶栏展示产品、框架版本、模型链和认证主体。 */}
      <header className="topbar">
        {/* 产品标识是首屏主要信号。 */}
        <div className="brand-block">
          {/* 方形文字标志避免额外网络图片依赖。 */}
          <span className="brand-mark" aria-hidden="true">审</span>
          {/* 产品名称与当前前端实现。 */}
          <div>
            {/* 产品名称。 */}
            <strong>题审台</strong>
            {/* 明确当前页面由 React 19 实现，方便与 Vue 对照。 */}
            <small>智能补录工作台 · React 19</small>
          </div>
        </div>
        {/* 模型版本帮助人工反馈与线上模型关联。 */}
        <div className="model-status" title={modelVersion}>
          {/* 模型建议图标。 */}
          <Sparkles size={16} aria-hidden="true" />
          {/* 字段名称。 */}
          <span>模型链</span>
          {/* 长版本字符串由 CSS 截断。 */}
          <code>{modelVersion}</code>
        </div>
        {/* 操作员身份来自 FastAPI /me，而不是前端硬编码。 */}
        <div className="operator-block">
          {/* 用户图标。 */}
          <UserRound size={17} aria-hidden="true" />
          {/* 可选链和空值合并处理初始加载状态。 */}
          <span>{workbench.principal?.user_id ?? '身份加载中'}</span>
        </div>
      </header>

      {/* 主体由学科导航和任务工作区组成。 */}
      <div className="shell-body">
        {/* 子组件通过 Props 接收状态和事件，不能直接访问 Hook 内部 Reducer。 */}
        <SubjectSidebar subject={workbench.subject} onSubjectChange={workbench.changeSubject} />

        {/* main 是页面唯一主内容区域。 */}
        <main className="workspace">
          {/* 工具栏展示当前队列并提供重新领取命令。 */}
          <div className="workspace-toolbar">
            {/* 当前学科标题。 */}
            <div>
              {/* 可选链处理极端配置不一致。 */}
              <span className="eyebrow">{currentSubject?.label ?? '未知学科'} · 填空题</span>
              {/* 页面主标题保持运营工具尺度。 */}
              <h1>标签复核</h1>
            </div>
            {/* 重新领取当前学科任务。 */}
            <button type="button" className="secondary-button" disabled={workbench.loading} onClick={workbench.loadNext}>
              {/* 加载时旋转图标但不改变按钮宽度。 */}
              <RefreshCw size={17} className={workbench.loading ? 'spinning' : undefined} aria-hidden="true" />
              {/* 命令文字。 */}
              重新领取
            </button>
          </div>

          {/* && 条件渲染错误条，对应 Vue 的 v-if。 */}
          {workbench.errorMessage && (
            <div className="message-banner error-message" role="alert">
              {/* 错误状态图标。 */}
              <AlertTriangle size={18} aria-hidden="true" />
              {/* 错误文本可能包含后端 request ID。 */}
              <span>{workbench.errorMessage}</span>
            </div>
          )}

          {/* 嵌套三元表达式选择互斥页面状态。 */}
          {workbench.loading ? (
            // 首次领取和切学科时显示加载状态。
            <section className="loading-state" aria-live="polite">
              {/* 旋转图标。 */}
              <RefreshCw size={26} className="spinning" aria-hidden="true" />
              {/* 加载文本。 */}
              <strong>正在领取任务</strong>
            </section>
          ) : workbench.successMessage && !workbench.bundle ? (
            // 人工提交成功后展示确认和下一题命令。
            <section className="empty-state" aria-live="polite">
              {/* 成功图标。 */}
              <CheckCircle2 size={34} aria-hidden="true" />
              {/* 后端记录 ID 和修改数量。 */}
              <h2>{workbench.successMessage}</h2>
              {/* 主动领取下一题，防止成功信息一闪而过。 */}
              <button type="button" className="primary-button" onClick={workbench.loadNext}>
                {/* 搜索图标。 */}
                <Search size={17} aria-hidden="true" />
                {/* 命令文本。 */}
                领取下一题
              </button>
            </section>
          ) : !workbench.bundle ? (
            // API 返回 204 时展示空队列状态。
            <section className="empty-state">
              {/* 空收件箱图标。 */}
              <Inbox size={36} aria-hidden="true" />
              {/* 空状态标题。 */}
              <h2>当前队列已清空</h2>
              {/* 使用当前学科配置生成说明。 */}
              <p>暂时没有待复核的{currentSubject?.label ?? ''}题目。</p>
              {/* 允许人工稍后再次检查。 */}
              <button type="button" className="secondary-button" onClick={workbench.loadNext}>
                {/* 刷新图标。 */}
                <RefreshCw size={17} aria-hidden="true" />
                {/* 命令文本。 */}
                再次检查
              </button>
            </section>
          ) : (
            // bundle 非空时 TypeScript 会在当前 JSX 分支中收窄类型。
            <>
              {/* 任务元数据条显示乐观锁、租约和建议数量。 */}
              <div className="task-meta-strip">
                {/* 数据库任务 ID 和版本。 */}
                <span><Info size={15} aria-hidden="true" /> 任务 #{workbench.bundle.task.id} · v{workbench.bundle.task.version}</span>
                {/* 人工任务租约到期时间。 */}
                <span><Clock3 size={15} aria-hidden="true" /> 租约至 {formatLease(workbench.bundle.task.claimed_until)}</span>
                {/* 模型默认建议数量。 */}
                <span><Sparkles size={15} aria-hidden="true" /> 建议 {workbench.suggestedCount} 项</span>
              </div>

              {/* map 渲染全部 RAG/LLM 降级警告。 */}
              {workbench.bundle.prediction.warnings.map((warning) => (
                // warning 文本在当前工作流中唯一，可作为 key。
                <div key={warning} className="message-banner warning-message">
                  {/* 警告图标。 */}
                  <AlertTriangle size={17} aria-hidden="true" />
                  {/* 降级说明。 */}
                  <span>{warning}</span>
                </div>
              ))}

              {/* 任务主体由题目/标签主区和 RAG 证据侧栏组成。 */}
              <div className="review-layout">
                {/* 主复核面板组合两个职责独立的子组件。 */}
                <section className="review-main">
                  {/* 题目详情是只读组件。 */}
                  <QuestionDetail task={workbench.bundle.task} />
                  {/* 标签列表通过回调把点击事件交还 Reducer。 */}
                  <LabelReviewList
                    rows={workbench.labelRows}
                    selectedCount={workbench.selectedTags.length}
                    changedCount={workbench.changedCount}
                    onToggle={workbench.toggleTag}
                  />
                </section>
                {/* 证据组件只读取模型快照和标签字典。 */}
                <EvidencePanel evidence={workbench.bundle.prediction.evidence} taxonomy={workbench.taxonomy} />
              </div>

              {/* 受控备注和两个命令集中在固定底栏。 */}
              <SubmissionBar
                note={workbench.note}
                refreshing={workbench.refreshing}
                submitting={workbench.submitting}
                onNoteChange={workbench.changeNote}
                onRefresh={() => { void workbench.rerunPrediction() }}
                onSubmit={() => { void workbench.submitReview() }}
              />
            </>
          )}
        </main>
      </div>
    </div>
  )
}

// 默认导出供 main.tsx 导入。
export default App
