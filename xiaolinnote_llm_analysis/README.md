# Xiaolinnote 大模型工程 1-22 专题分析

本目录逐页分析 [Xiaolinnote 大模型工程面试题](https://xiaolinnote.com/ai/llm/)。每篇严格区分三类内容：

1. **网页理论**：保留原文主线，同时修正需要限定条件的说法。
2. **项目事实**：只描述仓库中确实存在、可以定位的代码。
3. **补充实现**：对框架内部机制或当前缺口提供可离线运行的教学实现，不冒充生产框架。

## 总知识图谱

- [大模型工程 1-22 总知识图谱](LLM_ENGINEERING_KNOWLEDGE_GRAPH.md)：将模型架构、训练对齐、推理优化、Prompt、可靠性、部署、评测和选型放到一张 Mermaid 图中，并标注跨主题关系与常见混淆点。

## 代码入口

- [Day1 LLM CLI](../chat_cli.py)：OpenAI-compatible 请求、Temperature、Token Usage 和 JSONL 日志。
- [Day11 引用问答](../run_day11_kb_qa_with_citations.py)：基于检索上下文回答、引用校验和失败重试。
- [Day16 离线评测](../run_day16_offline_eval.py)：命中率、引用正确率、拒答率和业务评测报告。
- [Day29 LoRA/QLoRA 笔记](../run_day29_lora_qlora_notes.py)：训练资源量级估算。
- [Day30 数据构造](../run_day30_build_instruction_dataset.py)：指令数据生成和 train/eval 拆分。
- [Day31 SFT](../run_day31_sft_lora_light.py)：Tokenizer、LoRA/QLoRA、NF4、TRL SFTTrainer。
- [Day32 前后评测](../run_day32_sft_before_after_eval.py)：基础模型与 Adapter 的固定集对比。
- [Day33 推理加速](../run_day33_inference_acceleration_comparison.py)：FP32、动态 INT8、批处理和并发基准。
- [Day34 统一推理引擎](../run_day34_unified_inference_api.py)：Transformers 模型加载、生成和 Adapter 接入。
- [Day36-Day38 服务](../run_day36_day38_unified_service.py)：FastAPI、认证、限流、重试和可观测性。
- [LLM 算法参考实现](examples/llm_algorithms_reference.py)：纯标准库实现 Attention、位置编码、BPE、Scaling、LoRA、DPO/GRPO、采样、缓存、量化、MoE、评测与选型。
- [算法参考测试](../tests/test_llm_algorithms_reference.py)：20 个确定性离线测试。

> 边界：参考模块用于解释公式和控制流，不包含自动求导、GPU Kernel、分布式训练、官方 Tokenizer 兼容性，也不替代 PyTorch、Transformers、FlashAttention、vLLM 或 SGLang。

## 文档索引

| 编号 | 网页标题与功能 | 分析文档 |
| --- | --- | --- |
| 1 | 什么是 LLM，与传统 NLP 的区别 | [统一生成接口与工程边界](1_what_is_llm_and_traditional_nlp.md) |
| 2 | Transformer、Encoder、Decoder | [Attention 与架构变体](2_transformer_encoder_decoder_attention.md) |
| 3 | MHA、MQA、GQA、Flash Attention | [结构优化与 IO 优化](3_mha_mqa_gqa_flash_attention.md) |
| 4 | sin/cos、RoPE、ALiBi | [位置编码与长上下文](4_position_encoding_sinusoidal_rope_alibi.md) |
| 5 | Tokenizer 原理 | [BPE、Unigram 与工程影响](5_tokenizer_bpe_sentencepiece_engineering.md) |
| 6 | 大模型训练 | [预训练、SFT 与对齐](6_llm_training_pretraining_sft_alignment.md) |
| 7 | Scaling Law 与涌现 | [幂律、计算最优与评测争议](7_scaling_law_and_emergent_abilities.md) |
| 8 | 微调方案 | [全量、LoRA、QLoRA、SFT、DPO](8_finetuning_full_lora_qlora_sft_dpo.md) |
| 9 | LoRA | [低秩更新与部署](9_lora_low_rank_adaptation_and_deployment.md) |
| 10 | Post-Training | [RLHF、DPO、GRPO、拒绝采样](10_post_training_rlhf_dpo_grpo_rejection_sampling.md) |
| 11 | DPO 与 PPO | [离线偏好和在线策略优化](11_dpo_vs_ppo_preference_alignment.md) |
| 12 | 解码策略 | [贪心、Beam Search 与采样](12_decoding_greedy_beam_sampling.md) |
| 13 | Temperature、Top-P、Top-K | [概率重整与截断](13_temperature_top_p_top_k_sampling.md) |
| 14 | KV Cache 与 Prompt Caching | [单请求增量与跨请求复用](14_kv_cache_and_prompt_caching.md) |
| 15 | INT8、INT4、AWQ、GPTQ | [量化算法与 Kernel 选型](15_quantization_int8_int4_awq_gptq_nf4.md) |
| 16 | Prompt Engineering | [结构化、测试与版本迭代](16_prompt_engineering_testing_and_iteration.md) |
| 17 | CoT | [工作草稿、验证与 Self-Consistency](17_chain_of_thought_and_self_consistency.md) |
| 18 | 幻觉 | [成因、校准、Grounding 与核查](18_hallucination_causes_mitigation_grounding.md) |
| 19 | MoE | [Router、专家与负载均衡](19_moe_router_experts_load_balancing.md) |
| 20 | 部署框架 | [vLLM、SGLang、TGI、llama.cpp](20_deployment_vllm_tgi_llamacpp_sglang.md) |
| 21 | 能力评测 | [Benchmark、业务指标与线上闭环](21_llm_evaluation_benchmarks_business_metrics.md) |
| 22 | 模型选型 | [质量、成本、延迟、合规与路由](22_model_selection_routing_cost_compliance.md) |

```mermaid
flowchart LR
    A[数据与 Tokenizer] --> B[预训练 Transformer]
    B --> C[SFT / LoRA]
    C --> D[偏好与 RL 后训练]
    D --> E[量化与推理部署]
    E --> F[Prompt / RAG / Agent 应用]
    F --> G[离线评测与线上反馈]
    G --> A
```

推荐阅读顺序：先读 1-6 建立模型底座，再读 7-11 理解训练与对齐，读 12-15 理解推理效率，最后读 16-22 完成应用、部署、评测和选型闭环。