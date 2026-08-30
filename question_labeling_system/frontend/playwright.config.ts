// 导入 Playwright 类型化配置函数。
import { defineConfig } from '@playwright/test'

// 导出桌面与移动端共享的端到端配置。
export default defineConfig({
  // 指定补录系统端到端测试目录。
  testDir: './e2e',
  // 单条用例最长等待三十秒。
  timeout: 30_000,
  // CI 中禁止意外提交 test.only。
  forbidOnly: Boolean(process.env.CI),
  // CI 对偶发浏览器失败重试一次，本地不隐藏问题。
  retries: process.env.CI ? 1 : 0,
  // CI 使用一个 Worker 避免争抢同一补录队列，本地也保持顺序可读。
  workers: 1,
  // 配置所有浏览器上下文的通用行为。
  use: {
    // 通过 Vite 访问页面和同域代理 API。
    baseURL: 'http://127.0.0.1:5173',
    // 复用本机已安装并持续更新的 Google Chrome，兼容当前 macOS 12。
    channel: 'chrome',
    // 仅失败时保留截图，显式基线截图由用例单独保存。
    screenshot: 'only-on-failure',
    // 首次重试时记录 Trace，便于定位网络和布局问题。
    trace: 'on-first-retry',
  },
  // 自动启动后端和前端；本地已运行时复用现有服务。
  webServer: [
    {
      // 从前端目录切换到后端并启动 FastAPI。
      command: 'cd ../backend && PYTHONPATH=. ../../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010',
      // 使用存活探针判断 API 就绪。
      url: 'http://127.0.0.1:8010/healthz',
      // 本地复用当前 API，CI 则启动新进程。
      reuseExistingServer: !process.env.CI,
      // 给依赖装配和数据库建表预留时间。
      timeout: 60_000,
    },
    {
      // 启动 Vite 开发服务并固定端口。
      command: 'npm run dev -- --host 127.0.0.1',
      // 使用首页判断 Vue 已就绪。
      url: 'http://127.0.0.1:5173',
      // 本地复用当前 Vite，CI 则启动新进程。
      reuseExistingServer: !process.env.CI,
      // 给依赖预构建预留时间。
      timeout: 60_000,
    },
  ],
})
