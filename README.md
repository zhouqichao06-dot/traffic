# traffic_api — Django RESTful API 服务

面向 Ubuntu 22.04 部署的 Django 后端服务：**固定 Token 鉴权（可配置） + RESTful API（DRF）+ MySQL**。
已内置：探活、鉴权示例、**固定路线增/替业务接口**（`POST /api/v1/fixed-routes/`）。

## 目录结构

```
traffic_api/
├── manage.py                  # Django 管理入口
├── requirements.txt           # 基础依赖（Django/DRF/CORS/PyMySQL/dotenv）
├── requirements-prod.txt      # 生产依赖（gunicorn）
├── .env.example               # 环境配置模板（复制为 .env 后修改）
├── traffic_api/               # 项目配置包
│   ├── settings.py            # 全部配置：Token/MySQL/CORS/DRF
│   ├── urls.py                # 路由入口，API 统一挂 /api/v1/
│   ├── wsgi.py / asgi.py
├── apps/core/                 # 核心应用
│   ├── authentication.py      # 固定 Token 鉴权（核心）
│   ├── models.py              # FixedRoute 固定路线表
│   ├── views.py               # health / ping / fixed-routes 接口
│   ├── migrations/            # 数据库迁移
│   └── urls.py
└── deploy/
    ├── setup_mysql.sh         # Ubuntu 一键装 MySQL + 建库建用户
    ├── deploy.sh              # 一键部署（venv/依赖/迁移/启动）
    └── traffic-api.service    # systemd 服务模板（gunicorn）
```

## 一、本地快速验证（Windows/macOS 均可）

```bash
cd traffic_api
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # 默认 API_TOKEN=change-me-please-8f3a2c9d
# 本机没有 MySQL 时，先把 .env 里的 DB_ENGINE 改为 sqlite
python manage.py check
python manage.py runserver 0.0.0.0:8000
```

验证接口：

```bash
# 探活（无需 Token）
curl http://127.0.0.1:8000/api/v1/health/

# 鉴权示例（需要 Token，缺 Token 返回 401）
curl http://127.0.0.1:8000/api/v1/ping/
curl -H "Authorization: Token change-me-please-8f3a2c9d" http://127.0.0.1:8000/api/v1/ping/
curl -H "X-API-Key: change-me-please-8f3a2c9d" http://127.0.0.1:8000/api/v1/ping/
```

## 二、部署到 Ubuntu 22.04（含 MySQL）

前置：项目已上传到服务器（例如 `/opt/traffic_api`），Ubuntu 22.04 自带 Python 3.10。

### 1. 安装并初始化 MySQL（只需一次）

```bash
sudo bash deploy/setup_mysql.sh
```

脚本会：安装 `mysql-server` → 开机自启 → 创建数据库 `traffic_api` 和用户 `traffic`（密码随机生成），
并把结果打印出来。**把 `DB_PASSWORD` 填进 `.env`。**

### 2. 配置 .env

```bash
cp .env.example .env
vim .env   # 至少修改：API_TOKEN、DB_PASSWORD、DJANGO_SECRET_KEY
```

各配置项说明：

| 变量 | 说明 |
|---|---|
| `API_TOKEN` | 对外接口的固定 Token，客户端请求头携带，**务必改强随机值** |
| `DJANGO_SECRET_KEY` | Django 签名密钥，用 `openssl rand -hex 32` 生成 |
| `DJANGO_DEBUG` | 生产设为 `false` |
| `DJANGO_ALLOWED_HOSTS` | 逗号分隔域名/IP 白名单；留空 = 允许所有 |
| `DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT` | MySQL 连接参数 |
| `CORS_ALLOW_ALL` | 互联网前端跨域调用时保持 `true` |

### 3. 一键部署

```bash
sudo bash deploy/deploy.sh SYSTEMD=1
```

脚本完成：venv → 装依赖 → `manage.py check` → 迁移建表 → 注册 systemd 服务（gunicorn，监听 0.0.0.0:8000，开机自启）。

### 4. 验证

```bash
curl http://127.0.0.1:8000/api/v1/health/
# 公网访问（需云服务器安全组放行 8000 端口）
curl http://<服务器公网IP>:8000/api/v1/health/
curl -H "Authorization: Token <你的API_TOKEN>" http://<服务器公网IP>:8000/api/v1/ping/
```

常用运维命令：

```bash
sudo systemctl status traffic-api     # 查看状态
sudo journalctl -u traffic-api -f     # 实时日志
sudo systemctl restart traffic-api    # 重启
```

## 三、鉴权说明

固定 Token，全局生效，可通过 `.env` 的 `API_TOKEN` 随时更换。三种携带方式等价：

