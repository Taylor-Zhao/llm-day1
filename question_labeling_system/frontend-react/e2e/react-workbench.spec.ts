// 导入 Playwright 断言、测试函数和上下文类型。
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

// E2E 通过 React Vite 代理访问真实 FastAPI。
const debugHeaders = {
  // 导入接口使用独立测试主体。
  'X-Debug-User': 'react-e2e-importer',
  // 同时授予导入和补录权限。
  'X-Debug-Roles': 'labeler,importer',
}

// enqueueQuestion 为每条测试准备唯一待办。
async function enqueueQuestion(request: APIRequestContext, subject: 'chinese' | 'math' | 'english'): Promise<void> {
  // 时间和随机片段共同避免 external_id 冲突。
  const externalId = `react-e2e-${subject}-${Date.now()}-${Math.random().toString(16).slice(2)}`
  // 三个学科使用不同代表题型。
  const examples = {
    // 语文开放答案。
    chinese: { stem: '请写出一个描写夏天的词语：____。', answer: '骄阳似火，其他合理答案均可。', analysis: '开放表达题' },
    // 数学单位计算。
    math: { stem: '正方形边长 4 厘米，周长是____。', answer: '16 厘米', analysis: '周长计算题' },
    // 英语时态填空。
    english: { stem: 'Last week Amy ____ (finish) the book.', answer: 'finished', analysis: '一般过去时' },
  }
  // 调用真实题目导入和同步预测接口。
  const response = await request.post('/api/v1/questions', {
    // 注入开发环境认证头。
    headers: debugHeaders,
    // 发送后端 CreateQuestionRequest。
    data: {
      // 题目结构与 FastAPI Pydantic 模型一致。
      question: {
        // 唯一业务 ID。
        external_id: externalId,
        // 目标学科。
        subject,
        // 题干原文。
        stem: examples[subject].stem,
        // 参考答案。
        reference_answer: examples[subject].answer,
        // 题目解析。
        analysis: examples[subject].analysis,
        // 明确测试来源。
        source: 'react_playwright_e2e',
        // 测试数据无外部地址。
        source_uri: '',
        // 测试题由项目创建。
        license_name: 'project-authored',
      },
      // 页面打开前生成完整建议。
      predict_now: true,
    },
  })
  // 导入必须成功。
  expect(response.status()).toBe(201)
  // 解析刚创建题目的预测结果，不依赖 FIFO 页面随后领取到哪条历史任务。
  const created = await response.json() as { prediction: { suggestions: Array<{ selected_by_default: boolean }> } }
  // 开放答案题应至少产生一个默认标签，单独验证模型预标注业务能力。
  expect(created.prediction.suggestions.some((item) => item.selected_by_default)).toBe(true)
}

// expectWorkbenchReady 验证 React 页面完成初始 Effect 和数据加载。
async function expectWorkbenchReady(page: Page): Promise<void> {
  // 打开 React 首页。
  await page.goto('/')
  // 验证 React 版框架标识。
  await expect(page.getByText('智能补录工作台 · React 19')).toBeVisible()
  // 验证服务端 /me 返回的 React 操作员。
  await expect(page.locator('.operator-block')).toContainText('react-reviewer')
  // 验证主标题。
  await expect(page.getByRole('heading', { name: '标签复核' })).toBeVisible()
  // 验证题干加载完成。
  await expect(page.locator('.question-stem')).toBeVisible()
  // 验证标签列表加载完成。
  await expect(page.locator('.label-row').first()).toBeVisible()
  // 验证 RAG 证据区域加载完成。
  await expect(page.locator('.evidence-panel')).toBeVisible()
  // 验证人工提交栏可见。
  await expect(page.locator('.submission-bar')).toBeVisible()
}

// expectNoHorizontalOverflow 检查根文档没有横向滚动。
async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  // page.evaluate 在浏览器上下文读取真实布局尺寸。
  const dimensions = await page.evaluate(() => ({
    // 完整内容宽度。
    scrollWidth: document.documentElement.scrollWidth,
    // 当前视口内容宽度。
    clientWidth: document.documentElement.clientWidth,
  }))
  // 允许浏览器亚像素取整的一像素误差。
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1)
}

// 桌面用例覆盖真实任务、模型建议、人工修改和提交。
test('React desktop workbench completes the human review flow', async ({ page, request }) => {
  // 创建唯一语文待办。
  await enqueueQuestion(request, 'chinese')
  // 设置桌面工作区尺寸。
  await page.setViewportSize({ width: 1440, height: 1000 })
  // 等待 React 页面和 API 数据就绪。
  await expectWorkbenchReady(page)
  // FIFO 可能领取到零默认建议的合法任务，因此页面只要求完整展示全部标签。
  await expect(page.locator('.label-row')).not.toHaveCount(0)
  // 至少一条相似题证据可供核验。
  await expect(page.locator('.evidence-item').first()).toBeVisible()
  // 桌面不应出现水平滚动。
  await expectNoHorizontalOverflow(page)
  // 点击一个未选标签模拟人工补标。
  await page.locator('.label-row:not(.selected)').first().click()
  // 受控 textarea 输入人工依据。
  await page.locator('.note-field textarea').fill('React E2E：人工补充一个遗漏标签。')
  // 保存 React 桌面截图用于与 Vue 对比。
  await page.screenshot({ path: '../docs/screenshots/react-labeling-desktop.png', fullPage: true })
  // 提交人工最终标签。
  await page.getByRole('button', { name: '确认并提交' }).click()
  // 验证成功状态。
  await expect(page.getByText(/已提交 #/)).toBeVisible()
})

// 移动用例覆盖响应式布局和固定提交栏。
test('React mobile workbench has no horizontal overflow', async ({ page, request }) => {
  // 创建唯一语文待办。
  await enqueueQuestion(request, 'chinese')
  // 使用常见手机视口。
  await page.setViewportSize({ width: 390, height: 844 })
  // 等待页面就绪。
  await expectWorkbenchReady(page)
  // 三个学科导航在手机上均可见。
  await expect(page.getByRole('button', { name: /语文/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /数学/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /英语/ })).toBeVisible()
  // 手机页面不应横向溢出。
  await expectNoHorizontalOverflow(page)
  // 获取固定提交栏真实布局框。
  const submissionBox = await page.locator('.submission-bar').boundingBox()
  // 提交栏必须参与布局。
  expect(submissionBox).not.toBeNull()
  // 右边缘不能超过 390px 视口。
  expect((submissionBox?.x ?? 0) + (submissionBox?.width ?? 0)).toBeLessThanOrEqual(391)
  // 保存 React 移动截图用于对比。
  await page.screenshot({ path: '../docs/screenshots/react-labeling-mobile.png', fullPage: true })
})