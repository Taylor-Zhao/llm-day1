# Day 7 周总结（Week 1）

## 1. 本周目标与完成情况

本周目标：用 7 天完成一个从「能调用 LLM」到「可控输出」的后端实战小闭环。

完成情况（Day1 ~ Day6）：
1. Day1：完成 LLM CLI、日志落盘与实验模板。
2. Day2：完成参数实验脚本，支持结构化对比输出。
3. Day3：完成后端助手（日志分析 + 代码解释）。
4. Day4：完成回归评测与质量门禁（通过率、重试、多模型）。
5. Day5：完成接口文档生成器（函数签名 -> API 草稿）。
6. Day6：完成固定 JSON 输出控制（schema 校验 + 失败重试）。

## 2. 关键产物

脚本：
- `chat_cli.py`
- `run_day1_experiments.py`
- `run_day2_parameter_sweep.py`
- `run_day3_backend_assistant.py`
- `run_day4_regression_eval.py`
- `run_day5_api_doc_generator.py`
- `run_day6_json_output_control.py`

报告与数据：
- `experiments/day1_run_results.md`
- `experiments/day2_parameter_sweep.md`
- `experiments/day3_backend_assistant.md`
- `experiments/day4_regression_eval.md`
- `experiments/day5_api_doc_draft.md`
- `experiments/day6_json_output_control.md`
- `experiments/day6_structured_output.json`

## 3. 方法论沉淀

1. Prompt 要和评测绑定：只写 prompt 不做评测，改进很难量化。
2. 输出要可控：能直接消费的 JSON，比自然语言更适合工程链路。
3. 重试要有反馈：失败重试应带上具体校验错误，成功率更高。
4. 日志要全链路：保留 input/output tokens + latency，便于成本与性能分析。

## 4. 已知问题与下周建议

已知问题：
1. 某些模型会在结构化输出场景中夹杂解释文本，需要更强约束提示词。
2. 本地 Debug 偶发用到系统 Python，需优先使用 `.venv/bin/python`。

下周（Week 2）建议：
1. 从 Day8 开始切入 RAG：先做切分与 Embedding 对照实验。
2. 先做可用版再优化：V1 先实现检索可用，V2 再加重排与评测指标。
