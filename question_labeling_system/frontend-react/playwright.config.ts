// 导入 Playwright 类型化配置函数。
import { defineConfig } from '@playwright/test'

// 导出 React 工作台端到端测试配置。
export default defineConfig({
  // 指定测试文件目录。
  testDir: './e2e',
  // 单条用例最长等待三十秒。
  timeout: 30_000,
  // CI 禁止意外提交 test.only。
  forbidOnly: Boolean(process.env.CI),
  // CI 对浏览器偶发失败重试一次，本地保持快速失败。
  retries: process.env.CI ? 1 : 0,
  // 单 Worker 避免并发争抢同一个人工补录队列。
  workers: 1,
  // 配置所有页面共享选项。
  use: {
    // React Vite 服务运行在独立 5174 端口。
    baseURL: 'http://127.0.0.1:5174',
    // 复用本机持续更新的 Google Chrome，兼容当前 macOS。
    channel: 'chrome',
    // 失败时自动保留页面截图。
    screenshot: 'only-on-failure',
    // 首次重试时记录完整 Trace。
    trace: 'on-first-retry',
  },
  // 测试可以独立启动后端和 React 服务，也可复用当前服务。
  webServer: [
    {
      // 使用绝对虚拟环境路径启动 FastAPI，避免当前目录影响命令。
      command: 'PYTHONPATH=../backend /Users/zhaoyonggng/work/llm-day1/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010',
      // 通过存活探针等待 API 就绪。
      url: 'http://127.0.0.1:8010/healthz',
      // 本地开发复用已运行后端。
      reuseExistingServer: !process.env.CI,
      // 为模型和数据库装配预留时间。
      timeout: 60_000,
    },
    {
      // 启动 React Vite 开发服务。
      command: 'npm run dev -- --host 127.0.0.1',
      // 等待 React 首页可访问。
      url: 'http://127.0.0.1:5174',
      // 本地开发复用已运行服务。
      reuseExistingServer: !process.env.CI,
      // 为依赖预构建预留时间。
      timeout: 60_000,
    },
  ],
})
