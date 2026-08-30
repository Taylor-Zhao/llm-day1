"""Prometheus 指标封装；业务代码只记录稳定、低基数标签。"""  # 禁止将 question_id、user_id 等高基数字段作为 label。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

from prometheus_client import CollectorRegistry, Counter, Histogram  # 使用 Prometheus 官方 Python 客户端。


class ServiceMetrics:  # 聚合 HTTP、预测、降级和人工反馈指标。
    def __init__(self) -> None:  # 为每个应用实例创建独立 Registry，便于测试隔离。
        self.registry = CollectorRegistry(auto_describe=True)  # 不使用全局 Registry，避免重复注册。
        self.http_requests = Counter("question_labeling_http_requests_total", "HTTP 请求总数", ["method", "route", "status"], registry=self.registry)  # 按稳定路由统计请求量。
        self.http_latency = Histogram("question_labeling_http_request_duration_seconds", "HTTP 请求延迟", ["method", "route"], registry=self.registry)  # 记录接口延迟分布。
        self.predictions = Counter("question_labeling_predictions_total", "模型预测运行总数", ["status"], registry=self.registry)  # 统计成功和失败预测。
        self.prediction_latency = Histogram("question_labeling_prediction_duration_seconds", "LangGraph 预测延迟", registry=self.registry)  # 记录完整模型链耗时。
        self.degradations = Counter("question_labeling_degradations_total", "模型链降级总数", ["component"], registry=self.registry)  # 统计 RAG 和 LLM 降级。
        self.annotations = Counter("question_labeling_annotations_total", "人工提交总数", registry=self.registry)  # 统计完成题目数量。
        self.annotation_changes = Counter("question_labeling_annotation_changes_total", "人工相对模型修改标签总数", ["change_type"], registry=self.registry)  # 统计新增和取消标签。

    def observe_prediction(self, latency_seconds: float, warnings: list[str]) -> None:  # 记录一次成功预测与降级信息。
        self.predictions.labels(status="success").inc()  # 增加成功预测计数。
        self.prediction_latency.observe(latency_seconds)  # 记录端到端模型链耗时。
        for warning in warnings:  # 遍历工作流返回的有限警告集合。
            component = "rag" if warning.startswith("RAG") else "llm" if warning.startswith("LLM") else "other"  # 映射为低基数组件标签。
            self.degradations.labels(component=component).inc()  # 增加对应降级计数。

    def observe_prediction_failure(self) -> None:  # 记录未产生可用结果的预测失败。
        self.predictions.labels(status="failed").inc()  # 增加失败预测计数。

    def observe_annotation(self, added_count: int, removed_count: int) -> None:  # 记录一次人工提交和修改量。
        self.annotations.inc()  # 增加人工提交计数。
        if added_count:  # 仅在存在新增标签时更新计数。
            self.annotation_changes.labels(change_type="added").inc(added_count)  # 记录模型漏标数量。
        if removed_count:  # 仅在存在移除标签时更新计数。
            self.annotation_changes.labels(change_type="removed").inc(removed_count)  # 记录模型误报数量。
