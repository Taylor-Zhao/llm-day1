"""通过 FastAPI TestClient 验证题目补录主业务闭环。"""  # 测试 HTTP 契约与真实应用服务协作。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

import unittest  # 使用标准库组织测试。
from dataclasses import replace  # 基于固定测试配置只替换 JWT 字段。
from datetime import datetime, timedelta, timezone  # 构造带有效过期时间的 JWT。

import jwt  # 使用与应用相同的 PyJWT 生成测试令牌。
from cryptography.hazmat.primitives import serialization  # 将 RSA 公钥编码为 PEM。
from cryptography.hazmat.primitives.asymmetric import rsa  # 生成测试专用 RSA 密钥对。
from fastapi.testclient import TestClient  # 在进程内调用 ASGI 应用。

from app.adapters.historical_model import BootstrapHistoricalModel  # 使用可解释本地模型避免网络。
from app.adapters.llm_reasoner import DisabledLabelReasoner  # 验证 LLM 降级仍可人工补录。
from app.adapters.rag import InMemoryEvidenceRetriever  # 使用本地相似题证据。
from app.bootstrap import AppContainer  # 导入不触发 Web 初始化的依赖容器。
from app.core.config import Settings  # 构造安全测试配置。
from app.core.metrics import ServiceMetrics  # 创建测试隔离指标 Registry。
from app.db.models import Base  # 创建内存数据库表。
from app.db.repositories import QuestionRepository  # 创建真实事务仓储。
from app.db.session import build_engine, build_session_factory  # 创建测试数据库连接。
from app.main import create_app  # 创建可注入依赖的 FastAPI 应用。
from app.seed_data import local_evidence  # 使用原创合成证据。
from app.services.application import LabelingApplicationService  # 创建真实用例服务。
from app.services.labeling_workflow import QuestionLabelingWorkflow  # 创建真实 LangGraph 工作流。


def test_settings() -> Settings:  # 返回不依赖环境变量的固定测试配置。
    return Settings(environment="test", database_url="sqlite://", auth_disabled=True, jwt_public_key="", jwt_issuer="question-labeling", jwt_audience="question-labeling-api", cors_origins=("http://localhost:5173",), historical_model_path="unused", historical_model_mode="bootstrap", rag_mode="memory", llm_mode="disabled", openai_api_key="", openai_base_url="http://localhost:11434/v1", openai_model="qwen2.5:3b", embedding_model="nomic-embed-text", qdrant_url="http://localhost:6333", qdrant_api_key="", qdrant_collection="test", selection_threshold=0.58, rag_limit=6, claim_lease_seconds=900)  # 明确列出所有配置避免测试受本机环境污染。


