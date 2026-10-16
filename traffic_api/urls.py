"""URL 路由入口"""
from django.contrib import admin
from django.urls import include, path

from apps.core.admin_views import console_page

urlpatterns = [
    path("admin/", admin.site.urls),
    # 前端管理控制台页面（固定账号登录 + FixedRoute 增删改查）
    path("console/", console_page, name="console"),
    # RESTful API 统一挂在 /api/v1/ 下，方便后续按版本演进
    path("api/v1/", include("apps.core.urls")),
]
