// 导入 React Fast Refresh 与 JSX 编译插件。
import react from '@vitejs/plugin-react'
// 导入 Vite 类型化配置助手。
import { defineConfig } from 'vite'

// 导出 React 补录工作台的开发与构建配置。
export default defineConfig({
  // 启用 React JSX 转换和开发期 Fast Refresh。
  plugins: [react()],
  // 配置本地开发服务器。
  server: {
    // React 版使用 5174，便于和 5173 的 Vue 版同时运行比较。
    port: 5174,
    // 端口被占用时直接报错，避免误打开其他应用。
    strictPort: true,
    // 将同域业务请求代理到同一个 FastAPI 后端。
    proxy: {
      // 代理版本化业务 API。
      '/api': 'http://127.0.0.1:8010',
      // 代理存活探针。
      '/healthz': 'http://127.0.0.1:8010',
      // 代理就绪探针。
      '/readyz': 'http://127.0.0.1:8010',
      // 代理 Prometheus 指标，仅本地调试使用。
      '/metrics': 'http://127.0.0.1:8010',
    },
  },
})
