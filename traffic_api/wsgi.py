"""WSGI 入口，供 gunicorn / uwsgi 等生产服务器使用"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "traffic_api.settings")

application = get_wsgi_application()
