// 导入 Vue 单文件组件编译插件。
import vue from '@vitejs/plugin-vue'
// 导入 Vite 类型化配置助手。
import { defineConfig } from 'vite'

// 导出开发与构建配置。
export default defineConfig({
  // 启用 Vue 3 单文件组件支持。
  plugins: [vue()],
  // 配置本地开发服务器。
  server: {
    // 使用固定端口便于后端 CORS 白名单和文档说明。
    port: 5173,
    // 端口被占用时直接失败，避免打开错误服务。
    strictPort: true,
    // 将业务和探针请求代理到 FastAPI，生产环境由反向代理同域转发。
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
