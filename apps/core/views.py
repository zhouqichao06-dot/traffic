"""核心接口：健康检查 + 鉴权示例 + 固定路线应用业务接口"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import FixedRoute


#: 坐标一致性容差（度），与批量导入 BATCH_COORD_TOL 保持一致（约 0.0002° ≈ 20 米）。
#: 用于判断数据库记录起点/终点坐标是否包含在请求 points 中时，允许来源间的取整差异。
COORD_MATCH_TOL = 0.0002


# ---------------------------------------------------------------- 基础接口

@api_view(["GET"])
@authentication_classes([])  # 健康检查不要求鉴权，方便外部探活
@permission_classes([AllowAny])
def health(request):
    """GET /api/v1/health/ —— 探活接口，不依赖数据库。"""
    return Response(
        {
            "status": "ok",
            "service": "traffic_api",
            "time": timezone.now().isoformat(),
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
def ping(request):
    """GET /api/v1/ping/ —— 需要携带 Token，用于验证鉴权是否生效。"""
    return Response(
        {
            "message": "pong",
            "authenticated": True,
            "time": timezone.now().isoformat(),
        },
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------- 固定路线业务接口

def _split_step(step):
    """把路线字符串拆成分段：兼容单横线（原始路线）与双横线（固定路线）。"""
    if not step:
        return []
    return [t for t in step.replace("--", "-").split("-") if t]


def _find_point_index(coords, target, tol=1e-5):
    """在坐标列表中查找目标点索引：先精确匹配（容差 tol），再取欧氏距离最近点。

    匹配前坐标统一转 float 归一化（去掉无效尾 0），因此数据库中存储的
    119.872940 与请求坐标中的 119.87294 视为同一坐标。
    """
    if not target or len(target) != 2:
        return None
    try:
        t_lng, t_lat = float(target[0]), float(target[1])
    except (TypeError, ValueError):
        return None
    best, best_dist = None, None
    for i, (lng, lat) in enumerate(coords):
        try:
            lng, lat = float(lng), float(lat)
        except (TypeError, ValueError):
            continue
        if abs(lng - t_lng) <= tol and abs(lat - t_lat) <= tol:
            return i
        dist = (lng - t_lng) ** 2 + (lat - t_lat) ** 2
        if best_dist is None or dist < best_dist:
            best, best_dist = i, dist
    return best


def _match_routes(step_tokens, coords=None, tol=COORD_MATCH_TOL):
    """拿 step 分段列表匹配数据库中的固定路线记录。

    匹配条件（全部满足才算命中，任一不满足则跳过该记录）：
      ① 名称：记录 (start_name, end_name) 在 step 分段中按顺序出现
         （起点在前、终点在后）；
      ② 坐标：记录 start_point / end_point 必须**都包含**在请求的 points
         （coords 坐标序列）中（任一点偏差 ≤ tol 即视为包含）；
         未提供 coords 时跳过坐标校验（仅按名称匹配）。
    返回按起点索引排序、互不重叠的匹配列表：[(s_idx, e_idx, route), ...]
    """
    matches = []
    for r in FixedRoute.objects.all():
        s_name, e_name = r.start_name, r.end_name
        if not s_name or not e_name or s_name not in step_tokens:
            continue
        s = step_tokens.index(s_name)
        if e_name not in step_tokens[s + 1:]:
            continue
        e = s + 1 + step_tokens[s + 1:].index(e_name)
        # ② 坐标包含校验：起点与终点坐标都必须包含在 points 中，全部符合才替换
        if coords is not None:
            if not _point_contained(coords, r.start_point, tol):
                continue
            if not _point_contained(coords, r.end_point, tol):
                continue
        matches.append((s, e, r))

    # 去重叠：按起点排序，起点位于已保留段终点之后才保留
    matches.sort(key=lambda m: m[0])
    kept = []
    for m in matches:
        if not kept or m[0] > kept[-1][1]:
            kept.append(m)
    return kept


def _coord_distance(a, b):
    """两点欧氏距离；任一点含非法坐标时返回 None。"""
    try:
        a_lng, a_lat = float(a[0]), float(a[1])
        b_lng, b_lat = float(b[0]), float(b[1])
    except (TypeError, ValueError):
        return None
    return ((a_lng - b_lng) ** 2 + (a_lat - b_lat) ** 2) ** 0.5


def _point_contained(coords, target, tol=COORD_MATCH_TOL):
    """判断 target 坐标（如 [lng, lat]）是否包含在 coords 坐标序列中。

    只要存在任意一个坐标点与 target 偏差 ≤ tol 即视为包含；
    坐标统一转 float 归一化（去掉无效尾 0），与数据库/请求的取整差异兼容。
    """
    if not target or len(target) < 2:
        return False
    try:
        t_lng, t_lat = float(target[0]), float(target[1])
    except (TypeError, ValueError):
        return False
    for c in coords:
        d = _coord_distance(c, (t_lng, t_lat))
        if d is not None and d <= tol:
            return True
    return False


def _index_in(tokens, name, start):
    """在 tokens 中从 start 起查找 name，返回绝对索引；找不到返回 None。"""
    if start < 0:
        return None
    try:
        return start + tokens[start:].index(name)
    except ValueError:
        return None


def _apply_to_step(step, matches):
    """把路线字符串中匹配到的『起点…终点』段替换为数据库 fixed_step。

    固定段内部与前后段统一用 "-" 连接（fixed_step 本身为单横线分隔）。
    示例：扎下镇-XZ04天津北路-G205山深线-江苏沭阳北收费站-G2-…
          → 扎下镇-运河路-玉龙南路-龙城大道-江苏沭阳北收费站-G2-…

    定位规则：matches 中的 (s, e) 索引来自 step 的分段，但 originalStep 与 step
    的分段数量可能不一致（step 常含更多道路名），直接套用索引会错位误删路段。
    因此替换时按记录 start_name / end_name 在 originalStep 分段中重新定位；
    名称定位失败时回退为 step 索引（保留原文段，不做替换以免误删）。
    """
    tokens = _split_step(step)
    new_tokens = []
    i = 0
    for s, e, r in matches:
        s2 = _index_in(tokens, r.start_name, i)
        if s2 is None:
            s2 = s if s < len(tokens) else len(tokens)
        e2 = _index_in(tokens, r.end_name, s2 + 1)
        if e2 is None:
            e2 = e if e < len(tokens) else len(tokens) - 1
        if e2 >= s2:
            new_tokens.extend(tokens[i:s2])
            new_tokens.append(r.fixed_step)
            i = e2 + 1
        else:
            # 异常：终点位于起点之前（数据异常），保留原文剩余部分
            new_tokens.extend(tokens[i:])
            i = len(tokens)
    new_tokens.extend(tokens[i:])
    return "-".join(new_tokens)


def _apply_to_coords(coords, matches):
    """把坐标列表中匹配段（起点坐标→终点坐标之间的点位）整体替换为 fixed_points.coordinates。

    定位规则：用数据库记录的 start_point / end_point 在原始坐标中查找
    （精确匹配 ±1e-5，否则取欧氏最近点），该段整体替换。
    多段匹配时从后往前替换，避免索引漂移。
    """
    new_coords = coords
    for s, e, r in reversed(matches):
        fixed_points = r.fixed_points
        fixed_coords = (
            fixed_points.get("coordinates") if isinstance(fixed_points, dict) else fixed_points
        )
        if not fixed_coords or len(fixed_coords) < 2:
            continue
        start_idx = _find_point_index(new_coords, r.start_point)
        end_idx = _find_point_index(new_coords, r.end_point)
        if start_idx is None or end_idx is None or end_idx <= start_idx:
            continue
        new_coords = new_coords[:start_idx] + fixed_coords + new_coords[end_idx + 1:]
    return new_coords


def _apply_fixed_routes(payload):
    """请求体（原始路线数据）→ 匹配数据库固定路线 → 替换 originalStep / points。

    规则：
      ① 从 step 分段中匹配数据库 fixed_route 记录：先按名称（start_name 在前、
         end_name 在后），再校验记录的起点/终点坐标都包含在请求 points 中，
         全部符合才替换；
      ② step / originalStep / uploadStep 处理一致：匹配到的『起点…终点』段
         在各字段中整段替换为 fixed_step（请求中不存在的字段不新增）；
      ③ 匹配段坐标整体替换为 fixed_points.coordinates；
      ④ 响应新增 iscsdl：匹配到固定线路返回 1，未匹配返回 0；
      ⑤ 响应追加 fixedStep（固定线路点位）与 fixedPoints（固定线路坐标），
         取第一条匹配的固定线路；多段匹配时 step / originalStep / uploadStep /
         points 仍全部替换；
      ⑥ 未匹配到任何固定路线时原样返回原始数据（iscsdl=0，无 fixedStep/fixedPoints）。
    """
    route = payload.get("route") or payload  # 兼容嵌套 route 或平铺结构
    step = route.get("step") or route.get("originalStep")
    original_step = route.get("originalStep") or route.get("step")
    points = route.get("points") or {}
    original_coords = points.get("coordinates") if isinstance(points, dict) else None
    if not step or not original_coords:
        return {"code": 400, "detail": "缺少原始路线：需要 step（或 originalStep）与 points.coordinates"}

    matches = _match_routes(_split_step(step), original_coords)
    if not matches:
        # 没有匹配到任何固定路线：原样返回，iscsdl=0
        result = dict(route)
        result["iscsdl"] = 0
        return result

    new_original_step = _apply_to_step(original_step, matches)
    new_coords = _apply_to_coords(original_coords, matches)

    first = matches[0][2]  # 第一条匹配的固定线路记录
    result = dict(route)
    # step / originalStep / uploadStep 处理一致：匹配到的『起点…终点』段整段替换为 fixed_step
    # （请求中原本没有的字段不新增）
    if route.get("step"):
        result["step"] = _apply_to_step(route.get("step"), matches)
    result["originalStep"] = new_original_step
    if route.get("uploadStep"):
        result["uploadStep"] = _apply_to_step(route.get("uploadStep"), matches)
    # points 同样按匹配段替换：匹配段坐标整体替换为 fixed_points.coordinates
    result["points"] = {**points, "coordinates": new_coords}
    result["iscsdl"] = 1  # 匹配到固定线路返回 1
    result["fixedStep"] = first.fixed_step     # 固定线路点位
    result["fixedPoints"] = first.fixed_points  # 固定线路坐标
    return result


@api_view(["GET", "POST"])
def fixed_routes(request):
    """固定路线接口（需要 Token 鉴权，沿用全局配置）。

    POST /api/v1/fixed-routes/ —— 应用固定路线：
        输入：原始路线数据（参考 数据.json：step / originalStep / uploadStep / steps / points），
              不含任何固定路线字段。
        处理：① 拿 step 分段匹配数据库 fixed_route 表（起点+终点 命中即匹配）；
             ② step / originalStep / uploadStep 处理一致：『起点…终点』段整段
                替换为数据库 fixed_step；
             ③ points.coordinates 中对应坐标段整体替换为 fixed_points.coordinates；
             ④ 响应追加 fixedStep / fixedPoints（匹配到的固定线路点位与坐标）；
             ⑤ 匹配标记 iscsdl=1，未匹配 iscsdl=0 原样返回。
    GET /api/v1/fixed-routes/ —— 列出已入库的固定路线（供确认数据库数据）。
    """
    if request.method == "POST":
        payload = request.data
        if not isinstance(payload, dict):
            return Response({"detail": "请求体必须是 JSON 对象"}, status=status.HTTP_400_BAD_REQUEST)
        result = _apply_fixed_routes(payload)
        if isinstance(result, dict) and result.get("code") == 400:
            return Response({"detail": result["detail"]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_200_OK)

    # GET：固定路线列表
    qs = FixedRoute.objects.order_by("-updated_at")
    data = [
        {
            "id": r.id,
            "startName": r.start_name,
            "endName": r.end_name,
            "startPoint": r.start_point,
            "endPoint": r.end_point,
            "fixedStep": r.fixed_step,
            "fixedPoints": r.fixed_points,
            "createdAt": r.created_at.isoformat(),
            "updatedAt": r.updated_at.isoformat(),
        }
        for r in qs
    ]
    return Response(data, status=status.HTTP_200_OK)
