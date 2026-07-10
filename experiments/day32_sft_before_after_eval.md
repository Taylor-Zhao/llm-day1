# Day 32 - 微调前后效果对比（固定评测集）

- 生成时间（UTC）：2026-07-10T03:10:57.118215+00:00
- 评测集：data/day30_backend_sft_eval.jsonl
- 样本数：5
- 基础模型：HuggingFaceTB/SmolLM2-135M-Instruct
- LoRA Adapter：outputs/day31_sft_lora/adapter
- 评分规则：quality = 0.6 * section_score + 0.4 * keyword_hit_ratio

## 汇总结果

- base_avg_quality: 0.0533
- tuned_avg_quality: 0.2200
- delta_quality: +0.1667
- base_avg_section_score: 0.0000
- tuned_avg_section_score: 0.1000
- base_avg_keyword_hit: 0.1333
- tuned_avg_keyword_hit: 0.4000
- tuned_win_count: 3/5
- draw_count: 1/5

## 样本对比（前 5 条）

| id | base_score | tuned_score | delta |
|---|---:|---:|---:|
| day30-0107 | 0.0000 | 0.1333 | +0.1333 |
| day30-0027 | 0.1333 | 0.1333 | +0.0000 |
| day30-0018 | 0.1333 | 0.0000 | -0.1333 |
| day30-0003 | 0.0000 | 0.4167 | +0.4167 |
| day30-0105 | 0.0000 | 0.4167 | +0.4167 |

## 结论建议

1. 若 tuned_avg_quality 持续高于 base，说明 Day31 微调方向有效。
2. 若提升主要来自 section_score，说明格式对齐收益明显。
3. 若 keyword_hit 提升有限，可在 Day30 增加覆盖真实业务关键词的样本。