# Day 33 - 推理加速对比报告

- 生成时间（UTC）：2026-07-10T09:28:24.029405+00:00
- 样本数：2
- 基础模型：HuggingFaceTB/SmolLM2-135M-Instruct
- batch_size：2
- concurrency_workers：2

## 对比结果

| Mode | Total Seconds | Avg / Sample | Throughput |
|---|---:|---:|---:|
| base_serial_fp32 | 10.1517 | 5.0758 | 0.1970 |
| base_batch_fp32 | 6.5395 | 3.2697 | 0.3058 |
| base_serial_dynamic_int8 | 9.0831 | 4.5416 | 0.2202 |
| base_concurrent_fp32_x2 | 9.8990 | 4.9495 | 0.2020 |
| tuned_serial_fp32 | 13.5204 | 6.7602 | 0.1479 |

## 观察结论

- base_batch_fp32 相对 base_serial_fp32 的单样本速度倍率：1.55x
- base_serial_dynamic_int8 相对 base_serial_fp32 的单样本速度倍率：1.12x
- base_concurrent_fp32_x2 相对 base_serial_fp32 的单样本速度倍率：1.03x
- tuned_serial_fp32 相对 base_serial_fp32 的单样本速度倍率：0.75x
- 本次最快模式：base_batch_fp32。
- 动态量化主要用于 CPU 轻量实验；真实线上 GPU 场景可继续评估更成熟的量化方案。
- 批处理更适合离线批评测；并发更适合多请求场景，但单机 CPU 上未必线性提速。