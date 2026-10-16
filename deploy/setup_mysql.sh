#!/usr/bin/env bash
# ============================================================
# Ubuntu 22.04 一键安装并初始化 MySQL，为 Django 创建库和用户
# 用法（root 或 sudo 执行）：
#   sudo bash deploy/setup_mysql.sh
# 可自定义：
#   DB_NAME=myapi DB_USER=myuser sudo bash deploy/setup_mysql.sh
# 脚本会把生成的数据库密码打印出来，请写入项目 .env。
# ============================================================
set -euo pipefail

DB_NAME="${DB_NAME:-traffic_api}"
DB_USER="${DB_USER:-traffic}"
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -hex 16)}"

echo "==> 更新 apt 索引"
apt-get update -y

echo "==> 安装 MySQL Server（含 systemd 服务）"
DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-server

echo "==> 启动并设置开机自启"
systemctl enable --now mysql
systemctl status mysql --no-pager | head -5 || true

echo "==> 等待 MySQL 就绪"
for i in $(seq 1 30); do
    if mysqladmin ping --silent 2>/dev/null; then break; fi
    sleep 1
done

echo "==> 创建数据库与专用用户"
mysql <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

echo ""
echo "======================================================"
echo " MySQL 初始化完成，请把以下内容写入项目 .env："
echo "   DB_NAME=${DB_NAME}"
echo "   DB_USER=${DB_USER}"
echo "   DB_PASSWORD=${DB_PASSWORD}"
echo "   DB_HOST=127.0.0.1"
echo "   DB_PORT=3306"
echo "======================================================"
echo "验证连接：mysql -u${DB_USER} -p -h127.0.0.1 ${DB_NAME}"
