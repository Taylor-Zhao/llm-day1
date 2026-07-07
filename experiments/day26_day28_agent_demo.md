# Day 26-28 Agent Demo Report（失败重试 / 审计日志 / 演示版）

- 生成时间（UTC）：2026-07-07T12:39:46.290816+00:00
- Trace ID：6bddf1408074
- 模型：qwen2.5:0.5b
- 问题：请完成接口联调：先识别可用 endpoint，再请求 order_id=1001，最后总结联调结果
- 最大步骤：6
- 单步最大重试：3
- 退避秒数：0.5
- 计划来源：llm
- 总结来源：llm
- 审计日志：/Users/zhaoyonggng/work/llm-day1/logs/day26_day28_agent_demo.audit.jsonl

## Day 26 - 失败重试与超时处理

- 工具重试总次数：0
- 失败步骤：无
- HTTP 工具对超时做了重试，LLM 计划/总结也有退避重试。

## Day 27 - 审计日志

- 每个步骤尝试都会写入 `logs/day26_day28_agent_demo.audit.jsonl`。
- 审计记录包含 trace_id、phase、event、step_index、attempt、latency_ms、error 等字段。

## Day 28 - Agent Demo

- 这个脚本可以直接作为演示入口：先规划，再执行工具，最后生成总结和报告。
- 发生超时或模型失败时，会回退到本地兜底总结，保证演示不被中断。

## Phase 1 - Analysis（执行计划）

```json
{
  "plan": [
    {
      "step": "识别可用 endpoint",
      "tool": "list_mock_endpoints",
      "args": {},
      "why": "首先需要确定哪些endpoint是可用的"
    },
    {
      "step": "请求 order_id=1001",
      "tool": "http_get",
      "args": {
        "order_id": 1001
      },
      "why": "然后根据order_id获取数据并进行下一步操作"
    },
    {
      "step": "总结联调结果",
      "tool": "http_post",
      "args": {},
      "why": "最后将联调结果以指定格式返回给用户"
    }
  ]
}
```

## Phase 2 - Tool Execution（步骤执行结果）

| Step | Tool | Ok | Attempts | Why |
|---:|---|---:|---:|---|
| 1 | list_mock_endpoints | 1 | 1 | 首先需要确定哪些endpoint是可用的 |
| 2 | http_get | 1 | 1 | 然后根据order_id获取数据并进行下一步操作 |
| 3 | http_post | 1 | 1 | 最后将联调结果以指定格式返回给用户 |

## Phase 3 - Summary（最终总结）

### 结论

用户的目标是完成接口联调，首先需要识别可用的endpoint，并请求order_id=1001。根据执行计划的结果，我们已经完成了第一个步骤。

### 关键请求

- **识别可用 endpoint**：通过`list_mock_endpoints`工具可以获取到哪些endpoint是可用的。
- **请求 order_id=1001**：使用`http_get`工具请求order_id=1001并返回数据。
- **总结联调结果**：最后将联调结果以指定格式返回给用户。

### 关键响应

- **识别可用 endpoint**：
  - `list_mock_endpoints`工具成功获取了endpoint的名称、方法和URL，以及描述等信息。
  
- **请求 order_id=1001**：
  - 使用`http_get`工具请求order_id=1001并返回数据。

- **总结联调结果**：
  - `http_post`工具成功请求order_id=1001，并返回了状态码503，表示服务暂时不可用。
  
### 下一步建议

- **进一步验证接口的可用性**：在执行计划中已经识别出一些endpoint是可用的。下一步可以尝试使用这些endpoint进行更多的测试和验证。
  - 可以通过调用这些endpoint来模拟实际业务场景，并观察返回结果是否符合预期。

- **优化联调流程**：
  - 在执行计划中，我们已经识别了哪些endpoint是可用的。接下来可以考虑将这个信息整合到后续的接口请求中，以便在联调过程中自动识别并使用这些endpoint。
  
- **集成测试和验证**：如果需要进一步验证接口的可用性，可以在实际业务场景下进行集成测试，并记录所有成功的和失败的案例。

通过以上步骤，我们可以更有效地完成接口联调任务。

## Token Usage

- input_tokens_total: 923
- output_tokens_total: 547
- total_tokens_total: 1470