```
Authorization: Token <API_TOKEN>
Authorization: Bearer <API_TOKEN>
X-API-Key: <API_TOKEN>
```

未携带或 Token 错误 → `401`。后续业务接口默认继承该鉴权；
个别公开接口（如探活）用 `@authentication_classes([])` + `@permission_classes([AllowAny])` 放行。

## 四、固定路线接口（应用固定路线）

**`POST /api/v1/fixed-routes/`**（需要 Token）—— 接收原始路线数据，拿 `step` 分段去数据库 `fixed_route` 表匹配固定路线。匹配规则：**① 先按名称**（记录 `start_name` 在前、`end_name` 在后）；**② 再校验坐标**——记录的起点坐标与终点坐标必须**都包含**在请求 `points.coordinates` 中（任一点偏差 ≤ 0.0002° 即视为包含），**全部符合才替换**；匹配到则 `step` / `originalStep` / `uploadStep` 中的「起点…终点」段统一替换为数据库 `fixed_step`，`points` 坐标段整体替换为 `fixed_points.coordinates`，并追加 `fixedStep` / `fixedPoints`（匹配到的固定线路点位与坐标）与匹配标记 `iscsdl`（=1）；未匹配则原样返回（`iscsdl=0`）。

### 输入（仅原始路线，参考 数据.json 结构，字段平铺即可）

**请求体中不需要任何固定路线字段**（startName/endName/startPoint/endPoint/fixedStep/fixedPoints 一律不传），固定路线数据全部来自数据库。

| 字段 | 说明 | 是否必填 |
|---|---|---|
| `step` | 原始完整路线字符串（用 `-` 分段） | 必填 |
| `points.coordinates` | 原始路线点位（GeoJSON LineString） | 必填 |
| `originalStep` / `uploadStep` / `steps` 等 | 随原始数据原样传入 | 选填 |

### 数据库固定路线数据（fixed_route 表）

表结构（由 `migrate` 自动创建）：

| 字段 | 说明 | 示例 |
|---|---|---|
| `start_name` | 匹配段起点名称 | `扎下镇` |
| `end_name` | 匹配段终点名称（收费站等） | `江苏沭阳北收费站` |
| `start_point` | 起点坐标 JSON `[lng, lat]` | `[118.751556, 34.161008]` |
| `end_point` | 终点坐标 JSON `[lng, lat]` | `[118.743252, 34.16809]` |
| `fixed_step` | 固定路线字符串（内部 `-` 分隔） | `扎下镇-运河路-玉龙南路-龙城大道-江苏沭阳北收费站` |
| `fixed_points` | 固定路线坐标（LineString） | `{"type": "LineString", "coordinates": [[lng, lat], ...]}` |

插入/替换数据（`start_name + end_name` 唯一，重复插入即覆盖）：

```sql
INSERT INTO fixed_route
  (start_name, end_name, start_point, end_point, fixed_step, fixed_points, created_at, updated_at)
VALUES
  ('扎下镇', '江苏沭阳北收费站',
   '[118.751556, 34.161008]', '[118.743252, 34.16809]',
   '扎下镇-运河路-玉龙南路-龙城大道-江苏沭阳北收费站',
   '{"type": "LineString", "coordinates": [[118.751556, 34.161008], "...真实固定路线坐标...", [118.743252, 34.16809]]}',
   NOW(), NOW())
ON DUPLICATE KEY UPDATE
  start_point = VALUES(start_point),
  end_point   = VALUES(end_point),
  fixed_step  = VALUES(fixed_step),
  fixed_points = VALUES(fixed_points),
  updated_at  = NOW();
```

### 处理逻辑

1. 把 `step` 按 `-` 拆成段，逐条匹配数据库 `fixed_route`：记录的 `start_name` 在前、`end_name` 在后同时出现即命中；
2. **`step` / `originalStep` / `uploadStep` 处理一致**：各字段中「起点…终点」一段整段替换为数据库 `fixed_step`（固定段内部与前后段统一用 `-` 连接；请求中不存在的字段不新增）；
3. `points.coordinates` 中从 `start_point` 到 `end_point` 的坐标段整体替换为 `fixed_points.coordinates`（定位规则：精确匹配 ±1e-5，否则取欧氏最近点）；
4. 响应新增 `iscsdl`（匹配到固定线路=1，否则=0），并**追加 `fixedStep` / `fixedPoints`**（匹配到的固定线路点位与坐标，取第一条匹配记录；多段匹配时 `step` / `originalStep` / `uploadStep` / `points` 仍全部替换）；
5. 未匹配到任何固定路线时原样返回原始数据（`iscsdl=0`，不返回 `fixedStep` / `fixedPoints`）。

