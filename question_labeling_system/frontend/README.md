# 题审台 Vue 补录工作台

本目录使用 Vue 3、TypeScript 和 Vite 实现题目标签人工复核页面。

## 代码结构

- `src/App.vue`：学科导航、题目、标签、相似题和提交操作。
- `src/composables/useLabelingWorkbench.ts`：响应式状态与业务动作。
- `src/api.ts`：认证、错误、空响应和请求 ID 处理。
- `src/types.ts`：与 FastAPI 对齐的 TypeScript 契约。
- `src/style.css`：桌面、平板和移动端工作台样式。
- `e2e/labeling-workbench.spec.ts`：真实 API 的 Playwright 测试。

## 运行

```bash
npm install
npm run dev -- --host 127.0.0.1
```

需要先在 `127.0.0.1:8010` 启动后端，Vite 会将 `/api` 代理到 FastAPI。

## 验证

```bash
npm run typecheck
npm run build
npm run test:e2e
```

完整 Vue 用法说明见 [Vue 3 对应用法学习指南](../docs/VUE_LEARNING_GUIDE.md)。