class LabelingApiTests(unittest.TestCase):  # 覆盖页面真实调用顺序。
    def setUp(self) -> None:  # 为每条测试创建独立应用与数据库。
        self.engine = build_engine("sqlite://")  # 创建可跨 TestClient 线程共享的内存 SQLite。
        Base.metadata.create_all(self.engine)  # 创建生产同构表结构。
        repository = QuestionRepository(build_session_factory(self.engine))  # 创建真实仓储。
        workflow = QuestionLabelingWorkflow(BootstrapHistoricalModel(), InMemoryEvidenceRetriever(local_evidence()), DisabledLabelReasoner())  # 创建无网络 LangGraph。
        self.service = LabelingApplicationService(repository, workflow, 900)  # 保存真实用例服务供 JWT 客户端复用。
        self.client = TestClient(create_app(AppContainer(test_settings(), self.service, ServiceMetrics())))  # 创建进程内 API 客户端和隔离指标。
        self.headers = {"X-Debug-User": "alice", "X-Debug-Roles": "labeler,importer"}  # 构造仅测试环境可用的调试身份。

    def tearDown(self) -> None:  # 测试结束后释放连接池。
        self.client.close()  # 关闭 ASGI 测试客户端。
        self.engine.dispose()  # 关闭内存数据库连接。

    def test_import_claim_review_and_idempotent_submit(self) -> None:  # 验证完整人工补录闭环。
        create_payload = {"question": {"external_id": "api-cn-1", "subject": "chinese", "stem": "请写出一个描写春天的成语：____。", "reference_answer": "春暖花开，其他合理答案均可。", "analysis": "开放型语言表达题", "source": "api-test"}, "predict_now": True}  # 构造上游导入请求。
        created = self.client.post("/api/v1/questions", json=create_payload, headers=self.headers)  # 导入并同步生成建议。
        self.assertEqual(created.status_code, 201, created.text)  # 确认创建成功。
        created_body = created.json()  # 解析创建响应。
        self.assertTrue(created_body["prediction_id"])  # 每次预测必须有追踪 ID。
        suggested = {item["code"]: item for item in created_body["prediction"]["suggestions"]}  # 建立标签映射。
        self.assertTrue(suggested["answer_not_unique"]["selected_by_default"])  # 开放答案应默认勾选。

        claimed = self.client.get("/api/v1/tasks/next?subject=chinese", headers=self.headers)  # 领取下一条语文任务。
        self.assertEqual(claimed.status_code, 200, claimed.text)  # 确认领取成功。
        claimed_body = claimed.json()  # 解析页面完整数据。
        question_id = claimed_body["task"]["id"]  # 保存任务主键。
        selected_tags = [item["code"] for item in claimed_body["prediction"]["suggestions"] if item["selected_by_default"]]  # 模拟前端默认选中标签。
        submit_payload = {"prediction_id": claimed_body["prediction_id"], "selected_tags": selected_tags, "note": "人工核验通过", "idempotency_key": "api-idempotency-001", "expected_version": claimed_body["task"]["version"]}  # 构造带版本和幂等键的提交。
        submitted = self.client.post(f"/api/v1/tasks/{question_id}/submit", json=submit_payload, headers=self.headers)  # 提交人工结果。
        retried = self.client.post(f"/api/v1/tasks/{question_id}/submit", json=submit_payload, headers=self.headers)  # 模拟客户端超时后的重试。
        self.assertEqual(submitted.status_code, 201, submitted.text)  # 首次提交成功。
        self.assertEqual(retried.status_code, 201, retried.text)  # 幂等重试也返回成功。
        self.assertEqual(submitted.json()["annotation_id"], retried.json()["annotation_id"])  # 两次响应必须指向同一记录。

    def test_taxonomy_requires_labeler_role(self) -> None:  # 验证标签字典受角色保护。
        denied = self.client.get("/api/v1/taxonomy?subject=math", headers={"X-Debug-User": "reader", "X-Debug-Roles": "importer"})  # 使用无 labeler 角色访问。
        allowed = self.client.get("/api/v1/taxonomy?subject=math", headers=self.headers)  # 使用合法补录角色访问。
        self.assertEqual(denied.status_code, 403)  # 无权限请求必须拒绝。
        self.assertEqual(allowed.status_code, 200)  # 合法请求应成功。
        self.assertTrue(any(item["code"] == "unit_required" for item in allowed.json()["tags"]))  # 数学标签应包含单位要求。

    def test_me_returns_server_verified_principal(self) -> None:  # 验证页面显示的是服务端身份而非硬编码用户。
        response = self.client.get("/api/v1/me", headers={"X-Debug-User": "verified-user", "X-Debug-Roles": "labeler,importer"})  # 使用测试环境调试身份调用。
        self.assertEqual(response.status_code, 200)  # 身份端点应成功。
        self.assertEqual(response.json(), {"user_id": "verified-user", "roles": ["importer", "labeler"]})  # 返回服务端确认且稳定排序的主体。

    def test_rs256_jwt_validates_issuer_audience_expiry_and_roles(self) -> None:  # 验证生产认证主路径。
        private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)  # 创建仅当前测试使用的 RSA 私钥。
        public_pem = private_key.public_key().public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo).decode("utf-8")  # 导出应用持有的公钥。
        secure_settings = replace(test_settings(), auth_disabled=False, jwt_public_key=public_pem)  # 关闭调试身份并配置公钥。
        secure_client = TestClient(create_app(AppContainer(secure_settings, self.service, ServiceMetrics())))  # 创建启用 JWT 的隔离应用。
        now = datetime.now(timezone.utc)  # 获取令牌签发基准时间。
        token = jwt.encode({"sub": "jwt-reviewer", "roles": ["labeler"], "iss": secure_settings.jwt_issuer, "aud": secure_settings.jwt_audience, "iat": now, "exp": now + timedelta(minutes=5)}, private_key, algorithm="RS256")  # 使用私钥签发完整标准声明。
        accepted = secure_client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})  # 使用有效令牌访问身份接口。
        rejected = secure_client.get("/api/v1/me", headers={"Authorization": "Bearer invalid-token"})  # 使用损坏令牌验证拒绝路径。
        secure_client.close()  # 关闭额外 TestClient。
        self.assertEqual(accepted.status_code, 200)  # 有效 RS256 令牌应通过。
        self.assertEqual(accepted.json()["user_id"], "jwt-reviewer")  # subject 应成为可信用户 ID。
        self.assertEqual(rejected.status_code, 401)  # 无效令牌必须拒绝。

    def test_metrics_use_route_templates_instead_of_question_ids(self) -> None:  # 验证监控标签不会随题目数量无限增长。
        response = self.client.get("/api/v1/taxonomy?subject=english", headers=self.headers)  # 产生一条受监控业务请求。
        metrics = self.client.get("/metrics")  # 抓取 Prometheus 指标。
        self.assertEqual(response.status_code, 200)  # 确认业务请求成功。
        self.assertEqual(metrics.status_code, 200)  # 确认指标端点可用。
        self.assertIn('route="/api/v1/taxonomy"', metrics.text)  # 指标应使用稳定路由模板。


if __name__ == "__main__":  # 允许直接执行该测试文件。
    unittest.main()  # 启动标准测试运行器。