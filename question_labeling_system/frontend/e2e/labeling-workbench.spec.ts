// 导入 Playwright 断言、请求上下文和测试函数。
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

// 定义仅开发环境后端接受的调试身份 Header。
const debugHeaders = {
  // 指定测试操作员。
  'X-Debug-User': 'playwright-reviewer',
  // 授予导入和人工补录角色。
  'X-Debug-Roles': 'labeler,importer',
}

// 在打开页面前创建唯一待办，保证重复运行始终有任务可领取。
async function enqueueQuestion(request: APIRequestContext, subject: 'chinese' | 'math' | 'english'): Promise<void> {
  // 使用时间和随机数组合稳定避免 external_id 冲突。
  const externalId = `e2e-${subject}-${Date.now()}-${Math.random().toString(16).slice(2)}`
  // 按学科选择一条可预测填空题。
  const examples = {
    // 语文开放答案用于验证答案不唯一标签。
    chinese: { stem: '请写出一个描写秋天的词语：____。', answer: '秋高气爽，其他合理答案均可。', analysis: '开放表达题' },
    // 数学单位题用于验证计算和单位标签。
    math: { stem: '长方形长 6 厘米、宽 2 厘米，面积是____。', answer: '12 平方厘米', analysis: '面积计算题' },
    // 英语时态题用于验证语法标签。
    english: { stem: 'Yesterday Tom ____ (visit) the museum.', answer: 'visited', analysis: '一般过去时' },
  }
  // 调用真实 FastAPI 导入接口并同步生成模型建议。
  const response = await request.post('/api/v1/questions', {
    // 发送本地测试身份。
    headers: debugHeaders,
    // 提交结构化题目。
    data: {
      // 包装后端 CreateQuestionRequest。
      question: {
        // 保存唯一业务 ID。
        external_id: externalId,
        // 保存目标学科。
        subject,
        // 保存题干。
        stem: examples[subject].stem,
        // 保存参考答案。
        reference_answer: examples[subject].answer,
        // 保存题目解析。
        analysis: examples[subject].analysis,
        // 明确标记 E2E 数据来源。
        source: 'playwright_e2e',
      },
      // 同步生成建议，页面打开后无需等待 Worker。
      predict_now: true,
    },
  })
  // 导入接口必须返回 201。
  expect(response.status()).toBe(201)
}

// 等待页面完成任务领取并显示核心工作区。
async function expectWorkbenchReady(page: Page): Promise<void> {
  // 打开 Vue 工作台。
  await page.goto('/')
  // 验证操作界面标题。
  await expect(page.getByRole('heading', { name: '标签复核' })).toBeVisible()
  // 验证顶栏展示服务端确认的开发操作员身份。
  await expect(page.locator('.operator-block')).toContainText('vue-reviewer')
  // 验证题干已加载。
  await expect(page.locator('.question-stem')).toBeVisible()
  // 验证标签列表已加载。
  await expect(page.locator('.label-row').first()).toBeVisible()
  // 验证 RAG 证据面板已加载。
  await expect(page.locator('.evidence-panel')).toBeVisible()
  // 验证人工提交栏始终可见。
  await expect(page.locator('.submission-bar')).toBeVisible()
}

// 检查根文档没有横向溢出。
async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  // 在浏览器上下文读取布局尺寸。
  const dimensions = await page.evaluate(() => ({
    // 读取完整内容宽度。
    scrollWidth: document.documentElement.scrollWidth,
    // 读取可见视口宽度。
    clientWidth: document.documentElement.clientWidth,
  }))
  // 允许浏览器亚像素取整产生一像素误差。
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1)
}

// 桌面用例验证完整三栏工作台和人工提交。
test('desktop reviewer can inspect evidence, adjust tags and submit', async ({ page, request }) => {
  // 为本次用例准备至少一个待办。
  await enqueueQuestion(request, 'chinese')
  // 设置桌面视口。
  await page.setViewportSize({ width: 1440, height: 1000 })
  // 等待工作台就绪。
  await expectWorkbenchReady(page)
  // 至少一个模型建议标签应默认选中。
  await expect(page.locator('.ai-badge').first()).toBeVisible()
  // 至少展示一条 RAG 相似题。
  await expect(page.locator('.evidence-item').first()).toBeVisible()
  // 验证页面没有横向滚动条。
  await expectNoHorizontalOverflow(page)
  // 选择一个当前未选标签以模拟人工纠正。
  await page.locator('.label-row:not(.selected)').first().click()
  // 填写人工修改原因。
  await page.locator('.note-field textarea').fill('E2E：人工补充一个遗漏标签。')
  // 保存桌面基线截图。
  await page.screenshot({ path: '../docs/screenshots/labeling-desktop.png', fullPage: true })
  // 提交人工最终结论。
  await page.getByRole('button', { name: '确认并提交' }).click()
  // 验证成功状态出现。
  await expect(page.getByText(/已提交 #/)).toBeVisible()
})

// 移动用例验证顶部学科导航、单列内容和固定提交栏。
test('mobile layout remains usable without horizontal overflow', async ({ page, request }) => {
  // 为移动用例准备至少一个待办。
  await enqueueQuestion(request, 'chinese')
  // 使用常见移动设备视口。
  await page.setViewportSize({ width: 390, height: 844 })
  // 等待工作台就绪。
  await expectWorkbenchReady(page)
  // 验证语文、数学、英语导航均可见。
  await expect(page.getByRole('button', { name: /语文/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /数学/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /英语/ })).toBeVisible()
  // 验证页面没有横向滚动条。
  await expectNoHorizontalOverflow(page)
  // 验证固定提交栏没有超出当前视口宽度。
  const submissionBox = await page.locator('.submission-bar').boundingBox()
  // 元素必须有可计算布局框。
  expect(submissionBox).not.toBeNull()
  // 提交栏右边缘不能超过移动视口。
  expect((submissionBox?.x ?? 0) + (submissionBox?.width ?? 0)).toBeLessThanOrEqual(391)
  // 保存移动端基线截图。
  await page.screenshot({ path: '../docs/screenshots/labeling-mobile.png', fullPage: true })
})
