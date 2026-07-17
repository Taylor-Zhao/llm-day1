# Day36-Day38 统一服务运行说明（FastAPI）

## 1) 安装依赖

```bash
cd /Users/zhaoyonggng/work/llm-day1
source .venv/bin/activate
pip install -r requirements_day36_day38.txt
```

## 2) 配置环境变量

```bash
export DAY36_API_KEYS="dev-api-key"
export DAY36_BASE_MODEL_ID="HuggingFaceTB/SmolLM2-135M-Instruct"
export DAY36_ADAPTER_DIR="outputs/day31_sft_lora/adapter"
export DAY36_INFERENCE_MODE="fp32"
export DAY36_PORT="8070"

# Day37: 限流 + 重试
export DAY37_RATE_LIMIT_REQUESTS="30"
export DAY37_RATE_LIMIT_WINDOW_SEC="60"
export DAY37_RETRY_ATTEMPTS="3"
export DAY37_RETRY_BACKOFF_MS="200"
```

## 3) 启动服务

```bash
python run_day36_day38_unified_service.py
```

服务地址默认：`http://127.0.0.1:8070`

## 4) 快速验证

健康检查：

```bash
curl -s http://127.0.0.1:8070/healthz | jq .
```

推理接口：

```bash
curl -s -X POST "http://127.0.0.1:8070/v1/inference" \
  -H "Content-Type: application/json" \
  -H "x-api-key: dev-api-key" \
  -d '{
    "instruction": "请给出订单服务接口超时的排查步骤",
    "user_input": "环境：生产，偶发超时",
    "max_new_tokens": 180,
    "temperature": 0.0,
    "top_p": 1.0
  }' | jq .
```

监控指标：

```bash
curl -s http://127.0.0.1:8070/metrics | jq .
```

## 5) 接口与能力映射

- Day36（统一服务层）：
  - `POST /v1/inference`
- Day37（鉴权/限流/错误码/重试）：
  - 鉴权：`x-api-key`
  - 限流：滑动窗口内存限流
  - 错误码：`AUTH_MISSING` `AUTH_INVALID` `RATE_LIMITED` `UPSTREAM_INFERENCE_FAILED` `INTERNAL_ERROR`
  - 重试：指数退避重试
- Day38（日志与监控）：
  - 请求日志：request_id、path、status、latency
  - token 统计：prompt/completion token
  - 失败原因：按错误码聚合
  - 监控接口：`GET /metrics`

## 6) 常见问题

1. 缺少 FastAPI/Uvicorn：
   - 重新执行 `pip install -r requirements_day36_day38.txt`
2. 返回 `AUTH_INVALID`：
   - 检查请求头 `x-api-key` 与 `DAY36_API_KEYS` 是否一致
3. 返回 `RATE_LIMITED`：
   - 调大 `DAY37_RATE_LIMIT_REQUESTS` 或增加窗口时长
4. 推理失败 `UPSTREAM_INFERENCE_FAILED`：
   - 先检查模型和 adapter 路径是否存在，再看控制台日志中的 cause
