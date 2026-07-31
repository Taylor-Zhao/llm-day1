# 2. LLM 如何学会调用工具：训练能力与运行时闭环

- 原文：[LLM 是如何学会调用外部工具的？](https://xiaolinnote.com/ai/tools/2_llm_tool_learning.html)
- 一句话结论：专项数据让模型学会产生正确调用结构，偏好优化改善“何时调用”；上线后仍由宿主完成真实执行。

## 1. 预训练为什么不够

预训练目标通常是下一 token 预测。模型可能在文本中见过 API 代码，但没有稳定学到“给定动态工具 Schema，必须输出平台规定的 `tool_calls` 结构，并在 Tool 结果回来后继续回答”。这需要专项对话格式和目标数据。

```mermaid
flowchart LR
    P[预训练语言能力] --> S[SFT工具调用样本]
    S --> C[会选择工具和填参数]
    C --> A[偏好优化/DPO/RLHF]
    A --> B[该调与不该调的边界]
    B --> R[运行时宿主闭环]
```

网页用“SFT 教怎么调、RLHF 教什么时候调”建立直觉是有效的，但工程上不应断言只有 PPO/RLHF 一条路线。DPO、RLAIF、拒绝采样、规则验证和厂商专有训练管线也可塑造工具选择行为。

## 2. 一条训练样本的结构

训练数据应包含完整角色链：System/Tools、User、Assistant Tool Call、Tool Result、Assistant Final。损失可只作用于 Assistant token，避免要求模型“预测”用户或真实工具输出。

```json
{
  "messages": [
    {"role": "user", "content": "payment 服务负责人是谁"},
    {"role": "assistant", "tool_calls": [{"name": "lookup_service_owner", "arguments": {"service": "payment"}}]},
    {"role": "tool", "content": "{\"owner\": \"finops-oncall\"}"},
    {"role": "assistant", "content": "负责人是 finops-oncall。"}
  ]
}
```

负样本同样关键：无需工具的问题、工具不存在、参数信息不足应先澄清、权限不足、超时、并行调用和多轮依赖。

## 3. 当前项目映射

- [run_day22_function_calling_basics.py](../run_day22_function_calling_basics.py) 展示训练后模型的运行时使用，不训练模型。
- [run_day30_build_instruction_dataset.py](../run_day30_build_instruction_dataset.py) 合成后端指令 JSONL 并拆分 train/eval。
- [run_day31_sft_lora_light.py](../run_day31_sft_lora_light.py) 用 TRL `SFTTrainer` 和 LoRA 训练。
- [run_day32_sft_before_after_eval.py](../run_day32_sft_before_after_eval.py) 固定评测集比较基础模型与 Adapter。

Day30 数据目标是后端结构化回答，不包含 Tools/Assistant Tool Call/Tool Result 角色链，所以项目虽然有 SFT 管线，但没有 Function Calling 专项训练实践。

## 4. 运行时仍要治理

训练只能提高调用决策概率，不能替代 [参考实现](examples/tooling_capabilities_reference.py) 中的确定性白名单、参数校验、批准和超时。即使模型在评测集达到 99% 参数准确率，剩余 1% 也不能直接获得删除文件权限。

模型选择工具可抽象为：

$$
P(action\mid user, history, tool\ schemas)
$$

训练改变这个分布，宿主策略则决定某个 action 是否允许执行。两层职责必须分开。

## 5. 如何评估

- Tool selection accuracy：该调用哪个工具。
- Argument exact/schema validity：参数是否正确且可校验。
- No-tool accuracy：无需工具时是否直接回答。
- Recovery rate：超时、错误和无权限后能否正确处理。
- Parallelism accuracy：独立调用是否并行，依赖调用是否保持顺序。
- End-to-end task success：执行真实/模拟工具后最终任务是否完成。

## 6. 模拟面试

**Q1：参数量大就会自然获得 Function Calling 吗？**  
A：不可靠。平台特定结构、动态 Schema 遵循和工具结果角色链需要专项训练与对齐。

**Q2：SFT 与偏好优化分别解决什么？**  
A：SFT 主要学习格式和示范行为；偏好优化进一步校准调用必要性、错误恢复和用户偏好。

**Q3：必须用 RLHF/PPO 吗？**  
A：不是。DPO、RLAIF、拒绝采样和规则反馈都可用，具体取决于数据与训练栈。

**Q4：为什么训练数据要有 no-tool 样本？**  
A：否则模型会学习过度调用，增加延迟、成本和副作用风险。

**Q5：训练好后为什么还要宿主校验？**  
A：模型输出是概率性的外部输入，安全约束必须由确定性代码执行。

## 7. 复习清单

- 能区分预训练、专项 SFT、偏好优化和运行时。
- 知道完整工具对话样本的角色结构。
- 能列出六类工具调用指标。
- 不把 Day30-Day32 说成已训练 Function Calling。