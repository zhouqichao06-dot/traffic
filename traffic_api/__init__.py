import pymysql

# 让 Django 的 MySQL 后端直接使用 PyMySQL 驱动，
# 这样 Ubuntu 22.04 上无需编译 mysqlclient（依赖系统 libmysqlclient-dev）。
pymysql.install_as_MySQLdb()
