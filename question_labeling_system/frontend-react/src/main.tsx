// StrictMode 会在开发环境额外检查不纯渲染和 Effect 清理问题。
import { StrictMode } from 'react'
// createRoot 是 React 18+ 的并发根节点入口。
import { createRoot } from 'react-dom/client'
// 导入整个 React 工作台的全局样式。
import './index.css'
// 导入根函数组件。
import App from './App.tsx'

// 查找 index.html 提供的根 DOM 节点。
const rootElement = document.getElementById('root')

// 根节点缺失属于部署模板错误，应立即失败而不是静默白屏。
if (!rootElement) {
  // 抛出清晰错误供浏览器日志和前端监控捕获。
  throw new Error('React root element #root was not found')
}

// 使用已经校验非空的 DOM 元素创建 React 根并渲染应用。
createRoot(rootElement).render(
  // StrictMode 只在开发环境执行额外检查，不会渲染可见 DOM。
  <StrictMode>
    {/* App 负责组合页面组件，业务状态集中在自定义 Hook 中。 */}
    <App />
  </StrictMode>,
)
