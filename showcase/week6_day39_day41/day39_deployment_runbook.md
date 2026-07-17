# Day39 部署到云端或可公网访问环境

## 目标

把 Day36-Day38 的统一服务从本地开发形态推进到“外部可访问”的状态，至少具备：

1. 一个公网访问地址。
2. 一个稳定健康检查接口：`GET /healthz`。
3. 一个可鉴权调用的推理接口：`POST /v1/inference`。
4. 一个最小监控接口：`GET /metrics`。

## 路线选择

### 方案 A：本地服务 + 隧道公网暴露

适合：

1. 先快速演示。
2. 不想立刻上云。
3. 需要当天对外给同学/面试官展示。

步骤：

1. 本地启动服务：

```bash
cd /Users/zhaoyonggng/work/llm-day1
source .venv/bin/activate
python run_day36_day38_unified_service.py
```

2. 用 Cloudflare Tunnel 或 ngrok 暴露 8070 端口：

```bash
cloudflared tunnel --url http://127.0.0.1:8070
```

或：

```bash
ngrok http 8070
```

3. 对外提供如下地址：

- `GET https://<public-host>/healthz`
- `POST https://<public-host>/v1/inference`
- `GET https://<public-host>/metrics`

优点：

1. 启动快。
2. 适合 Day40 录演示视频。
3. 不需要先处理云端 GPU/模型下载等复杂度。

缺点：

1. 依赖本地机器在线。
2. 稳定性一般。
3. 不适合长期生产使用。

### 方案 B：Docker 化后部署到云主机

适合：

1. 你已经有云服务器。
2. 希望更接近真实上线流程。

#### 1) 构建镜像

```bash
cd /Users/zhaoyonggng/work/llm-day1
docker build -f Dockerfile.day39 -t llm-day39-service:latest .
```

#### 2) 本地容器验证

```bash
docker run --rm -p 8070:8070 \
  -e DAY36_API_KEYS=dev-api-key \
  -e DAY36_BASE_MODEL_ID=HuggingFaceTB/SmolLM2-135M-Instruct \
  -e DAY36_ADAPTER_DIR=outputs/day31_sft_lora/adapter \
  -e DAY36_INFERENCE_MODE=fp32 \
  llm-day39-service:latest
```

#### 3) 云主机部署思路

1. 准备一台可运行 Python/Torch 的 Linux 主机。
2. 安装 Docker。
3. 上传镜像或在服务器上直接 build。
4. 开放 8070 端口，或通过 Nginx 反代到 80/443。
5. 用 `/healthz` 做探活检查。

### 方案 C：PaaS 平台部署

适合：

1. 希望更快拿到公网地址。
2. 更关注演示与作品集，而不是运维细节。

可选平台：

1. Railway
2. Render
3. Fly.io
4. Hugging Face Spaces（如果后续改成更轻量服务形态）

注意事项：

1. 这类平台通常更适合 CPU 小模型或 API 转发型服务。
2. 如果服务启动时需要下载模型，冷启动会比较慢。
3. 若使用本地 adapter，需确保相关文件在部署产物中存在。

## 上线前检查清单

1. `GET /healthz` 返回 200。
2. `POST /v1/inference` 在带 `x-api-key` 时返回 200。
3. 不带 `x-api-key` 时返回 401。
4. 高频请求能触发 429，验证限流逻辑有效。
5. `GET /metrics` 能看到请求数、延迟、token 与失败原因统计。
6. 已替换 `DAY36_API_KEYS` 默认值，避免对外使用弱默认密钥。

## 建议结论

如果当前目标是 Day40 演示视频与求职展示，优先建议：

1. 本地服务启动。
2. 用 tunnel 暴露公网。
3. 录视频时现场演示 `/healthz`、`/v1/inference`、`/metrics`。

这样投入最小，但展示效果最好。
