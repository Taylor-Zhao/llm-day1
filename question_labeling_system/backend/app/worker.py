"""异步消费数据库预测任务，适合独立容器水平扩展。"""  # 不使用 Web 进程内 BackgroundTasks 执行慢模型调用。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import argparse  # 解析 Worker 启动参数。
import logging  # 输出队列处理日志。
import os  # 读取容器主机名作为默认 Worker ID。
import socket  # 获取当前实例主机名。
import time  # 队列为空时执行有限轮询间隔。

from app.bootstrap import build_container  # 复用 API 相同的模型、RAG 和数据库装配且不创建 Web 应用。
from app.core.config import Settings  # 读取并验证运行配置。


LOGGER = logging.getLogger("question_labeling.worker")  # 创建 Worker 日志命名空间。


def parse_args() -> argparse.Namespace:  # 定义可运维的 Worker 参数。
    parser = argparse.ArgumentParser(description="Run question label prediction worker")  # 创建命令行解析器。
    parser.add_argument("--worker-id", default=os.getenv("WORKER_ID", f"prediction-{socket.gethostname()}"), help="数据库租约中的 Worker 标识")  # 支持部署平台注入稳定实例 ID。
    parser.add_argument("--lease-seconds", type=int, default=120, help="单个预测任务租约秒数")  # 设置故障恢复租约。
    parser.add_argument("--max-attempts", type=int, default=3, help="单任务最大尝试次数")  # 设置有界重试预算。
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="队列为空时轮询间隔")  # 防止空队列忙等。
    parser.add_argument("--once", action="store_true", help="最多处理一个任务后退出，便于调试和定时任务")  # 支持一次性运行模式。
    return parser.parse_args()  # 返回解析参数。


def main() -> None:  # 启动 Worker 主循环。
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")  # 配置容器友好的标准日志。
    args = parse_args()  # 读取命令行参数。
    if args.lease_seconds <= 0 or args.max_attempts <= 0 or args.poll_seconds <= 0:  # 校验所有循环控制参数。
        raise ValueError("lease-seconds, max-attempts and poll-seconds must be positive")  # 阻止危险配置。
    container = build_container(Settings.from_env())  # 构建与 API 一致的生产依赖。
    LOGGER.info("prediction worker started worker_id=%s", args.worker_id)  # 记录启动实例。
    while True:  # 持续消费数据库队列，容器终止信号由进程管理器处理。
        consumed = container.service.process_next_prediction_job(args.worker_id, args.lease_seconds, args.max_attempts)  # 尝试处理一个任务。
        if args.once:  # 一次性模式无论队列是否为空都立即退出。
            return  # 返回成功退出码。
        if not consumed:  # 队列为空时才等待，避免降低吞吐。
            time.sleep(args.poll_seconds)  # 使用可配置间隔减少数据库压力。


if __name__ == "__main__":  # 允许 `python -m app.worker` 启动。
    main()  # 进入 Worker 主循环。