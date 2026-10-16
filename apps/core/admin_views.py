"""管理控制台接口：固定账号登录 + FixedRoute 增删改查 + 批量导入 + 管理页面渲染。

路径约定：
    GET    /console/                       前端管理页面（HTML，免鉴权，登录在前端完成）
    POST   /api/v1/admin/login/            固定账号密码登录，返回 API_TOKEN（免鉴权）
    GET    /api/v1/fixed-routes/manage/    固定路线列表（需 Token）
    POST   /api/v1/fixed-routes/manage/    新增固定路线（需 Token）
    PUT    /api/v1/fixed-routes/manage/<id>/  修改固定路线（需 Token）
    DELETE /api/v1/fixed-routes/manage/<id>/  删除固定路线（需 Token）
    POST   /api/v1/fixed-routes/batch/excel/  批量导入①：解析总表 Excel（需 Token）
    POST   /api/v1/fixed-routes/batch/csv/    批量导入②：校验坐标一致后入库（需 Token）

新增/修改规则：
    start_name / end_name / fixed_step 手动输入；
    start_point / end_point 不需要输入，自动取 fixed_points 的首个/末个坐标；
    fixed_points 支持三种形式：LineString dict / 坐标数组 / CSV 文本（X,Y 表头）。

批量导入规则：
    ① 上传含「固定段路线文本」「收费站经纬度」两列的总表 Excel，
       解析后列出每行（起点=文本首段，终点=文本末段，收费站经纬度用于校验）；
    ② 逐行上传对应 CSV，服务端校验 CSV 坐标序列中是否存在与总表收费站经纬度
       一致的坐标点（容差 BATCH_COORD_TOL 度）后，按 (start_name, end_name)
       新增或覆盖入库；入库坐标截断为『起点 → 收费站』（含收费站点），
       收费站经纬度后面的坐标全部去掉。
"""
from django.conf import settings
from django.shortcuts import render
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .batch_import import (
    BATCH_COORD_TOL,
    find_matching_point,
    parse_csv_coords as _parse_csv_coords_batch,
    parse_excel_rows,
    split_step_text,
    trim_to_toll,
)
from .models import FixedRoute


# ---------------------------------------------------------------- 管理页面

def console_page(request):
    """渲染前端管理页面（单页，登录与增删改查都在页面上完成）。"""
    return render(request, "console.html", {"api_base": "/api/v1"})


# ---------------------------------------------------------------- 登录

