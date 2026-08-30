// 只导入 TypeScript 类型，不会增加浏览器运行时代码。
import type {
  AnnotationResponse,
  ApiErrorPayload,
  PrincipalResponse,
  Subject,
  SubmitAnnotationRequest,
  TaskBundleResponse,
  TaxonomyResponse,
} from './types'

// VITE_API_BASE_URL 支持同域部署和独立 API 域名。
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

// accessToken 只保存在页面内存，不写入容易被 XSS 读取的 localStorage。
let accessToken = ''

// 企业 OIDC 初始化完成后可以调用该函数注入访问令牌。
export function setAccessToken(token: string): void {
  // 清理令牌首尾空白。
  accessToken = token.trim()
}

// ApiRequestError 为 UI 保留 HTTP 状态、业务码和请求 ID。
export class ApiRequestError extends Error {
  // HTTP 状态码。
  readonly status: number
  // 后端稳定机器错误码。
  readonly code: string
  // 跨系统排查请求 ID。
  readonly requestId: string

  // 使用后端错误体构造类型化异常。
  constructor(status: number, payload: ApiErrorPayload, fallbackRequestId: string) {
    // 优先展示业务 message，其次展示字符串 detail。
    super(payload.message ?? (typeof payload.detail === 'string' ? payload.detail : `请求失败（HTTP ${status}）`))
    // 修复编译目标环境中的 Error 子类原型链。
    Object.setPrototypeOf(this, ApiRequestError.prototype)
    // 保存 HTTP 状态码。
    this.status = status
    // 没有业务码时使用通用错误码。
    this.code = payload.code ?? 'HTTP_ERROR'
    // 优先使用响应体 request_id，再回退响应头。
    this.requestId = payload.request_id ?? fallbackRequestId
  }
}

// 根据生产令牌或开发模式构造认证头。
function authenticationHeaders(): Record<string, string> {
  // 正式令牌存在时使用 Bearer Token。
  if (accessToken) {
    // 返回标准 OAuth2 Authorization 头。
    return { Authorization: `Bearer ${accessToken}` }
  }
  // 生产构建绝不发送可伪造调试身份。
  if (!import.meta.env.DEV) {
    // 返回空头，交由同域 BFF Cookie 或 OIDC 集成认证。
    return {}
  }
  // 本地 React 工作台使用独立用户，便于与 Vue 同时比较。
  return {
    // 指定开发操作员。
    'X-Debug-User': 'react-reviewer',
    // 授予补录和导入角色。
    'X-Debug-Roles': 'labeler,importer',
  }
}

// requestJson 统一处理认证、JSON、204 和错误转换。
async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T | null> {
  // 使用 Headers API 安全合并默认值与调用方值。
  const headers = new Headers({
    // API 请求和响应统一使用 JSON。
    'Content-Type': 'application/json',
    // 注入可信登录凭据或开发身份。
    ...authenticationHeaders(),
    // 允许具体请求补充 Header。
    ...(init.headers as Record<string, string> | undefined),
  })
  // 发起同域或配置域名请求。
  const response = await fetch(`${API_BASE_URL}${path}`, {
    // 支持 HttpOnly/SameSite Cookie 的 BFF 模式。
    credentials: 'include',
    // 接收 method、body、signal 等请求选项。
    ...init,
    // 使用合并后的 Header。
    headers,
  })
  // 204 表示当前学科暂无待办。
  if (response.status === 204) {
    // 不解析空响应体。
    return null
  }
  // 非 2xx 响应转换为类型化异常。
  if (!response.ok) {
    // 网关可能返回非 JSON，因此解析失败时使用空对象。
    const payload = (await response.json().catch(() => ({}))) as ApiErrorPayload
    // 抛出包含排查上下文的异常。
    throw new ApiRequestError(response.status, payload, response.headers.get('X-Request-ID') ?? '')
  }
  // 按调用方泛型返回成功响应。
  return (await response.json()) as T
}

// 获取服务端验证后的当前操作员。
export async function fetchCurrentUser(signal?: AbortSignal): Promise<PrincipalResponse> {
  // 传入取消信号，组件卸载或切学科时可中止请求。
  const result = await requestJson<PrincipalResponse>('/api/v1/me', { signal })
  // 身份端点不应返回 204。
  if (!result) {
    // 抛出协议错误而非伪造本地身份。
    throw new Error('当前用户响应为空')
  }
  // 返回可信主体。
  return result
}

// 获取当前学科的完整标签字典。
export async function fetchTaxonomy(subject: Subject, signal?: AbortSignal): Promise<TaxonomyResponse> {
  // 对查询参数进行 URL 编码。
  const result = await requestJson<TaxonomyResponse>(`/api/v1/taxonomy?subject=${encodeURIComponent(subject)}`, { signal })
  // 标签字典端点不应返回 204。
  if (!result) {
    // 抛出协议错误。
    throw new Error('标签字典响应为空')
  }
  // 返回类型化字典。
  return result
}

// 原子领取指定学科下一条任务。
export async function claimNextTask(subject: Subject, signal?: AbortSignal): Promise<TaskBundleResponse | null> {
  // 空队列会由 requestJson 转换成 null。
  return requestJson<TaskBundleResponse>(`/api/v1/tasks/next?subject=${encodeURIComponent(subject)}`, { signal })
}

// 强制刷新当前题目的模型建议。
export async function refreshPrediction(questionId: number, signal?: AbortSignal): Promise<TaskBundleResponse> {
  // force=true 创建新预测运行而不是复用快照。
  const result = await requestJson<TaskBundleResponse>(`/api/v1/questions/${questionId}/predict?force=true`, {
    // POST 表示创建预测运行。
    method: 'POST',
    // 支持取消慢模型请求。
    signal,
  })
  // 预测端点不应返回 204。
  if (!result) {
    // 抛出协议错误。
    throw new Error('预测响应为空')
  }
  // 返回最新任务与预测。
  return result
}

// 提交人工最终标签。
export async function submitAnnotation(questionId: number, payload: SubmitAnnotationRequest): Promise<AnnotationResponse> {
  // 调用幂等人工提交接口。
  const result = await requestJson<AnnotationResponse>(`/api/v1/tasks/${questionId}/submit`, {
    // POST 创建新的人工反馈事实。
    method: 'POST',
    // 将类型化命令序列化为 JSON。
    body: JSON.stringify(payload),
  })
  // 提交成功不应返回 204。
  if (!result) {
    // 抛出协议错误。
    throw new Error('提交响应为空')
  }
  // 返回人工标签和模型差异。
  return result
}
