"""创建 SQLAlchemy Engine 与短生命周期 Session。"""  # 统一连接池配置并支持测试注入。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

from pathlib import Path  # 在 SQLite 本地模式下创建数据库父目录。
from typing import Any, Dict  # 为连接参数提供类型。

from sqlalchemy import create_engine  # 创建同步 SQLAlchemy Engine。
from sqlalchemy.engine import Engine  # 标注 Engine 返回类型。
from sqlalchemy.orm import sessionmaker  # 创建线程安全的 Session 工厂。


def build_engine(database_url: str) -> Engine:  # 根据 URL 创建适合环境的数据库引擎。
    connect_args: Dict[str, Any] = {}  # 初始化数据库驱动参数。
    engine_args: Dict[str, Any] = {"pool_pre_ping": True, "future": True}  # 开启断线检测和 2.0 风格 API。
    if database_url.startswith("sqlite:///"):  # 为本地文件 SQLite 创建目录并关闭线程限制。
        database_path = Path(database_url.removeprefix("sqlite:///"))  # 从 SQLAlchemy URL 提取文件路径。
        database_path.parent.mkdir(parents=True, exist_ok=True)  # 确保数据目录存在。
        connect_args["check_same_thread"] = False  # 允许 FastAPI 线程池共享 Engine。
    if database_url == "sqlite://":  # 处理测试使用的内存 SQLite。
        connect_args["check_same_thread"] = False  # 允许测试客户端跨线程访问。
        from sqlalchemy.pool import StaticPool  # 延迟导入仅内存测试需要的连接池。

        engine_args["poolclass"] = StaticPool  # 保持同一内存数据库连接。
    return create_engine(database_url, connect_args=connect_args, **engine_args)  # 返回已配置 Engine。


def build_session_factory(engine: Engine) -> sessionmaker:  # 创建请求或仓储使用的 Session 工厂。
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)  # 避免提交后 DTO 转换触发隐式查询。
