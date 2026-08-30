# 使用受支持的 Python 3.11 精简镜像运行后端。
FROM python:3.11-slim AS runtime
# 禁止 Python 写入容器内字节码文件。
ENV PYTHONDONTWRITEBYTECODE=1
# 强制日志实时输出到容器标准输出。
ENV PYTHONUNBUFFERED=1
# 设置应用工作目录。
WORKDIR /app
# 创建无登录权限的低权限用户。
RUN groupadd --system app && useradd --system --gid app --home /app app
# 先复制依赖文件以复用 Docker 构建缓存。
COPY requirements.txt ./requirements.txt
# 安装锁定依赖并清理 pip 下载缓存。
RUN pip install --no-cache-dir --requirement requirements.txt
# 复制应用源码。
COPY app ./app
# 复制数据库迁移脚本。
COPY migrations ./migrations
# 复制 Alembic 配置。
COPY alembic.ini ./alembic.ini
# 创建只读挂载前的模型目录并设置权限。
RUN mkdir -p /app/artifacts && chown -R app:app /app
# 切换为非 root 用户运行服务。
USER app
# 声明 FastAPI 服务端口。
EXPOSE 8010
# 启动两个进程内 Worker；生产副本数由编排平台扩展。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
