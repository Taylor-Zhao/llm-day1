# Python 速记（结合本项目）

这份速记只讲你当前项目里真实出现的语法和写法：
- [chat_cli.py](chat_cli.py)
- [run_day1_experiments.py](run_day1_experiments.py)

## 1. 导入（import）

```python
import argparse
import json
import os
from pathlib import Path
from dotenv import load_dotenv
```

要点：
- `import xxx`：导入整个模块。
- `from xxx import yyy`：只导入某个对象，使用更短。
- 三方库需要先 `pip install`，例如 `python-dotenv`、`httpx`。

## 2. 函数定义与类型注解

```python
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```

要点：
- `def` 定义函数。
- `-> str` 是返回类型注解，主要用于可读性与 IDE 提示，不是强校验。
- 参数也可以注解：

```python
def append_jsonl(path: Path, record: dict) -> None:
    ...
```

## 3. 变量与常量

```python
PROJECT_DIR = Path(__file__).resolve().parent
```

要点：
- Python 没有真正的常量，`PROJECT_DIR` 全大写是“约定为常量”。
- `__file__` 表示当前脚本文件路径。
- `Path(...).resolve().parent` 常用于拿脚本所在目录。

## 4. if/for 基础控制流

```python
if not api_key:
    raise RuntimeError("OPENAI_API_KEY is missing")

for run in runs:
    ...
```

要点：
- `if not x`：x 为空字符串/空列表/None/0 时为真。
- `for item in list`：遍历列表。

## 5. 字典与列表（最常用）

```python
payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ],
}
```

要点：
- `{}` 是字典，`[]` 是列表。
- LLM 请求体通常就是“字典 + 列表嵌套字典”。

## 6. f-string（字符串插值）

```python
url = f"{base_url}/chat/completions"
```

要点：
- 在字符串前加 `f`，可直接放变量：`{base_url}`。
- 可读性高，推荐默认使用。

## 7. 文件读写与 with

```python
with path.open("a", encoding="utf-8") as f:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")
```

要点：
- `with` 会自动关闭文件，防止资源泄漏。
- `"a"` 表示追加写入。
- JSONL 约定“一行一个 JSON 对象”。

## 8. 异常处理 try/except

```python
try:
    assistant_text = chat_once(...)
except Exception as exc:
    print(exc)
```

要点：
- 可能失败的网络调用放进 `try`。
- 进入 `except` 后可以根据错误文本做分支提示。

## 9. 命令行参数 argparse

```python
parser = argparse.ArgumentParser(description="Run Day 1 experiments")
parser.add_argument("--max-tokens", type=int, default=450)
args = parser.parse_args()
```

要点：
- `--max-tokens` 这种参数运行时可覆盖默认值。
- 访问方式：`args.max_tokens`。

运行示例：

```bash
python run_day1_experiments.py --max-tokens 300
```

## 10. Path 路径处理（避免 cwd 坑）

```python
def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path
```

要点：
- 这段逻辑可以避免“调试工作目录变化导致找不到文件”。
- 在 VS Code Debug 场景特别常见。

## 11. 入口写法

```python
if __name__ == "__main__":
    main()
```

要点：
- 作为脚本直接运行时才执行 `main()`。
- 被其他文件 `import` 时不会自动执行。

## 12. 你现在最该记住的 5 条

1. Python 日常开发最常用：`dict/list/for/if/try/with`。
2. 路径统一用 `Path`，不要手拼字符串路径。
3. 参数配置用 `argparse`，环境变量用 `.env`。
4. 网络请求必须有异常处理。
5. 日志用 JSONL，后续自动分析最省事。

## 13. 最短练习（5 分钟）

1. 在 [run_day1_experiments.py](run_day1_experiments.py) 把默认 `max_tokens` 改成 `300`。
2. 运行：

```bash
cd /Users/zhaoyonggng/work/llm-day1
source .venv/bin/activate
python run_day1_experiments.py
```

3. 打开 [experiments/day1_run_results.md](experiments/day1_run_results.md) 观察输出长度是否变短。
