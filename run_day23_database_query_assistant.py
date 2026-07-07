#!/usr/bin/env python3
"""Day 23：数据库查询助手（自然语言转 SQL，带安全限制）。"""

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from dotenv import load_dotenv

from chat_cli import build_client, load_system_prompt, normalize_usage, utc_now_iso

PROJECT_DIR = Path(__file__).resolve().parent
WRITE_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "replace",
    "truncate",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "reindex",
}

DB_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS customers (
        customer_id INTEGER PRIMARY KEY,
        customer_name TEXT NOT NULL,
        city TEXT NOT NULL,
        tier TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL,
        order_date TEXT NOT NULL,
        status TEXT NOT NULL,
        total_amount REAL NOT NULL,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payments (
        payment_id INTEGER PRIMARY KEY,
        order_id INTEGER NOT NULL,
        paid_amount REAL NOT NULL,
        payment_method TEXT NOT NULL,
        paid_at TEXT NOT NULL,
        status TEXT NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(order_id)
    )
    """,
]

DB_SEED_SQL = [
    "DELETE FROM payments",
    "DELETE FROM orders",
    "DELETE FROM customers",
    "INSERT INTO customers(customer_id, customer_name, city, tier) VALUES (1, 'Alice', 'Shanghai', 'gold')",
    "INSERT INTO customers(customer_id, customer_name, city, tier) VALUES (2, 'Bob', 'Beijing', 'silver')",
    "INSERT INTO customers(customer_id, customer_name, city, tier) VALUES (3, 'Cathy', 'Hangzhou', 'gold')",
    "INSERT INTO customers(customer_id, customer_name, city, tier) VALUES (4, 'David', 'Shenzhen', 'bronze')",
    "INSERT INTO orders(order_id, customer_id, order_date, status, total_amount) VALUES (101, 1, '2026-07-01', 'paid', 1200.0)",
    "INSERT INTO orders(order_id, customer_id, order_date, status, total_amount) VALUES (102, 2, '2026-07-02', 'paid', 860.0)",
    "INSERT INTO orders(order_id, customer_id, order_date, status, total_amount) VALUES (103, 3, '2026-07-03', 'pending', 1500.0)",
    "INSERT INTO orders(order_id, customer_id, order_date, status, total_amount) VALUES (104, 1, '2026-07-04', 'paid', 640.0)",
    "INSERT INTO orders(order_id, customer_id, order_date, status, total_amount) VALUES (105, 4, '2026-07-05', 'paid', 420.0)",
    "INSERT INTO payments(payment_id, order_id, paid_amount, payment_method, paid_at, status) VALUES (1001, 101, 1200.0, 'credit_card', '2026-07-01 10:00:00', 'settled')",
    "INSERT INTO payments(payment_id, order_id, paid_amount, payment_method, paid_at, status) VALUES (1002, 102, 860.0, 'alipay', '2026-07-02 09:30:00', 'settled')",
    "INSERT INTO payments(payment_id, order_id, paid_amount, payment_method, paid_at, status) VALUES (1003, 104, 640.0, 'wechat', '2026-07-04 14:20:00', 'settled')",
    "INSERT INTO payments(payment_id, order_id, paid_amount, payment_method, paid_at, status) VALUES (1004, 105, 420.0, 'credit_card', '2026-07-05 16:10:00', 'settled')",
]

CURRENT_DB_PATH: Optional[Path] = None
CURRENT_ROW_LIMIT = 20


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Day23 database query assistant")
    parser.add_argument(
        "--question",
        default="找出最近已支付订单金额最高的 3 位客户",
        help="自然语言查询问题",
    )
    parser.add_argument(
        "--system-prompt",
        default="prompts/day23_database_query_assistant_cn.txt",
        help="系统提示词路径",
    )
    parser.add_argument("--database", default="data/day23_demo.sqlite3", help="SQLite 数据库路径")
    parser.add_argument("--temperature", type=float, default=0.0, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=700, help="输出 token 上限")
    parser.add_argument("--max-tool-rounds", type=int, default=6, help="最多工具调用轮数")
    parser.add_argument("--row-limit", type=int, default=20, help="单次查询最大返回行数")
    parser.add_argument("--model", default="", help="可选：覆盖环境变量中的模型名")
    parser.add_argument(
        "--report",
        default="experiments/day23_database_query_assistant.md",
        help="Markdown 报告",
    )
    parser.add_argument(
        "--jsonl",
        default="logs/day23_database_query_assistant.jsonl",
        help="逐步日志 JSONL",
    )
    return parser.parse_args()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def ensure_demo_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        for stmt in DB_SCHEMA_SQL:
            cur.execute(stmt)
        for stmt in DB_SEED_SQL:
            cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def open_readonly_connection() -> sqlite3.Connection:
    if CURRENT_DB_PATH is None:
        raise RuntimeError("database path is not initialized")
    uri = f"file:{CURRENT_DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def list_tables() -> dict[str, Any]:
    conn = open_readonly_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return {"tables": [row["name"] for row in rows]}
    finally:
        conn.close()


def build_schema_summary() -> str:
    table_names = list_tables().get("tables") or []
    lines: list[str] = []
    for table_name in table_names:
        desc = describe_table(table_name)
        columns = desc.get("columns") or []
        column_parts = [f"{item['name']}:{item['type']}" for item in columns]
        lines.append(f"- {table_name}({', '.join(column_parts)})")
    return "\n".join(lines)


def describe_table(table_name: str) -> dict[str, Any]:
    conn = open_readonly_connection()
    try:
        safe_name = table_name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", safe_name):
            return {"table_name": table_name, "found": False, "message": "invalid table name"}
        rows = conn.execute(f"PRAGMA table_info({safe_name})").fetchall()
        if not rows:
            return {"table_name": table_name, "found": False, "message": "table not found"}
        columns = [
            {
                "name": row["name"],
                "type": row["type"],
                "notnull": bool(row["notnull"]),
                "pk": bool(row["pk"]),
            }
            for row in rows
        ]
        return {"table_name": table_name, "found": True, "columns": columns}
    finally:
        conn.close()


def validate_readonly_sql(sql: str) -> str:
    candidate = sql.strip()
    if not candidate:
        raise ValueError("empty sql")
    if candidate.endswith(";"):
        candidate = candidate[:-1].strip()
    if ";" in candidate:
        raise ValueError("multiple statements are not allowed")
    lowered = candidate.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValueError("only SELECT / WITH queries are allowed")
    for keyword in WRITE_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lowered):
            raise ValueError(f"forbidden keyword detected: {keyword}")
    return candidate


def run_readonly_sql(sql: str, limit: int = 20) -> dict[str, Any]:
    candidate = validate_readonly_sql(sql)
    safe_limit = max(1, min(int(limit), CURRENT_ROW_LIMIT))
    wrapped_sql = f"SELECT * FROM ({candidate}) LIMIT {safe_limit}"
    conn = open_readonly_connection()
    try:
        rows = conn.execute(wrapped_sql).fetchall()
        data_rows = [dict(row) for row in rows]
        return {
            "sql": candidate,
            "applied_limit": safe_limit,
            "row_count": len(data_rows),
            "rows": data_rows,
        }
    finally:
        conn.close()


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "查看当前数据库中的可查询表。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "查看某张表的字段定义。",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "description": "表名，例如 customers、orders"}
                },
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_readonly_sql",
            "description": "执行只读 SQL 查询，并限制返回行数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "只读 SQL 查询"},
                    "limit": {"type": "integer", "description": "最大返回行数"},
                },
                "required": ["sql"],
            },
        },
    },
]


TOOL_IMPLS: dict[str, Callable[..., dict[str, Any]]] = {
    "list_tables": list_tables,
    "describe_table": describe_table,
    "run_readonly_sql": run_readonly_sql,
}


def chat_once_with_tools(
    *,
    client: httpx.Client,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    tools: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    resp = client.post(url, headers=headers, json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    message = data.get("choices", [{}])[0].get("message", {})
    return message, normalize_usage(data)


def execute_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    function_obj = tool_call.get("function") or {}
    name = function_obj.get("name") or ""
    raw_args = function_obj.get("arguments") or "{}"
    try:
        kwargs = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        return {"tool_name": name, "ok": False, "error": f"invalid tool arguments: {exc}"}

    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return {"tool_name": name, "ok": False, "error": "unknown tool"}

    try:
        result = impl(**kwargs)
        return {"tool_name": name, "ok": True, "arguments": kwargs, "result": result}
    except Exception as exc:
        return {"tool_name": name, "ok": False, "arguments": kwargs, "error": str(exc)}


def add_usage(totals: dict[str, Optional[int]], usage: dict[str, Any]) -> None:
    for key in ["input_tokens", "output_tokens", "total_tokens"]:
        value = usage.get(key)
        if value is None:
            continue
        current = totals.get(key) or 0
        totals[key] = current + int(value)


def write_report(
    path: Path,
    *,
    args: argparse.Namespace,
    final_answer: str,
    steps: list[dict[str, Any]],
    usage_totals: dict[str, Optional[int]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Day 23 数据库查询助手报告",
        "",
        f"- 生成时间（UTC）：{utc_now_iso()}",
        f"- 模型：{args.model}",
        f"- 数据库：{args.database}",
        f"- 问题：{args.question}",
        f"- 行数上限：{args.row_limit}",
        "",
        "## 安全限制",
        "",
        "1. 仅允许 SELECT / WITH 查询。",
        "2. 禁止写操作和多语句执行。",
        "3. 以只读连接执行 SQLite。",
        "4. 对返回结果应用最大行数限制。",
        "",
        "## 最终回答",
        "",
        final_answer,
        "",
        "## 调用过程",
        "",
        "| Step | Type | Name | Summary |",
        "|---:|---|---|---|",
    ]

    for idx, step in enumerate(steps, start=1):
        if step["type"] == "assistant_tool_call":
            summary = json.dumps(step["tool_calls"], ensure_ascii=False)
            lines.append(f"| {idx} | assistant_tool_call | multiple | {summary} |")
        elif step["type"] == "tool_result":
            summary = json.dumps(step["payload"], ensure_ascii=False)[:240]
            lines.append(f"| {idx} | tool_result | {step['tool_name']} | {summary} |")
        else:
            summary = (step.get("content") or "").replace("\n", " ")[:160]
            lines.append(f"| {idx} | assistant_final | final_answer | {summary} |")

    lines.extend(
        [
            "",
            "## Token Usage",
            "",
            f"- input_tokens_total: {usage_totals['input_tokens']}",
            f"- output_tokens_total: {usage_totals['output_tokens']}",
            f"- total_tokens_total: {usage_totals['total_tokens']}",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    global CURRENT_DB_PATH, CURRENT_ROW_LIMIT

    load_dotenv(PROJECT_DIR / ".env")
    args = parse_args()

    database_path = resolve_project_path(args.database)
    report_path = resolve_project_path(args.report)
    jsonl_path = resolve_project_path(args.jsonl)
    system_prompt_path = resolve_project_path(args.system_prompt)

    ensure_demo_database(database_path)
    CURRENT_DB_PATH = database_path.resolve()
    CURRENT_ROW_LIMIT = max(1, min(args.row_limit, 50))

    system_prompt = load_system_prompt(system_prompt_path)
    model_name = args.model.strip() or (os.getenv("OPENAI_MODEL") or "").strip() or "qwen2.5:0.5b"
    args.model = model_name
    api_key, base_url, client = build_client()

    schema_summary = build_schema_summary()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "system",
            "content": (
                "当前 SQLite 数据库的真实表结构如下，请严格使用这些表名和字段名生成 SQL：\n"
                + schema_summary
            ),
        },
        {"role": "user", "content": args.question},
    ]

    steps: list[dict[str, Any]] = []
    usage_totals: dict[str, Optional[int]] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    final_answer = ""

    for round_idx in range(1, args.max_tool_rounds + 1):
        message, usage = chat_once_with_tools(
            client=client,
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            messages=messages,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            tools=TOOL_SPECS,
        )
        add_usage(usage_totals, usage)

        tool_calls = message.get("tool_calls") or []
        content = message.get("content") or ""

        if tool_calls:
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            step = {
                "timestamp_utc": utc_now_iso(),
                "round": round_idx,
                "type": "assistant_tool_call",
                "tool_calls": [
                    {
                        "id": item.get("id"),
                        "name": (item.get("function") or {}).get("name"),
                        "arguments": (item.get("function") or {}).get("arguments"),
                    }
                    for item in tool_calls
                ],
            }
            steps.append(step)
            append_jsonl(jsonl_path, step)

            for item in tool_calls:
                tool_name = (item.get("function") or {}).get("name") or "unknown"
                result_payload = execute_tool_call(item)
                tool_name = result_payload.get("tool_name") or tool_name
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": item.get("id"),
                        "name": tool_name,
                        "content": json.dumps(result_payload, ensure_ascii=False),
                    }
                )
                tool_step = {
                    "timestamp_utc": utc_now_iso(),
                    "round": round_idx,
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "payload": result_payload,
                }
                steps.append(tool_step)
                append_jsonl(jsonl_path, tool_step)
            continue

        final_answer = content.strip()
        final_step = {
            "timestamp_utc": utc_now_iso(),
            "round": round_idx,
            "type": "assistant_final",
            "content": final_answer,
        }
        steps.append(final_step)
        append_jsonl(jsonl_path, final_step)
        break

    if not final_answer:
        raise RuntimeError("model did not produce a final answer within max tool rounds")

    write_report(
        report_path,
        args=args,
        final_answer=final_answer,
        steps=steps,
        usage_totals=usage_totals,
    )

    print("Done. Day23 database query assistant generated.")
    print(f"Report => {args.report}")
    print(f"JSONL  => {args.jsonl}")


if __name__ == "__main__":
    main()