@api_view(["POST"])
@authentication_classes([])  # 登录接口免鉴权：验证固定账号密码后下发 Token
@permission_classes([AllowAny])
def admin_login(request):
    """POST /api/v1/admin/login/ —— 固定账号密码登录。

    请求：{"username": "admin", "password": "Xxxt@2026."}
    成功：{"token": "<API_TOKEN>", "username": "admin"}
    """
    username = (request.data.get("username") or "").strip()
    password = request.data.get("password") or ""
    if (username, password) != (settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD):
        return Response({"detail": "用户名或密码错误"}, status=status.HTTP_401_UNAUTHORIZED)
    if not settings.API_TOKEN:
        return Response(
            {"detail": "服务端未配置 API_TOKEN，无法下发访问令牌"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(
        {"token": settings.API_TOKEN, "username": username},
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------- fixed_points 解析

def _is_header(x, y):
    """判断 CSV 行是否为表头（X,Y / lng,lat / 经度,纬度 等常见写法）。"""
    xl = (x or "").lower()
    yl = (y or "").lower()
    return (
        xl in ("x", "lng", "lon", "longitude", "经度", "经度(")
        and yl in ("y", "lat", "latitude", "纬度")
    )


def _validate_coords(coords):
    """校验并规范化坐标数组：每点为 [lng, lat]，数值在合法经纬度范围内。"""
    if not isinstance(coords, list) or len(coords) < 2:
        raise ValueError("固定路线坐标至少需要 2 个点")
    out = []
    for c in coords:
        if not isinstance(c, (list, tuple)) or len(c) < 2:
            raise ValueError("坐标点格式错误，应为 [经度, 纬度]")
        try:
            lng, lat = float(c[0]), float(c[1])
        except (TypeError, ValueError):
            raise ValueError(f"坐标点包含非数字：{c}")
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise ValueError(f"坐标超出合法范围：{c}")
        out.append([lng, lat])
    return out


def _parse_csv_coords(text):
    """解析 CSV 文本为坐标数组：每行『经度,纬度』，自动跳过表头与空行。"""
    text = (text or "").lstrip("\ufeff")  # 去掉 UTF-8 BOM
    coords = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace("\t", ",").split(",")]
        if len(parts) != 2:
            raise ValueError(f"CSV 行格式错误（应为 经度,纬度）：{line}")
        if _is_header(parts[0], parts[1]):
            continue
        try:
            lng, lat = float(parts[0]), float(parts[1])
        except ValueError:
            raise ValueError(f"CSV 行坐标非数字：{line}")
        coords.append([lng, lat])
    if len(coords) < 2:
        raise ValueError("CSV 中至少需要 2 个坐标点")
    return coords


def _parse_fixed_points(raw):
    """把三种输入统一为 LineString dict：{"type": "LineString", "coordinates": [[lng, lat], ...]}。

    支持的输入：
      ① dict：GeoJSON LineString；
      ② list：坐标数组 [[lng, lat], ...]；
      ③ str：CSV 文本（X,Y 表头，每行 经度,纬度）。
    """
    if raw is None or raw == "":
        raise ValueError("fixed_points 不能为空")
    if isinstance(raw, dict):
        coords = raw.get("coordinates")
        if not isinstance(coords, list):
            raise ValueError("fixed_points.coordinates 必须是数组")
    elif isinstance(raw, list):
        coords = raw
    elif isinstance(raw, str):
        coords = _parse_csv_coords(raw)
    else:
        raise ValueError("fixed_points 格式不支持（支持 LineString / 坐标数组 / CSV 文本）")
    return {"type": "LineString", "coordinates": _validate_coords(coords)}


# ---------------------------------------------------------------- 序列化与公共校验

def _serialize(r):
    return {
        "id": r.id,
        "start_name": r.start_name,
        "end_name": r.end_name,
        "start_point": r.start_point,
        "end_point": r.end_point,
        "fixed_step": r.fixed_step,
        "fixed_points": r.fixed_points,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }


def _extract_names(data, defaults=None):
    """提取并校验 start_name / end_name / fixed_step。"""
    defaults = defaults or {}
    start_name = (data.get("start_name") or defaults.get("start_name") or "").strip()
    end_name = (data.get("end_name") or defaults.get("end_name") or "").strip()
    fixed_step = (data.get("fixed_step") or defaults.get("fixed_step") or "").strip()
    if not start_name:
        raise ValueError("起点名称 start_name 必填")
    if not end_name:
        raise ValueError("终点名称 end_name 必填")
    if not fixed_step:
        raise ValueError("固定段线路文本 fixed_step 必填")
    return start_name, end_name, fixed_step


# ---------------------------------------------------------------- CRUD 接口

@api_view(["GET", "POST"])
def fixed_route_manage(request):
    """GET 列表 / POST 新增（需要 Token，沿用全局鉴权）。"""
    if request.method == "GET":
        # 最新的放在最前面
        qs = FixedRoute.objects.order_by("-updated_at", "-id")
        return Response([_serialize(r) for r in qs], status=status.HTTP_200_OK)

    data = request.data if isinstance(request.data, dict) else {}
    try:
        start_name, end_name, fixed_step = _extract_names(data)
        fixed_points = _parse_fixed_points(data.get("fixed_points"))
    except ValueError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    if FixedRoute.objects.filter(start_name=start_name, end_name=end_name).exists():
        return Response(
            {"detail": f"「{start_name} → {end_name}」已存在，如需修改请使用编辑"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    coords = fixed_points["coordinates"]
    r = FixedRoute.objects.create(
        start_name=start_name,
        end_name=end_name,
        start_point=coords[0],  # 起点坐标 = 固定路线第一个坐标
        end_point=coords[-1],  # 终点坐标 = 固定路线最后一个坐标
        fixed_step=fixed_step,
        fixed_points=fixed_points,
    )
    return Response(_serialize(r), status=status.HTTP_201_CREATED)


@api_view(["PUT", "DELETE"])
def fixed_route_detail(request, pk):
    """PUT 修改 / DELETE 删除（需要 Token，沿用全局鉴权）。"""
    try:
        r = FixedRoute.objects.get(pk=pk)
    except FixedRoute.DoesNotExist:
        return Response({"detail": "记录不存在"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        r.delete()
        return Response({"detail": "已删除"}, status=status.HTTP_200_OK)

    data = request.data if isinstance(request.data, dict) else {}
    try:
        start_name, end_name, fixed_step = _extract_names(data, defaults={
            "start_name": r.start_name,
            "end_name": r.end_name,
            "fixed_step": r.fixed_step,
        })
    except ValueError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # 唯一性检查（排除自身）：修改起/终点名称时不允许撞已有组合
    if FixedRoute.objects.filter(start_name=start_name, end_name=end_name).exclude(pk=pk).exists():
        return Response(
            {"detail": f"「{start_name} → {end_name}」已存在，不能重复"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    r.start_name = start_name
    r.end_name = end_name
    r.fixed_step = fixed_step
    # 只有重新提交了 fixed_points 才更新坐标并重算起终点（编辑时可不传坐标保持原值）
    if "fixed_points" in data and data.get("fixed_points") not in (None, ""):
        try:
            fixed_points = _parse_fixed_points(data.get("fixed_points"))
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        coords = fixed_points["coordinates"]
        r.fixed_points = fixed_points
        r.start_point = coords[0]
        r.end_point = coords[-1]
    r.save()
    return Response(_serialize(r), status=status.HTTP_200_OK)


# ---------------------------------------------------------------- 批量导入

@api_view(["POST"])
def fixed_route_batch_excel(request):
    """POST /api/v1/fixed-routes/batch/excel/ —— 批量导入①：解析总表 Excel。

    multipart/form-data，字段名 file（.xlsx）。
    返回总表每行的 起点/终点/固定段文本/收费站经纬度（含库中是否已存在标记）。
    """
    f = request.FILES.get("file")
    if f is None:
        return Response(
            {"detail": "缺少文件（字段名 file，.xlsx）"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not (f.name or "").lower().endswith(".xlsx"):
        return Response(
            {"detail": "仅支持 .xlsx 总表文件"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        rows, errors = parse_excel_rows(f.read())
    except Exception as e:
        return Response(
            {"detail": "Excel 解析失败：%s" % e},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # 标注 (start_name, end_name) 是否已在库中（导入时将覆盖）
    if rows:
        keys = set(FixedRoute.objects.values_list("start_name", "end_name"))
        for r in rows:
            r["exists"] = (r["start_name"], r["end_name"]) in keys
    return Response({"rows": rows, "errors": errors, "tolerance": BATCH_COORD_TOL})


@api_view(["POST"])
def fixed_route_batch_csv(request):
    """POST /api/v1/fixed-routes/batch/csv/ —— 批量导入②：校验坐标一致后入库。

    JSON 请求体：
        start_name / end_name / fixed_step  （来自总表解析结果）
        toll_point    （总表收费站经纬度 [lng, lat]，用于校验）
        csv           （CSV 文本：X,Y 或 longitude,latitude 表头，每行 经度,纬度）

    校验规则：CSV 坐标序列中只要有**任意一个**坐标点与 toll_point 一致
    （偏差 ≤ BATCH_COORD_TOL 度）即视为匹配，否则拒绝入库（400）；
    匹配则按 (start_name, end_name) 新增或覆盖，入库坐标截断为
    『起点 → 收费站』（收费站经纬度后面的坐标全部去掉）。
    """
    data = request.data if isinstance(request.data, dict) else {}
    start_name = (data.get("start_name") or "").strip()
    end_name = (data.get("end_name") or "").strip()
    fixed_step = (data.get("fixed_step") or "").strip()
    csv_text = data.get("csv")
    toll_raw = data.get("toll_point")

    if not (start_name and end_name and fixed_step):
        return Response(
            {"detail": "缺少 start_name / end_name / fixed_step"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not csv_text:
        return Response(
            {"detail": "缺少 CSV 坐标内容（字段 csv）"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    segs = split_step_text(fixed_step)
    if segs and (segs[0] != start_name or segs[-1] != end_name):
        return Response(
            {"detail": "固定段文本的首段/末段与 start_name/end_name 不一致，请核对所选总表行"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        coords = _parse_csv_coords_batch(csv_text)
    except ValueError as e:
        return Response({"detail": "CSV 解析失败：%s" % e}, status=status.HTTP_400_BAD_REQUEST)

    if not (isinstance(toll_raw, (list, tuple)) and len(toll_raw) >= 2):
        return Response(
            {
                "detail": "缺少有效的收费站经纬度 toll_point，无法校验，未入库",
                "code": "NO_TOLL_POINT",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    matched_idx, matched_pt, dev = find_matching_point(coords, toll_raw)
    if matched_idx is None:
        return Response(
            {
                "detail": (
                    "CSV 中没有任何坐标点与总表收费站经纬度一致（最近偏差 %.6f°，容差 %.6f°），未入库。"
                    "请确认选择的 CSV 与这一行对应。" % (dev, BATCH_COORD_TOL)
                ),
                "code": "COORD_MISMATCH",
                "last_point": coords[-1],
                "nearest_point": None,
                "toll_point": [float(toll_raw[0]), float(toll_raw[1])],
                "deviation": dev,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 入库坐标截断：只保留『起点 → 收费站』（含收费站点），收费站后面的坐标去掉
    trimmed = trim_to_toll(coords, matched_idx)
    r, created = FixedRoute.objects.update_or_create(
        start_name=start_name,
        end_name=end_name,
        defaults={
            "start_point": trimmed[0],
            "end_point": trimmed[-1],
            "fixed_step": fixed_step,
            "fixed_points": {"type": "LineString", "coordinates": trimmed},
        },
    )
    return Response(
        {
            "action": "insert" if created else "update",
            "detail": "已新增入库" if created else "已覆盖数据库已有记录",
            "record": _serialize(r),
            "matched_point": matched_pt,
            "last_point": trimmed[-1],
            "toll_point": [float(toll_raw[0]), float(toll_raw[1])],
            "deviation": dev,
            "trimmed": len(coords) - len(trimmed),  # 被去掉的收费站后坐标点数
        },
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )
