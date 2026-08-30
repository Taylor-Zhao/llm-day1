// 导入前后端共享语义对应的 TypeScript 类型。
import type {
  AnnotationResponse,
  ApiErrorPayload,
  PrincipalResponse,
  Subject,
  SubmitAnnotationRequest,
  TaskBundleResponse,
  TaxonomyResponse,
} from './types'

// 使用 Vite 环境变量支持同域部署或独立 API 域名。
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

// 在内存中保存由企业 OIDC/BFF 注入的访问令牌，避免写入 localStorage。
let accessToken = ''

// 暴露令牌设置入口，生产登录集成在应用启动后调用。
export function setAccessToken(token: string): void {
  // 只保存当前页面生命周期内的令牌。
  accessToken = token.trim()
}

// 定义包含状态码、机器错误码和请求 ID 的前端异常。
export class ApiRequestError extends Error {
  // 保存 HTTP 状态码。
  readonly status: number
  // 保存后端机器错误码。
  readonly code: string
  // 保存跨端排查请求 ID。
  readonly requestId: string

  // 从后端响应构造可展示异常。
  constructor(status: number, payload: ApiErrorPayload, fallbackRequestId: string) {
    // 优先使用业务 message，再使用字符串 detail。
    super(payload.message ?? (typeof payload.detail === 'string' ? payload.detail : `请求失败（HTTP ${status}）`))
    // 修复 Error 子类原型链。
    Object.setPrototypeOf(this, ApiRequestError.prototype)
    // 保存 HTTP 状态。
    this.status = status
    // 保存稳定错误码或通用回退码。
    this.code = payload.code ?? 'HTTP_ERROR'
    // 优先使用响应体请求 ID，再使用响应头 ID。
    this.requestId = payload.request_id ?? fallbackRequestId
  }
}

// 构造生产认证头和仅开发环境可用的调试身份头。
function authenticationHeaders(): Record<string, string> {
  // 有正式令牌时只发送 Bearer Token。
  if (accessToken) {
    // 返回标准 OAuth2 Authorization 头。
    return { Authorization: `Bearer ${accessToken}` }
  }
  // Vite 生产构建禁止发送可伪造调试身份。
  if (!import.meta.env.DEV) {
    // 返回空认证头，由同域 BFF Cookie 或后续 OIDC 集成负责身份。
    return {}
  }
  // 本地开发与后端 AUTH_DISABLED 配合使用。
  return {
    // 指定本地演示用户。
    'X-Debug-User': 'vue-reviewer',
    // 同时授予导入和补录角色。
    'X-Debug-Roles': 'labeler,importer',
  }
}

// 实现统一 JSON 请求、204 处理和错误映射。
async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T | null> {
  // 合并内容类型、认证头和调用方 Header。
  const headers = new Headers({
    // 所有写请求使用 JSON。
    'Content-Type': 'application/json',
    // 注入正式或本地开发认证信息。
    ...authenticationHeaders(),
    // 允许调用方覆盖默认 Header。
    ...(init.headers as Record<string, string> | undefined),
  })
  // 发起同域或配置域名请求。
  const response = await fetch(`${API_BASE_URL}${path}`, {
    // 默认携带同域 HttpOnly Cookie，便于 BFF 认证模式。
    credentials: 'include',
    // 传入调用方 method、body 和 AbortSignal。
    ...init,
    // 使用规范化后的 Headers。
    headers,
  })
  // 204 表示当前没有可领取任务。
  if (response.status === 204) {
    // 返回空值而不是尝试解析空响应体。
    return null
  }
  // 非成功状态统一解析错误。
  if (!response.ok) {
    // 读取后端 JSON；网关 HTML 错误时使用空对象回退。
    const payload = (await response.json().catch(() => ({}))) as ApiErrorPayload
    // 抛出包含请求追踪信息的类型化异常。
    throw new ApiRequestError(response.status, payload, response.headers.get('X-Request-ID') ?? '')
  }
  // 成功响应按调用方泛型解析。
  return (await response.json()) as T
}

// 获取服务端验证后的当前操作员身份。
export async function fetchCurrentUser(signal?: AbortSignal): Promise<PrincipalResponse> {
  // 调用统一身份端点并传递取消信号。
  const result = await requestJson<PrincipalResponse>('/api/v1/me', { signal })
  // 成功接口不应返回空值。
  if (!result) {
    // 暴露不符合契约的响应。
    throw new Error('当前用户响应为空')
  }
  // 返回可信主体。
  return result
}

// 获取当前学科标签字典。
export async function fetchTaxonomy(subject: Subject, signal?: AbortSignal): Promise<TaxonomyResponse> {
  // 调用标签字典接口并传递取消信号。
  const result = await requestJson<TaxonomyResponse>(`/api/v1/taxonomy?subject=${encodeURIComponent(subject)}`, { signal })
  // 成功接口不应返回空值。
  if (!result) {
    // 暴露不符合契约的响应。
    throw new Error('标签字典响应为空')
  }
  // 返回已类型化字典。
  return result
}

// 原子领取指定学科的下一条任务。
export async function claimNextTask(subject: Subject, signal?: AbortSignal): Promise<TaskBundleResponse | null> {
  // GET 接口在空队列时返回 null。
  return requestJson<TaskBundleResponse>(`/api/v1/tasks/next?subject=${encodeURIComponent(subject)}`, { signal })
}

// 强制刷新指定题目的模型建议。
export async function refreshPrediction(questionId: number, signal?: AbortSignal): Promise<TaskBundleResponse> {
  // 使用 force=true 产生新预测快照。
  const result = await requestJson<TaskBundleResponse>(`/api/v1/questions/${questionId}/predict?force=true`, {
    // 使用 POST 表示创建新的预测运行。
    method: 'POST',
    // 传递取消信号。
    signal,
  })
  // 成功接口不应返回空值。
  if (!result) {
    // 暴露服务端契约错误。
    throw new Error('预测响应为空')
  }
  // 返回最新任务和预测。
  return result
}

// 提交人工最终标签。
export async function submitAnnotation(questionId: number, payload: SubmitAnnotationRequest): Promise<AnnotationResponse> {
  // 调用幂等人工提交接口。
  const result = await requestJson<AnnotationResponse>(`/api/v1/tasks/${questionId}/submit`, {
    // 使用 POST 创建人工反馈记录。
    method: 'POST',
    // 将类型化请求序列化为 JSON。
    body: JSON.stringify(payload),
  })
  // 成功接口不应返回空值。
  if (!result) {
    // 暴露服务端契约错误。
    throw new Error('提交响应为空')
  }
  // 返回人工标注结果。
  return result
}
