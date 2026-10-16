"""
固定 Token 鉴权（可配置）。

使用方法：请求头携带
    Authorization: Token <API_TOKEN>
或  Authorization: Bearer <API_TOKEN>
或  X-API-Key: <API_TOKEN>

Token 值从 settings.API_TOKEN 读取（.env 中配置 API_TOKEN 即可更换）。
"""
import secrets

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class FixedTokenUser:
    """不依赖数据库的轻量认证用户，仅用于通过 DRF 权限校验。"""

    is_active = True

    @property
    def is_authenticated(self):
        return True

    def __str__(self):
        return "api-token-user"


class FixedTokenAuthentication(BaseAuthentication):
    keyword = "Token"

    def authenticate(self, request):
        token = self._extract_token(request)
        if not token:
            # 未携带凭证：返回 None，交由权限类统一拒绝（返回 401/403）
            return None

        expected = getattr(settings, "API_TOKEN", "") or ""
        if not expected:
            raise AuthenticationFailed("服务端未配置 API_TOKEN，请在 .env 中设置。")
        if not secrets.compare_digest(token, expected):
            raise AuthenticationFailed("Token 无效或已过期。")

        return (FixedTokenUser(), token)

    def authenticate_header(self, request):
        # 让浏览器/客户端知道应当用什么认证方式
        return f'{self.keyword} realm="api"'

    @staticmethod
    def _extract_token(request):
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Token "):
            return auth[len("Token "):].strip()
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
        return (request.headers.get("X-API-Key") or "").strip() or None
