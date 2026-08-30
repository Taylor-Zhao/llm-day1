"""Alembic 在线与离线迁移环境。"""  # 生产只通过版本化迁移修改表结构。

from __future__ import annotations  # 延迟类型求值以兼容 Python 3.9。

import os  # 从环境读取数据库 URL。
from logging.config import fileConfig  # 加载 alembic.ini 日志配置。

from alembic import context  # 访问当前 Alembic 迁移上下文。
from sqlalchemy import engine_from_config, pool  # 创建迁移 Engine 并禁用持久连接池。

from app.db.models import Base  # 导入 ORM metadata 供差异检查使用。


config = context.config  # 获取 Alembic Config 对象。
if config.config_file_name is not None:  # 仅在存在 ini 文件时加载日志。
    fileConfig(config.config_file_name)  # 配置迁移日志。
database_url = os.getenv("DATABASE_URL", "sqlite:///../data/question_labeling.db")  # 本地默认 SQLite，生产必须注入 MySQL URL。
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))  # 转义 ConfigParser 百分号并注入连接地址。
target_metadata = Base.metadata  # 提供模型 metadata 以支持后续 autogenerate 检查。


def run_migrations_offline() -> None:  # 在不创建 Engine 时生成 SQL 脚本。
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True)  # 配置离线迁移上下文。
    with context.begin_transaction():  # 创建迁移事务边界。
        context.run_migrations()  # 执行版本脚本并输出 SQL。


def run_migrations_online() -> None:  # 连接真实数据库执行迁移。
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool, future=True)  # 迁移进程使用 NullPool，结束即释放连接。
    with connectable.connect() as connection:  # 打开单个数据库连接。
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, transaction_per_migration=True)  # 配置类型比较和逐版本事务。
        with context.begin_transaction():  # 开启迁移事务。
            context.run_migrations()  # 执行待应用版本。


if context.is_offline_mode():  # 根据 Alembic 命令模式选择入口。
    run_migrations_offline()  # 生成离线 SQL。
else:  # 在线模式连接数据库。
    run_migrations_online()  # 应用真实迁移。
