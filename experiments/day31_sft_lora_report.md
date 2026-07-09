# Day 31 - 小规模 SFT 训练报告（LoRA/QLoRA）

- 生成时间（UTC）：2026-07-09T10:09:19.835766+00:00
- 基础模型：HuggingFaceTB/SmolLM2-135M-Instruct
- 训练方式：LoRA
- 训练集样本数：48
- 评测集样本数：12
- epochs：1.0
- max_steps：20
- learning_rate：0.0002
- LoRA(r/alpha/dropout)：16/32/0.05
- 输出目录：/Users/zhaoyonggng/work/llm-day1/outputs/day31_sft_lora

## 训练指标

- train.epoch: 1.6666666666666665
- train.total_flos: 20475728363520.0
- train.train_loss: 1.9040589332580566
- train.train_runtime: 415.86
- train.train_samples_per_second: 0.192
- train.train_steps_per_second: 0.048

## 评测指标

- eval.epoch: 1.6666666666666665
- eval.eval_entropy: 1.7933312853177388
- eval.eval_loss: 1.6894952058792114
- eval.eval_mean_token_accuracy: 0.6388163616259893
- eval.eval_num_tokens: 30720.0
- eval.eval_runtime: 26.3757
- eval.eval_samples_per_second: 0.455
- eval.eval_steps_per_second: 0.455

## 结果说明

- 本报告用于证明 Day31 的 SFT 流程可运行，不代表最终最优效果。
- Day32 建议用固定评测集对比微调前后回答质量（格式遵循率/准确率/可执行性）。