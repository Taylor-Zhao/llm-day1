# Day 25-28 LangChain Agent Demo

- 生成时间（UTC）：2026-07-07T13:28:50.935292+00:00
- Trace ID：cbb544f8fc11
- 模型：qwen2.5:0.5b
- 问题：请完成接口联调：先识别可用 endpoint，再请求 order_id=1001，最后总结联调结果
- 计划来源：fallback
- 总结来源：llm
- 单步最大重试：3
- 总重试次数：0

## Day 25 - LangChain 计划生成

```json
{
  "plan": [
    {
      "step": "识别可用 endpoint",
      "tool": "list_mock_endpoints",
      "args": {},
      "why": "首先需要确认有哪些可联调的 endpoint。"
    },
    {
      "step": "请求 order_id=1001",
      "tool": "http_get",
      "args": {
        "order_id": 1001
      },
      "why": "使用 order_id=1001 读取查询接口结果。"
    },
    {
      "step": "总结联调结果",
      "tool": "http_post",
      "args": {},
      "why": "调用 POST 工具收束最终联调结论。"
    }
  ]
}
```

## Day 26 - 失败重试与超时处理

- 工具调用和 LLM 调用都经过 `retry_call()` 包装。
- 超时、连接错误、5xx 失败会触发退避重试。

## Day 27 - 审计日志

- LangChain callback 会记录 chain / llm / tool 事件。
- `logs/day25_day28_langchain_demo.audit.jsonl` 记录每次尝试的时间戳、阶段和错误。

## Day 28 - Agent Demo

- 这个脚本可以直接作为演示入口，展示计划、工具调用、审计和总结。

## Tool Results

| Step | Tool | Ok | Why |
|---:|---|---:|---|
| 1 | list_mock_endpoints | 1 | 首先需要确认有哪些可联调的 endpoint。 |
| 2 | http_get | 1 | 使用 order_id=1001 读取查询接口结果。 |
| 3 | http_post | 1 | 调用 POST 工具收束最终联调结论。 |

## Summary

### 结论

用户的目标是完成接口联调，首先需要识别可用的 endpoint。根据执行计划的结果，我们确认了有两个可用的 endpoint：`echo_get` 和 `status_check`。

接下来，我们将请求 order_id=1001 并总结联调结果。

### 关键请求

- **识别可用 endpoint**：
  - 使用 `list_mock_endpoints` 工具来获取可用的 endpoint。
  
- **请求 order_id=1001**：
  - 使用 `http_get` 工具发送一个 GET 请求到指定的 endpoint，参数为 `order_id: 1001`。

- **总结联调结果**：
  - 使用 `http_post` 工具接收并返回从 `status_check` 接口获取的结果。

### 关键响应

#### 发送请求
```json
{
  "tool_name": "list_mock_endpoints",
  "ok": true,
  "arguments": {},
  "result": "{\"endpoints\": [{\"name\": \"echo_get\", \"method\": \"GET\", \"url\": \"https://httpbin.org/get\", \"description\": \"回显 query 参数，适合联调查询型接口。\"}, {\"name\": \"echo_post\", \"method\": \"POST\", \"url\": \"https://httpbin.org/post\", \"description\": \"回显 JSON body，适合联调提交型接口。\"}, {\"name\": \"status_check\", \"method\": \"GET\", \"url\": \"https://httpbin.org/status/200\", \"description\": \"用于联调状态码检查。\"}]}",
  "attempts": 1
}
```

#### 发送 GET 请求
```json
{
  "tool_name": "http_get",
  "ok": true,
  "arguments": {"url": "https://httpbin.org/get", "params": {"order_id": 1001}},
  "result": "{\"url\": \"https://httpbin.org/get?order_id=1001\", \"status_code\": 503, \"content_type\": \"text/html\", \"body\": \"<html>\\r\\n<head><title>503 Service Temporarily Unavailable</title></head>\\r\\n<body>\\r\\n<center><h1>503 Service Temporarily Unavailable</h1></center>\\r\\n</body>\\r\\n</html>\\r\\n\"}",
  "attempts": 1
}
```

#### 发送 POST 请求
```json
{
  "tool_name": "http_post",
  "ok": true,
  "arguments": {"url": "https://httpbin.org/post", "json_body": {}},
  "result": "{\"url\": \"https://httpbin.org/post\", \"status_code\": 503, "content_type\": \"text/html\", \"body\": \"<html>\\r\\n<head><title>503 Service Temporarily Unavailable</title></head>\\r\\n<body>\\r\\n<center><h1>503 Service Temporarily Unavailable</h1></center>\\r\\n</body>\\r\\n</html>\\r\\n\"}",
  "attempts": 1
}
```

### 下