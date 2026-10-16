#!/usr/bin/env bash
# ============================================================
# Ubuntu 22.04 Django 服务一键部署脚本
# 前置：1) 项目已放到服务器（如 /opt/traffic_api）
#       2) 已执行 setup_mysql.sh 完成数据库初始化
#       3) 已配置 .env（cp .env.example .env 后修改）
# 用法：bash deploy/deploy.sh                    # 前台试运行
#       SYSTEMD=1 bash deploy/deploy.sh          # 注册 systemd 服务（环境变量方式）
#       bash deploy/deploy.sh SYSTEMD=1          # 注册 systemd 服务（参数方式，兼容）
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
VENV_DIR="${APP_DIR}/.venv"
SERVICE_NAME="traffic-api"

echo "==> 项目目录：${APP_DIR}"

echo "==> 检查 .env"
if [[ ! -f "${APP_DIR}/.env" ]]; then
    echo "  未找到 .env，从示例复制，请务必修改里面的密钥和 Token！"
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
fi

echo "==> 创建虚拟环境"
if [[ ! -d "${VENV_DIR}" ]]; then
    if ! "${PYTHON}" -m venv "${VENV_DIR}" 2>/dev/null; then
        echo ""
        echo "[错误] 创建虚拟环境失败。Ubuntu 22.04 通常缺少 python3-venv 包，请先执行："
        echo "  sudo apt-get update && sudo apt-get install -y python3-venv"
        echo "  rm -rf ${VENV_DIR}"
        echo "  bash deploy/deploy.sh ${*:-}"
        exit 1
    fi
fi
if [[ ! -f "${VENV_DIR}/bin/activate" ]]; then
    echo ""
    echo "[错误] 虚拟环境不完整（缺少 .venv/bin/activate）。请执行："
    echo "  rm -rf ${VENV_DIR}"
    echo "  sudo apt-get install -y python3-venv   # 如尚未安装"
    echo "  bash deploy/deploy.sh ${*:-}"
    exit 1
fi
source "${VENV_DIR}/bin/activate"

echo "==> 安装依赖"
pip install --upgrade pip
pip install -r "${APP_DIR}/requirements.txt"
pip install -r "${APP_DIR}/requirements-prod.txt"

echo "==> Django 自检"
python "${APP_DIR}/manage.py" check

echo "==> 数据库迁移（初始化 Django 系统表）"
python "${APP_DIR}/manage.py" migrate --noinput

echo "==> 收集静态文件"
python "${APP_DIR}/manage.py" collectstatic --noinput || true

if [[ "${SYSTEMD:-0}" == "1" || "${1:-}" == "SYSTEMD=1" ]]; then
    echo "==> 注册 systemd 服务（${SERVICE_NAME}）"
    sed "s|__APP_DIR__|${APP_DIR}|g" "${APP_DIR}/deploy/traffic-api.service" \
        > "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    systemctl enable --now "${SERVICE_NAME}"
    sleep 2
    systemctl status "${SERVICE_NAME}" --no-pager | head -10 || true
    echo "==> 部署完成，服务已通过 systemd 启动：http://<服务器IP>:8000/api/v1/health/"
else
    echo "==> 部署完成。前台试运行（Ctrl+C 停止）："
    echo "   cd ${APP_DIR} && source .venv/bin/activate && \\"
    echo "   python manage.py runserver 0.0.0.0:8000"
fi
