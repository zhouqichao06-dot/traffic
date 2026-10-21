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

**`POST /api/v1/fixed-routes/`**（需要 Token 鉴权）—— 接收外部 GIS 规划发来的原始路线数据，用「起点名称 + 起点模糊坐标」以及「替换点名称/编号多级规则 + 替换点精确坐标」在数据库 `fixed_route` 表中查找命中的固定路线，命中后用固定路线的文本（`fixed_step`）和坐标（`fixed_points`）**整段切片替换**原始路线，并追加 `fixedStep` / `fixedPoints` 与匹配标记 `iscsdl=1`；未匹配则原样返回原始数据（`iscsdl=0`）。

### 1. 核心设计原则

| 维度 | 处理策略 | 判定标准与容差 |
|---|---|---|
| **处理前置** | 门禁校验 | 仅当请求中 `iskey == "1"` 时执行匹配与替换，非 `"1"`（或缺失）直接原样返回并置 `iscsdl=0`。 |
| **匹配顺序** | **正向漏斗模型** | **先判定起点**（缩小候选池），**再多级判定替换点**（出口收费站/国省道接驳点）。 |
| **起始点判定** | 文本存在 + 坐标模糊 | ① 起点名在路线前部存在；<br>② 起点坐标按**小数点后 4 位截断匹配**（经纬度 `0.0001°` 约 10 米容差）。 |
| **替换点判定** | 坐标精确 + 分级文本 | ① 坐标**去尾 0 必须完全相等**（零容差），且位置在起点之后；<br>② **级别 1（优先）**：编号 + 路名 + 坐标一致（如 `S356沿江公路` 对 `S356沿江公路`）；<br>③ **级别 2（次优）**：编号 + 坐标一致（如请求中为 `S356`，库中为 `S356沿江公路`）；<br>④ **级别 3（收费站）**：全称完全一致 + 坐标一致（如 `江苏季市收费站`）。 |
| **替换执行** | **匹配期区间锁定** | 在匹配阶段一次性预先算好文本切片区间（`text_spans`）与坐标切片区间（`coord_span`），替换阶段直接切片替换，不再二次比对，防止文本名称多义与坐标索引漂移。 |

### 2. 总体处理流程

```
客户端 POST /api/v1/fixed-routes/
        │
        ▼
[0] DRF 全局鉴权（FixedTokenAuthentication，未带/错误 → 401）
        │
        ▼
[1] 请求体格式校验（非 JSON 字典 → 400）
        │
        ▼
[2] iskey 门禁校验：str(iskey) != "1" → 直接原样返回 + iscsdl=0  ✅ 终止
        │
        ▼
[3] 必填字段校验：缺 step/originalStep 或 points.coordinates → 400  ✅ 终止
        │
        ▼
[4] 路线拆段：_split_step() 兼容单横线与双横线
        │
        ▼
[5] 逐条匹配数据库固定路线（_match_routes）：
        ├─ 5.1 起点匹配：start_name 在前部存在 + start_point 小数点后 4 位截断模糊命中
        ├─ 5.2 替换点匹配：end_point 去尾 0 完全相等且在起点之后 + 编号/路名分级文本判定
        ├─ 5.3 排序与去重叠过滤
        └─ 5.4 预先计算并锁定 text_spans 与 coord_span (start_idx, end_idx)
        │
        ▼
[6] 无匹配路线 → 原样返回原始数据 + iscsdl=0  ✅ 终止
        │
        ▼
[7] 文本切片替换：按预先锁定的区间替换 step / originalStep / uploadStep 为 fixed_step
        │
        ▼
[8] 坐标切片替换：按锁定区间倒序替换 points.coordinates 为 fixed_points.coordinates
        │
        ▼
[9] 响应组装：标记 iscsdl=1，追加 fixedStep 与 fixedPoints，返回 200 OK
```

### 3. 输入字段规范（平铺或嵌套 route 均兼容）

请求体中**无需携带任何固定路线配置**，固定路线全量来自数据库。

| 字段 | 类型 | 说明 | 是否必填 |
|---|---|---|:---:|
| `iskey` | string / int | 关键路径处理标识，只有为 `"1"` 或 `1` 时才触发匹配 | **必须为 "1"** |
| `step` | string | 原始路线字符串（用 `-` 分段，如 `厂区-道路A-收费站-高速...`） | 必填（或有 originalStep） |
| `originalStep` | string | 原始路线分段文本，与 `step` 类似 | 选填（两者至少有其一） |
| `uploadStep` | string | 原始上报路线文本，若存在则同步参与替换 | 选填 |
| `points.coordinates` | array | 原始路线经纬度点序列 `[[lng, lat], ...]`（GeoJSON LineString） | **必填** |
| `steps` | array | 结构化分段信息列表（包含路段名称、ref 编号等） | 选填 |

### 4. 数据库固定路线数据（fixed_route 表）

表结构由 Django 迁移生成：

| 字段 | 说明 | 示例 |
|---|---|---|
| `start_name` | 匹配段起点名称（通常为发货企业/厂区名） | `大明重工有限公司` |
| `end_name` | 匹配段终点名称（收费站或接驳道路路口） | `S356沿江公路` 或 `江苏季市收费站` |
| `start_point` | 起点坐标 JSON `[lng, lat]` | `[120.199735, 31.954311]` |
| `end_point` | 终点坐标 JSON `[lng, lat]` | `[120.211367, 31.963333]` |
| `fixed_step` | 审批固定路线文本（`-` 分隔） | `大明重工有限公司-澄江路-联泰路-S356沿江公路` |
| `fixed_points` | 固定路线坐标 GeoJSON LineString | `{"type": "LineString", "coordinates": [[120.199735, 31.954311], ...]}` |