### 请求示例

```bash
curl -X POST http://<服务器IP>:8000/api/v1/fixed-routes/ \
  -H "Authorization: Token <你的API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d @request.json
```

`request.json`（只有原始路线字段）：

```json
{
  "step": "扎下镇-XZ04天津北路-G205山深线-江苏沭阳北收费站-G2-G30-G3-S69-江苏丰县北收费站-G237济宁线-G518-X208王宋线-徐州能动电动车工业园",
  "originalStep": "同上",
  "uploadStep": "同上",
  "steps": [],
  "points": {"type": "LineString", "coordinates": [[118.751556, 34.161008], "…原始坐标…"]}
}
```

### 响应（step/originalStep/uploadStep 统一替换 + iscsdl 匹配标记 + fixedStep/fixedPoints 命中数据）

```json
{
  "step": "扎下镇-运河路-玉龙南路-龙城大道-江苏沭阳北收费站-G2-G30-G3-S69-江苏丰县北收费站-G237济宁线-G518-X208王宋线-徐州能动电动车工业园",
  "originalStep": "扎下镇-运河路-玉龙南路-龙城大道-江苏沭阳北收费站-G2-G30-G3-S69-江苏丰县北收费站-G237济宁线-G518-X208王宋线-徐州能动电动车工业园",
  "uploadStep": "…（与 step 同样替换）",
  "steps": [],
  "regionDistName": "…（不变）",
  "endDistName": "…（不变）",
  "points": {"type": "LineString", "coordinates": ["固定路线坐标 + 原始路线剩余坐标"]},
  "iscsdl": 1,
  "fixedStep": "扎下镇-运河路-玉龙南路-龙城大道-江苏沭阳北收费站",
  "fixedPoints": {"type": "LineString", "coordinates": ["数据库中的固定路线坐标"]}
}
```

- `step` / `originalStep` / `uploadStep`：**处理一致**——匹配到的「起点…终点」段在各字段中整段替换为 `fixed_step`（固定段内部与前后段统一用 `-` 分隔）；请求中原本没有的字段不新增
- `iscsdl`：**是否匹配到固定线路**——匹配到返回 `1`，未匹配返回 `0`（未匹配时响应为原始数据原样返回，`iscsdl=0`，不返回 `fixedStep` / `fixedPoints`）
- `fixedStep`：**匹配到的固定线路点位**（string），`fixedPoints`：**匹配到的固定线路坐标**（object）——直接取用，无需再调 GET 接口；取第一条匹配记录，多段匹配时 `step` / `originalStep` / `uploadStep` / `points` 仍全部替换
- 校验失败返回 `400`，未带 Token 返回 `401`

**`GET /api/v1/fixed-routes/`**（需要 Token）—— 列出数据库中的全部固定路线，用于确认数据已入库。

## 五、管理控制台（前端页面）

**访问地址：`/console/`**（默认账号 `admin` / `Xxxt@2026.`，可在 `.env` 中通过 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 修改）。

登录后可以查看 `fixed_route` 表数据，并新增 / 修改 / 删除固定路线：

- **新增**：`start_name`（起点名称）、`end_name`（目标收费站）、`fixed_step`（固定段线路文本）手动输入；
  `fixed_points` 从 **CSV 文件导入**（格式：表头 `X,Y`，每行 `经度,纬度`，参考 `常州、泰州固定路线段` 目录下的 CSV）。
  导入后 `start_point` / `end_point` **自动取第一个 / 最后一个坐标**，无需手动输入，页面会实时预览。
- **修改**：可改任意字段；不重新导入 CSV 时坐标保持不变；重新导入后起终点坐标自动重算。
- **删除**：二次确认后删除。

### 管理接口（需要 Token，前端页面自动携带）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/admin/login/` | 固定账号登录（免鉴权），返回 `{"token": "<API_TOKEN>"}` |
| GET | `/api/v1/fixed-routes/manage/` | 固定路线列表（snake_case 全字段） |
| POST | `/api/v1/fixed-routes/manage/` | 新增；`fixed_points` 支持 LineString / 坐标数组 / CSV 文本 |
| PUT | `/api/v1/fixed-routes/manage/<id>/` | 修改；不传 `fixed_points` 则坐标保持不变 |
| DELETE | `/api/v1/fixed-routes/manage/<id>/` | 删除 |

新增示例（直接提交 CSV 文本即可）：

```bash
curl -X POST http://<服务器IP>:8000/api/v1/fixed-routes/manage/ \
  -H "Authorization: Token <你的API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"start_name":"扎下镇","end_name":"江苏沭阳北收费站",
       "fixed_step":"扎下镇-运河路-玉龙南路-龙城大道-江苏沭阳北收费站",
       "fixed_points":"X,Y\n118.751556,34.161008\n118.743252,34.16809"}'
```

