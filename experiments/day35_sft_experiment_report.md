# Day 35 - 微调实验报告（结论 + 局限）

- 生成时间（UTC）：2026-07-10T09:28:46.633383+00:00
- Day31 基础模型：HuggingFaceTB/SmolLM2-135M-Instruct
- Day31 训练方式：LoRA

## 1) 训练结果概览

- train_loss: 1.9040589332580566
- eval_loss: 1.6894952058792114
- eval_mean_token_accuracy: 0.6388163616259893

## 2) 微调前后效果（Day32）

- base_avg_quality: 0.0533
- tuned_avg_quality: 0.2200
- delta_quality: +0.1667
- tuned_win_count: 3/5

## 3) 推理加速结果（Day33）

- best_mode: base_batch_fp32
- best_avg_seconds_per_sample: 3.2697
- speedup_vs_baseline: 1.55x

## 4) 结论

1. Day31 证明 LoRA 训练流程已跑通，能够稳定产出 adapter。
2. Day32 显示微调后模型在固定评测集上相对基础模型有可见提升。
3. Day33 说明推理加速不能只看一种手段，批处理、量化、并发需要结合场景权衡。
4. 整体上，Day29-Day35 已形成从概念、数据、训练、评测到推理优化的完整实验链路。

## 5) 局限

1. Day30 数据是模板化合成数据，不等同于真实线上问答分布。
2. Day32 当前评分属于启发式规则，还不是人工标注或更强评测器。
3. Day33 在本机 CPU/macOS 环境下验证的是轻量加速思路，不代表 GPU 生产环境结论。
4. 当前样本规模较小，提升结果需要在更大评测集上继续验证。