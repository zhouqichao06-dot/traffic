"""核心应用路由"""
from django.urls import path

from . import admin_views, views

app_name = "core"

urlpatterns = [
    # 探活（无需鉴权）
    path("health/", views.health, name="health"),
    # 鉴权示例（需要 Token）
    path("ping/", views.ping, name="ping"),
    # 固定路线：POST 增加/替换，GET 列表（需要 Token）
    path("fixed-routes/", views.fixed_routes, name="fixed-routes"),
    # 管理控制台：登录（免鉴权）+ FixedRoute 增删改查（需要 Token）
    path("admin/login/", admin_views.admin_login, name="admin-login"),
    path("fixed-routes/manage/", admin_views.fixed_route_manage, name="fixed-route-manage"),
    path(
        "fixed-routes/manage/<int:pk>/",
        admin_views.fixed_route_detail,
        name="fixed-route-detail",
    ),
    # 管理控制台：批量导入（总表 Excel 解析 + CSV 校验入库，需要 Token）
    path(
        "fixed-routes/batch/excel/",
        admin_views.fixed_route_batch_excel,
        name="fixed-route-batch-excel",
    ),
    path(
        "fixed-routes/batch/csv/",
        admin_views.fixed_route_batch_csv,
        name="fixed-route-batch-csv",
    ),
]
