# Week 1 Showcase - LLM Backend Assistant Demo

这是一个可展示的 Week 1 小项目包，目标是快速演示三类能力：
1. 函数签名生成 API 文档草稿（Day5）
2. 异常日志输出固定 JSON（Day6）
3. 失败重试与输出校验（Day6）

## 1) 目录说明

```text
week1_demo/
  README.md
  inputs/
    day5_function_signatures.txt
    day6_sample_incident.log
  outputs/
    day5_api_doc_draft.md
    day6_structured_output.json
    day6_json_output_control.md
```

## 2) 本地运行（2~3 分钟）

在项目根目录执行：

```bash
cd /Users/zhaoyonggng/work/llm-day1
source .venv/bin/activate

# Day5: 接口文档生成器
python run_day5_api_doc_generator.py \
  --input-file inputs/day5_function_signatures.txt

# Day6: 固定 JSON 输出 + 校验失败重试
python run_day6_json_output_control.py \
  --input-file inputs/day6_sample_incident.log \
  --max-attempts 3
```

## 3) 展示时建议顺序

1. 先打开 `outputs/day5_api_doc_draft.md`：说明从签名到文档草稿。
2. 再打开 `outputs/day6_structured_output.json`：说明输出可被程序直接消费。
3. 最后打开 `outputs/day6_json_output_control.md`：说明重试与校验闭环。

## 4) 技术亮点

1. 输出可控：固定 JSON schema + 自动校验。
2. 可回放：输入输出均有文件化样例，可复现实验。
3. 工程可迁移：脚本已具备日志、重试、参数化基础。