> 唯一约束：`(start_name, end_name)` 唯一，重复录入即覆盖。

### 5. 关键算法与规则详解

#### 5.1 起点坐标 4 位小数模糊截断匹配
实际业务中，GIS 路径规划计算的起点坐标通常吸附到最近路网节点，与数据库中测绘的企业厂区大门点位常有 5～20 米微小偏差。
代码采用 `_trunc4` 算法，将经度与纬度分别截断至小数点后第 4 位（`0.0001°` 约 10 米量级）：
```python
# 示例：
请求起点: [120.422615, 32.062315] -> 截断为 120.4226, 32.0623
库内起点: [120.422606, 32.062310] -> 截断为 120.4226, 32.0623
# 两者完全相等，模糊匹配命中！
```

#### 5.2 替换点编号与路名分级匹配
数据库中的替换点既有纯收费站（如 `江苏季市收费站`），也有国省道接驳点（如 `S356沿江公路`、`G345启那线`、`S356`）。
代码采用正则 `^([A-Za-z]+\d+)(.*)$` 分离道路编号与道路名称：
* **级别 1（最高优先级）**：全称完全一致，或双方编号与路名皆完全相同；
* **级别 2（次高优先级）**：双方编号相同且至少一方缺路名（例如请求中由于地图数据简称为 `S356`，库中为 `S356沿江公路`，编号均为 `S356`，坐标精确一致，允许降级匹配）；
* **级别 3（收费站）**：收费站名称全字相等。

#### 5.3 匹配期区间锁定与无感替换
为避免 `originalStep` 与 `step` 分段数量不同造成的二次查找错位，匹配阶段使用 `_locate_text_spans` 单向递增锁定每个文本字段的替换下标 `(start, end)` 与坐标下标 `coord_span = (start_idx, end_idx)`。
替换阶段直接切片拼接：
* 文本：`tokens[:s] + [fixed_step] + tokens[e + 1:]`
* 坐标：`coords[:start_idx] + fixed_coords + coords[end_idx + 1:]`（多段倒序替换防下标漂移）

### 6. 请求与响应示例

#### 请求示例（以含有 S356 编号的大明重工为例）

```json
{
  "iskey": "1",
  "djId": "B3200002026102000264",
  "beginDistName": "大明重工有限公司",
  "endDistName": "扬州天勤混凝土预制构件有限公司",
  "step": "大明重工有限公司-澄江路-联泰路-S356-扬州天勤混凝土预制构件有限公司",
  "originalStep": "大明重工有限公司-澄江路-联泰路-S356-扬州天勤混凝土预制构件有限公司",
  "points": {
    "type": "LineString",
    "coordinates": [
      [120.199735, 31.954311],
      [120.201200, 31.956000],
      [120.211367, 31.963333],
      [119.609873, 32.314599]
    ]
  }
}
```

#### 响应示例（命中固定路线）

```json
{
  "iskey": "1",
  "djId": "B3200002026102000264",
  "beginDistName": "大明重工有限公司",
  "endDistName": "扬州天勤混凝土预制构件有限公司",
  "step": "大明重工有限公司-澄江路-联泰路-S356沿江公路-扬州天勤混凝土预制构件有限公司",
  "originalStep": "大明重工有限公司-澄江路-联泰路-S356沿江公路-扬州天勤混凝土预制构件有限公司",
  "points": {
    "type": "LineString",
    "coordinates": [
      [120.199735, 31.954311],
      "...固定路线中间点位...",
      [120.211367, 31.963333],
      [119.609873, 32.314599]
    ]
  },
  "iscsdl": 1,
  "fixedStep": "大明重工有限公司-澄江路-联泰路-S356沿江公路",
  "fixedPoints": {
    "type": "LineString",
    "coordinates": [
      [120.199735, 31.954311],
      "...数据库审批标准点位...",
      [120.211367, 31.963333]
    ]
  }
}
```

- **`iscsdl`**：是否命中固定路线，`1` 表示已匹配并完成替换，`0` 表示未匹配原样返回；
- **`fixedStep` / `fixedPoints`**：命中的固定路线标准分段文本与坐标，直接供前端绘制或归档使用。

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
| POST | `/api/v1/fixed-routes/batch/csv/` | JSON 提交 `start_name / end_name / fixed_step / toll_point / csv`；校验 CSV 坐标序列中存在与 `toll_point` **完全一致**的坐标点（去尾 0 归一化后相等，不允许任何容差，近似点不视为一致）后按 `(start_name, end_name)` 新增或覆盖入库；**入库坐标截断为『起点 → 收费站』**（收费站经纬度后面的坐标全部去掉，`end_point` 取收费站坐标） |

批量导入流程：① 上传总表 Excel → ② 列表展示每一行 → ③ 每行选择对应 CSV，服务端校验 CSV 中是否有坐标点与总表「收费站经纬度」**完全一致**，一致才入库；无任何坐标点完全一致返回 `400`（`code=COORD_MISMATCH`），该行不写入数据库。入库时只保留从起点到收费站的坐标。

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
