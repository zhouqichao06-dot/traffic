"""ASGI 入口（如后续使用通道/Daphne 可启用）"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "traffic_api.settings")

application = get_asgi_application()
