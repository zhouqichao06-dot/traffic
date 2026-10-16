"""
traffic_api Django 项目配置。

所有敏感/环境相关配置均通过环境变量或 .env 文件注入，可在 Ubuntu 上自由调整。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 尝试加载项目根目录下的 .env 文件（不存在时静默跳过）
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # 尚未安装 python-dotenv 时不影响配置加载
    pass

# ---------------------------------------------------------------- 基础设置
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-local-dev-only-change-me",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() in ("1", "true", "yes")

# 逗号分隔的域名白名单；留空则允许所有 Host（内网/公网 IP 直连时方便）
_raw_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "").strip()
ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.split(",") if h.strip()] or ["*"]

# ---------------------------------------------------------------- 应用与中间件
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 第三方
    "rest_framework",
    "corsheaders",
    # 业务应用
    "apps.core",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # 必须放在尽量靠前的位置
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "traffic_api.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "traffic_api.wsgi.application"

# ---------------------------------------------------------------- 数据库（MySQL）
# 默认连接 MySQL；如需在无 MySQL 的机器上临时启动，可设 DB_ENGINE=sqlite。
# Ubuntu 22.04 部署时，先运行 deploy/setup_mysql.sh 创建库和用户，再把这些值写入 .env。
if os.environ.get("DB_ENGINE", "mysql") == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("DB_NAME", "traffic_api"),
            "USER": os.environ.get("DB_USER", "traffic"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",
                # 连接超时、自动重连等生产参数
                "connect_timeout": 10,
            },
            "CONN_MAX_AGE": 60,
        }
    }

# ---------------------------------------------------------------- 认证与权限（固定 Token）
# 对外接口的固定 Token：部署后请在 .env 中设置 API_TOKEN 覆盖默认值。
API_TOKEN = os.environ.get("API_TOKEN", "")

# ---------------------------------------------------------------- 管理控制台（固定账号）
# 前端管理界面登录账号：部署后建议在 .env 中覆盖 ADMIN_PASSWORD。
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Xxxt@2026.")

REST_FRAMEWORK = {
    # 全局默认认证方式：固定 Token（可在视图上用 authentication_classes 覆盖）
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.core.authentication.FixedTokenAuthentication",
    ],
    # 全局默认权限：所有 API 默认需要认证
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",  # 浏览器调试方便
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# ---------------------------------------------------------------- 跨域（CORS）
# 接口会被互联网上的前端页面调用，默认放开跨域；
# 生产环境如需收紧，可把 CORS_ALLOW_ALL_ORIGINS 设为 false 并配置白名单。
CORS_ALLOW_ALL_ORIGINS = os.environ.get("CORS_ALLOW_ALL", "true").lower() in (
    "1",
    "true",
    "yes",
)
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "x-api-key",
    "x-requested-with",
]

# ---------------------------------------------------------------- 国际化与静态文件
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------- 生产安全（nginx HTTPS 反向代理）
# nginx 反代时必须开启：让 Django 识别 X-Forwarded-Proto=https（否则 request.is_secure() 为 False）
if os.environ.get("DJANGO_SECURE_PROXY_SSL_HEADER", "true").lower() in ("1", "true", "yes"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# 信任的 HTTPS 来源域名（逗号分隔；DRF 接口用 Token 鉴权不受 CSRF 影响，主要供 admin/表单使用）
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# 纯 API 场景可按需开启安全 Cookie（开启后 admin 等依赖 Session 的页面也会生效）
if os.environ.get("DJANGO_SECURE_COOKIES", "false").lower() in ("1", "true", "yes"):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
