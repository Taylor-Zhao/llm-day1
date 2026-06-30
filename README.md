# Day 1 - LLM Hands-on (2 hours)

中文说明：这是 Day 1 的最小实战项目，用 2 小时完成“能跑、能记录、能对比”。

This mini project gives you a runnable LLM CLI + experiment logging template.

## 1) Setup (15-20 min)

中文注释：创建虚拟环境并安装依赖，避免污染全局 Python。

```bash
cd /Users/zhaoyonggng/work/llm-day1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill `OPENAI_API_KEY`.

中文注释：如果你使用兼容网关（非官方端点），可同时配置 `OPENAI_BASE_URL`。

## 2) Run the CLI (15 min)

中文注释：先用默认参数跑通，确认“提问 -> 回复 -> 日志落盘”链路正常。

```bash
python chat_cli.py
```

Optional params:

中文注释：`temperature` 越低越稳定，越高越发散；Day 1 重点是对比差异。

```bash
python chat_cli.py --model gpt-4.1-mini --temperature 0.2 --max-tokens 400
```

## 3) Day 1 experiments (60-70 min)

中文注释：固定同一问题做多组实验，结论才有可比性。

Use one fixed question and run 3 comparisons.

Suggested baseline question:

"给我一个 Go HTTP 服务的错误日志排查清单，按优先级输出，并说明每一步预期现象。"

Try 3 variants:
1. Default system prompt + temperature 0.2
2. Default system prompt + temperature 0.7
3. Rewrite system prompt in `prompts/system_prompt_v1.txt`, then run again

中文注释：建议至少记录“准确性、结构化程度、可执行性”三项打分。

Fill notes in `experiments/experiment_template.md`.

## 4) Output requirements (10-15 min)

中文注释：这 3 个产出是 Day 1 完成标准，缺一项都不算完整闭环。

By end of Day 1, make sure you have:
1. Runnable CLI
2. JSONL logs in `logs/chat_history.jsonl`
3. Completed experiment notes in `experiments/experiment_template.md`

## 5) Troubleshooting

中文注释：先看鉴权和模型名，再看网络与代理配置，能覆盖 90% 启动问题。

- If API auth error: check `OPENAI_API_KEY`.
- If model not found: pass an available model via `--model`.
- If network error: verify your `OPENAI_BASE_URL` or internet/proxy settings.

## 6) Free model option (Ollama)

If your OpenAI account has no quota, you can run a free local model.

1. Install Ollama (https://ollama.com)
2. Pull a small model:

```bash
ollama pull qwen2.5:3b
```

3. Update `.env`:

```dotenv
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
OPENAI_MODEL=qwen2.5:3b
OPENAI_HTTP_PROXY=
```

4. Run:

```bash
python chat_cli.py
```

Note: local free models are usually weaker than cloud models, but good enough for Day 1 experiments.

## 7) One-command Day 1 experiment runner

You can run all 3 Day 1 comparisons in one command:

```bash
python run_day1_experiments.py
```

Generated files:
- `experiments/day1_run_results.md` (readable report)
- `logs/day1_experiments.jsonl` (raw records)

## 8) Day 2 - Parameter Sweep

Day 2 focuses on one thing: compare how `temperature` and `max_tokens` change output quality, length, and latency.

Run the Day 2 script:

```bash
python run_day2_parameter_sweep.py
```

Optional parameters:

```bash
python run_day2_parameter_sweep.py \
	--temperatures 0.1,0.3,0.7 \
	--max-tokens-list 150,300,450
```

Generated files:
- `experiments/day2_parameter_sweep.md` (readable report)
- `experiments/day2_parameter_sweep.csv` (summary table)
- `logs/day2_parameter_sweep.jsonl` (raw records)

What to observe:
1. Lower `temperature` usually makes answers more stable.
2. Higher `temperature` usually makes answers more diverse but easier to drift.
3. Larger `max_tokens` may improve completeness, but may also increase noise and latency.

## 9) Day 2 Notes - What `temperature` and `max_tokens` mean

### `temperature`

`temperature` controls how conservative or random the model is when choosing the next token.

- Lower `temperature` (for example `0.1` or `0.2`)
	- more stable
	- more deterministic
	- better for troubleshooting checklists, fixed formats, and structured answers

- Higher `temperature` (for example `0.7` or `1.0`)
	- more diverse
	- more creative
	- easier to drift away from the task

Practical recommendation for backend work:
- log analysis / troubleshooting: `0.2 ~ 0.3`
- structured output / checklist: `0.1 ~ 0.2`
- brainstorming: `0.7`

### `max_tokens`

`max_tokens` controls the maximum length of the generated answer.

- Smaller `max_tokens`
	- lower latency
	- lower cost
	- easier to truncate useful content

- Larger `max_tokens`
	- more complete output
	- higher latency
	- easier to become verbose or noisy

Practical recommendation for backend work:
- short answer / quick check: `100 ~ 200`
- checklist / explanation: `200 ~ 400`
- long comparison / detailed reasoning: `400+`

### How to read Day 2 results

When reading `experiments/day2_parameter_sweep.md`, compare each run from 3 angles:

1. Stability: does the answer stay on topic and follow the requested format?
2. Completeness: is the answer too short, or does it cover enough useful steps?
3. Cost-performance: is the extra latency worth the extra content?

Typical pattern:
- increasing `temperature` changes style and stability
- increasing `max_tokens` changes length and latency
- the best setting is usually not the longest answer, but the most stable answer that is still complete enough

## 10) Day 3 - Backend Assistant (Log + Code)

Day 3 goal: build one practical backend assistant with 2 modes.

### Mode A: Log Analysis

```bash
python run_day3_backend_assistant.py \
	--mode log-analysis \
	--input-file inputs/day3_sample.log
```

### Mode B: Code Explain

```bash
python run_day3_backend_assistant.py \
	--mode code-explain \
	--input-file inputs/day3_sample_code.go
```

Optional parameters:

```bash
python run_day3_backend_assistant.py \
	--mode log-analysis \
	--input-file inputs/day3_sample.log \
	--question "优先给最小修复方案" \
	--temperature 0.2 \
	--max-tokens 700
```

Generated files:
- `experiments/day3_backend_assistant.md` (readable report)
- `logs/day3_backend_assistant.jsonl` (raw records)