服务端会自动把 `start_point` 设为 `[118.751556, 34.161008]`、`end_point` 设为 `[118.743252, 34.16809]`。

### 批量导入（总表 Excel + CSV 逐条入库）

控制台页面内置「批量导入」区块，流程与接口对应：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/fixed-routes/batch/excel/` | 上传总表 `.xlsx`（multipart，字段名 `file`），解析「固定段路线文本」「收费站经纬度」两列，列出每行（起点=文本首段、终点=文本末段、收费站经纬度用于校验） |
| POST | `/api/v1/fixed-routes/batch/csv/` | JSON 提交 `start_name / end_name / fixed_step / toll_point / csv`；校验 CSV 坐标序列中存在与 `toll_point` 一致的坐标点（偏差 ≤ 0.0002° ≈ 20 米）后按 `(start_name, end_name)` 新增或覆盖入库；**入库坐标截断为『起点 → 收费站』**（收费站经纬度后面的坐标全部去掉，`end_point` 取收费站坐标） |

批量导入流程：① 上传总表 Excel → ② 列表展示每一行 → ③ 每行选择对应 CSV，服务端校验 CSV 中是否有坐标点与总表「收费站经纬度」一致，一致才入库；无任何坐标点匹配返回 `400`（`code=COORD_MISMATCH`），该行不写入数据库。入库时只保留从起点到收费站的坐标。

## 六、如何加业务接口（后续）

在 `apps/core/views.py` 或新建 app，例如：

```python
from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(["GET", "POST"])
def demo(request):
    """默认需要 Token"""
    return Response({"hello": "world"})
```

然后在 `apps/core/urls.py` 注册：`path("demo/", views.demo)`，即得 `GET /api/v1/demo/`。

## 七、正式部署：nginx + HTTPS（生产模式）

前提：已有域名（如 `api.example.com`）并解析到服务器公网 IP。HTTPS 必须有域名，仅 IP 无法申请可信证书。

### 1. 关闭 Debug 并配置生产参数（.env）

```bash
vim /home/traffic_api/.env
```

```ini
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=api.example.com
DJANGO_SECURE_PROXY_SSL_HEADER=true
CSRF_TRUSTED_ORIGINS=https://api.example.com
DJANGO_SECURE_COOKIES=false
```

### 2. 安装 nginx 并写入反代配置

```bash
apt install -y nginx
cp /home/traffic_api/deploy/traffic-api-nginx.conf /etc/nginx/sites-available/traffic-api
# 把文件里的 __DOMAIN__ 全部替换为你的域名
sed -i 's/__DOMAIN__/api.example.com/g' /etc/nginx/sites-available/traffic-api
ln -sf /etc/nginx/sites-available/traffic-api /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

### 3. 证书（二选一）

**方案 A：certbot（推荐，免费 90 天，自动续期）**

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d api.example.com --redirect
# 自动完成 443 监听 + HTTP 跳转 + 证书续期（续期任务 certbot.timer 已自动注册）
```

**方案 B：阿里云免费 SSL 证书**

1. 阿里云控制台 → SSL 证书 → 免费证书 → 申请 DV 单域名证书（绑定 api.example.com）；
2. 审核通过后下载 **Nginx** 格式证书，得到 `.pem` 和 `.key`；
3. 上传到服务器：

```bash
mkdir -p /etc/nginx/ssl
# 将两个文件上传到 /etc/nginx/ssl/traffic_api.pem 与 traffic_api.key
nginx -t && systemctl reload nginx
```

### 4. 调整 gunicorn 只监听本机（nginx 后面不再直接暴露 8000）

```bash
vim /home/traffic_api/deploy/traffic-api.service   # 确认 bind 为 127.0.0.1:8000
sudo systemctl daemon-reload
sudo systemctl restart traffic-api
```

### 5. 放行端口与验证

- 云服务器安全组放行 **443**（及 80 用于跳转/证书校验）；**不再对公网放行 8000**；
- 服务器防火墙：`ufw allow 443/tcp && ufw allow 80/tcp`

```bash
curl https://api.example.com/api/v1/health/
curl -H "Authorization: Token <你的API_TOKEN>" https://api.example.com/api/v1/ping/
```

### 6. 常用运维

```bash
sudo systemctl status traffic-api nginx
sudo journalctl -u traffic-api -f      # Django 日志
sudo tail -f /var/log/nginx/error.log  # nginx 日志
sudo certbot renew --dry-run           # 检查证书续期
```
