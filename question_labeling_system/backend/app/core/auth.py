"""验证 JWT 并向业务层提供可信用户身份。"""  # 模型永远不能生成或覆盖当前用户 ID。

from __future__ import annotations  # 延迟类型解析以兼容 Python 3.9。

from dataclasses import dataclass  # 使用不可变主体对象。
from pathlib import Path  # 支持从挂载文件读取 JWT 公钥。
from typing import FrozenSet, Optional  # 标注角色集合和可选 Header。

import jwt  # 使用 PyJWT 验证 RS256 签名和标准声明。
from fastapi import Header, HTTPException, Request, status  # 使用 FastAPI 注入请求头与返回认证错误。

from app.core.config import Settings  # 导入已验证配置。


@dataclass(frozen=True)  # 冻结主体防止请求处理中被修改。
class UserPrincipal:  # 表示认证系统确认的用户身份。
    user_id: str  # 保存 JWT subject。
    roles: FrozenSet[str]  # 保存授权角色集合。


def _load_public_key(value: str) -> str:  # 支持公钥文本或 Secret 挂载文件路径。
    path = Path(value)  # 尝试将配置解释为路径。
    if value and "BEGIN PUBLIC KEY" not in value and path.exists():  # 仅当路径存在时读取文件。
        return path.read_text(encoding="utf-8")  # 返回 PEM 公钥文本。
    return value.replace("\\n", "\n")  # 支持环境变量中的转义换行。


def current_user(request: Request, authorization: Optional[str] = Header(default=None), x_debug_user: Optional[str] = Header(default=None), x_debug_roles: Optional[str] = Header(default=None)) -> UserPrincipal:  # 构造可信请求主体。
    settings: Settings = request.app.state.container.settings  # 从应用容器读取不可变配置。
    if settings.auth_disabled:  # 仅开发和测试允许调试身份。
        roles = frozenset(item.strip() for item in (x_debug_roles or "labeler,importer").split(",") if item.strip())  # 解析本地角色。
        return UserPrincipal(user_id=(x_debug_user or "local-reviewer").strip(), roles=roles)  # 返回本地身份并明确不用于生产。
    if not authorization or not authorization.startswith("Bearer "):  # 检查 Bearer Header。
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")  # 拒绝匿名请求。
    token = authorization.removeprefix("Bearer ").strip()  # 提取 JWT 文本。
    try:  # 验证签名、签发者、受众和过期时间。
        payload = jwt.decode(token, _load_public_key(settings.jwt_public_key), algorithms=["RS256"], audience=settings.jwt_audience, issuer=settings.jwt_issuer, options={"require": ["exp", "sub"]})  # 只允许固定非对称算法，防止算法降级。
    except jwt.PyJWTError as exc:  # 捕获所有安全验证失败。
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token") from exc  # 不向客户端泄漏具体验签细节。
    raw_roles = payload.get("roles", [])  # 读取角色声明。
    roles = frozenset(raw_roles if isinstance(raw_roles, list) else str(raw_roles).split())  # 兼容数组和空格分隔角色。
    return UserPrincipal(user_id=str(payload["sub"]), roles=roles)  # 返回可信主体。


def require_role(principal: UserPrincipal, role: str) -> None:  # 执行确定性角色授权。
    if role not in principal.roles:  # 检查所需角色。
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing role: {role}")  # 拒绝无权限调用。
