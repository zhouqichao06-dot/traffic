"""核心接口：健康检查 + 鉴权示例 + 固定路线应用业务接口"""
from collections import namedtuple
from decimal import Decimal, ROUND_DOWN
import re

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import FixedRoute

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

ROAD_REF_PATTERN = re.compile(r"^([A-Za-z]+\d+)(.*)$")

# 一条命中的固定路线，以及匹配阶段预先算好的替换位置：
#   s / e       匹配所用 step 分段中的起点/终点索引（去重叠排序用）
#   priority    替换点匹配优先级（1: 编号+路名一致/全称一致; 2: 编号一致）
#   coord_span  原始坐标中的替换区间 (start_idx, end_idx)；None 表示不替换坐标
#   text_spans  {字段名: (start, end)}；字段为 step / originalStep / uploadStep
_RouteMatch = namedtuple("_RouteMatch", "s e priority route coord_span text_spans")


def _split_step(step):
    """把路线字符串拆成分段：兼容单横线（原始路线）与双横线（固定路线）。"""
    if not step:
        return []
    return [t for t in step.replace("--", "-").split("-") if t]


def _parse_road_ref_name(text):
    """解析道路/站点名称中的编号与路名。

    例如：
      'S356沿江公路' -> ('S356', '沿江公路')
      'S356'         -> ('S356', '')
      'G345启那线'   -> ('G345', '启那线')
      '江苏季市收费站' -> (None, '江苏季市收费站')
    """
    if not text:
        return None, ""
    text = str(text).strip()
    m = ROAD_REF_PATTERN.match(text)
    if m:
        ref = m.group(1).upper()
        road = m.group(2).strip()
        return ref, road
    return None, text


def _find_point_index(coords, target):
    """在坐标列表中查找与目标点**完全一致**的索引；不存在返回 None。

    不允许任何容差：坐标统一转 float 归一化（去掉无效尾 0）后必须相等，
    数据库中存储的 119.872940 与请求坐标中的 119.87294 视为同一坐标；
    近似点（如偏差 0.00004°）一律不算匹配。
    """
    if not target or len(target) != 2:
        return None
    try:
        t_lng, t_lat = float(target[0]), float(target[1])
    except (TypeError, ValueError):
        return None
    for i, (lng, lat) in enumerate(coords):
        try:
            lng, lat = float(lng), float(lat)
        except (TypeError, ValueError):
            continue
        if lng == t_lng and lat == t_lat:
            return i
    return None


def _trunc4(value):
    """把单个坐标分量截断到小数点后 4 位（第 4 位以后直接忽略）。"""
    try:
        return Decimal(str(float(value))).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    except (TypeError, ValueError, ArithmeticError):
        return None


def _find_point_index_fuzzy(coords, target):
    """在坐标列表中模糊查找目标点，返回索引；不存在返回 None。

    模糊规则：经度、纬度各自截断到小数点后 4 位后完全相等即视为同一点
    （0.0001° 约 10 米量级）。仅用于**起点**判定与替换定位，
    终点仍用 _find_point_index 精确匹配。
    """
    if not target or len(target) != 2:
        return None
    t_lng, t_lat = _trunc4(target[0]), _trunc4(target[1])
    if t_lng is None or t_lat is None:
        return None
    for i, (lng, lat) in enumerate(coords):
        lng4, lat4 = _trunc4(lng), _trunc4(lat)
        if lng4 is not None and lat4 is not None and lng4 == t_lng and lat4 == t_lat:
            return i
    return None


def _match_end_token(db_end_name, candidate_token):
    """判断 candidate_token 与数据库替换点 db_end_name 是否匹配，并返回优先级。

    优先级规则：
      - 优先级 1（最优）：
        * 全称完全一致（如都是 'S356沿江公路' 或都是 '江苏季市收费站'）；
        * 编号一致且路名一致（如 'S356沿江公路' 对 'S356沿江公路'）；
      - 优先级 2（次优）：
        * 含有编号，且编号一致（如 'S356沿江公路' 对 'S356'，或 'S356' 对 'S356'）；
      - 不匹配：返回 None。
    """
    if not db_end_name or not candidate_token:
        return None
    db_clean = db_end_name.strip()
    cand_clean = candidate_token.strip()

    # 1. 全称完全一致
    if db_clean == cand_clean:
        return 1

    db_ref, db_road = _parse_road_ref_name(db_clean)
    cand_ref, cand_road = _parse_road_ref_name(cand_clean)

    # 2. 含有编号时的分级判断
    if db_ref and cand_ref and db_ref == cand_ref:
        # 编号相同
        if db_road and cand_road:
            # 两者都有路名：路名一致为优先级 1，路名不同不匹配（避免 S356A路 匹配 S356B路）
            if db_road == cand_road:
                return 1
            return None
        # 其中一方缺路名（例如 S356 对应 S356沿江公路），编号一致算匹配，优先级 2
        return 2

    return None


def _find_end_token_in_tokens(tokens, start_idx, db_end_name):
    """在 tokens 中从 start_idx + 1 开始寻找与 db_end_name 最佳匹配的终点 token。

    返回 (end_idx, matched_token, priority)；找不到返回 (None, None, None)。
    优先返回优先级高（数值小）的；同优先级返回最靠前出现的。
    """
    best_match = None  # (priority, idx, token)
    for idx in range(start_idx + 1, len(tokens)):
        token = tokens[idx]
        p = _match_end_token(db_end_name, token)
        if p is not None:
            if best_match is None or p < best_match[0]:
                best_match = (p, idx, token)
                if p == 1:
                    # 已经是最优优先级，直接命中
                    break
    if best_match is not None:
        return best_match[1], best_match[2], best_match[0]
    return None, None, None


def _match_routes(step_tokens, coords=None, text_tokens=None):
    """按「先判定起点、再多级判定替换点」规则匹配数据库固定路线，并记录替换位置。

    匹配规则（正向顺序）：
      ① 起点判定：
         - 文本：r.start_name 必须在 step 分段中存在，且取其首次出现位置 s；
         - 坐标：r.start_point 必须在 points 中按小数点后 4 位截断模糊命中，得到 start_idx；
         未满足直接跳过，快速缩小候选集。
      ② 替换点判定（终点/接驳点）：
         - 坐标：r.end_point 必须精确包含在 points 中（去尾 0 完全相等），且 end_idx > start_idx；
         - 文本：在 step 分段中 s 之后寻找匹配的 token，分级优先：
             ① 优先：编号+路名+坐标一致（或全称完全一致）；
             ② 其次：编号+坐标一致（如 S356沿江公路 对 S356）。
         未满足跳过。

    命中时预先记录替换位置：
      - coord_span: (start_idx, end_idx)
      - text_spans: {字段名: (s_field, e_field)}
    返回按起点索引排序、互不重叠的 _RouteMatch 列表。
    """
    raw = []
    for r in FixedRoute.objects.all():
        s_name, e_name = r.start_name, r.end_name
        if not s_name or not e_name:
            continue

        # ---------------- 1. 先判定起点 ----------------
        # 1.1 起点文本匹配
        if s_name not in step_tokens:
            continue
        s = step_tokens.index(s_name)

        # 1.2 起点坐标匹配（模糊：小数点后 4 位截断）
        start_idx = None
        if coords is not None:
            start_idx = _find_point_index_fuzzy(coords, r.start_point)
            if start_idx is None:
                continue

        # ---------------- 2. 再判定替换点 ----------------
        # 2.1 替换点坐标匹配（精确：去尾 0 必须完全相等）
        end_idx = None
        if coords is not None:
            end_idx = _find_point_index(coords, r.end_point)
            if end_idx is None or end_idx <= start_idx:
                continue

        # 2.2 替换点文本匹配（多级优先级）
        e, matched_token, priority = _find_end_token_in_tokens(step_tokens, s, e_name)
        if e is None:
            continue

        coord_span = (start_idx, end_idx) if coords is not None else None
        raw.append((s, e, priority, r, coord_span, matched_token))

    # 去重叠：按起点索引排序；若起点相同，按匹配优先级优（小）的保留；互不重叠
    raw.sort(key=lambda m: (m[0], m[2]))
    kept = []
    for m in raw:
        if not kept or m[0] > kept[-1][1]:
            kept.append(m)

    # 预先计算各文本字段（step / originalStep / uploadStep）的替换区间
    field_spans = {}
    if text_tokens:
        for field, tokens in text_tokens.items():
            if tokens is not None:
                field_spans[field] = _locate_text_spans(tokens, kept)

    matches = []
    for i, m in enumerate(kept):
        s, e, priority, route, coord_span, matched_token = m
        t_spans = {f: field_spans[f][i] for f in field_spans} if field_spans else {}
        matches.append(_RouteMatch(s, e, priority, route, coord_span, t_spans))

    return matches


def _locate_text_spans(tokens, kept_matches):
    """为单一文本字段计算每条命中的替换区间 (start, end)。

    保证推进单调性，避免重叠或倒退。找不到时回退到匹配阶段索引（防越界）。
    """
    spans = []
    curr = 0
    for s_step, e_step, priority, route, coord_span, matched_token in kept_matches:
        s = None
        try:
            s = curr + tokens[curr:].index(route.start_name)
        except ValueError:
            pass

        e = None
        if s is not None:
            # 优先在 s 之后寻找匹配到的 matched_token，找不到再尝试 route.end_name
            found_e, _, _ = _find_end_token_in_tokens(tokens, s, matched_token or route.end_name)
            if found_e is None and matched_token != route.end_name:
                found_e, _, _ = _find_end_token_in_tokens(tokens, s, route.end_name)
            e = found_e

        if s is None or e is None:
            # 回退：用 step 的索引，截断在合法范围内
            fallback_s = min(s_step, len(tokens) - 1) if tokens else 0
            fallback_e = min(e_step, len(tokens) - 1) if tokens else 0
            if fallback_s < curr or fallback_e < fallback_s:
                spans.append(None)
                continue
            s, e = fallback_s, fallback_e

        spans.append((s, e))
        curr = e + 1
    return spans


def _apply_to_step(tokens, matches, field_name):
    """按匹配阶段预先算好的区间，把文本字段替换为 fixed_step。"""
    if not tokens:
        return ""
    new_tokens = []
    i = 0
    for m in matches:
        span = m.text_spans.get(field_name)
        if span is None:
            continue
        s, e = span
        if s < i or e < s:
            continue
        new_tokens.extend(tokens[i:s])
        new_tokens.append(m.route.fixed_step)
        i = e + 1
    new_tokens.extend(tokens[i:])
    return "-".join(new_tokens)


def _apply_to_coords(coords, matches):
    """按匹配阶段预先记录的坐标区间，把命中段整体替换为 fixed_points.coordinates。

    coord_span 来自匹配阶段（先起点模糊定位、后终点精确定位）的原始坐标索引，
    替换阶段直接切片替换；多段匹配时从后往前替换，避免索引漂移。
    """
    new_coords = list(coords)
    for m in reversed(matches):
        span = m.coord_span
        if span is None:
            continue
        start_idx, end_idx = span
        if end_idx <= start_idx:
            continue
        fixed_points = m.route.fixed_points
        fixed_coords = (
            fixed_points.get("coordinates") if isinstance(fixed_points, dict) else fixed_points
        )
        if not fixed_coords or len(fixed_coords) < 2:
            continue
        new_coords = new_coords[:start_idx] + fixed_coords + new_coords[end_idx + 1:]
    return new_coords


def _apply_fixed_routes(payload):
    """请求体（原始路线数据）→ 匹配数据库固定路线 → 替换 originalStep / points。

    执行流程：
      ⓪ iskey 前置校验：仅 iskey == "1" 时处理，其它直接原样返回 + iscsdl=0；
      ① 校验 step / points.coordinates 必填；
      ② 按「先判定起点、再多级判定替换点」匹配固定路线；
      ③ 文本/坐标替换按匹配阶段算好的位置执行；
      ④ 响应返回 iscsdl=1、fixedStep、fixedPoints。
    """
    route = payload.get("route") or payload  # 兼容嵌套 route 或平铺结构

    # ⓪ iskey 前置校验
    iskey = payload.get("iskey")
    if iskey is None and isinstance(route, dict):
        iskey = route.get("iskey")
    if str(iskey) != "1":
        result = dict(route)
        result["iscsdl"] = 0
        return result

    step = route.get("step") or route.get("originalStep")
    original_step = route.get("originalStep") or route.get("step")
    points = route.get("points") or {}
    original_coords = points.get("coordinates") if isinstance(points, dict) else None
    if not step or not original_coords:
        return {"code": 400, "detail": "缺少原始路线：需要 step（或 originalStep）与 points.coordinates"}

    # 各文本字段拆分，预先用于计算替换区间
    step_tokens = _split_step(step)
    text_tokens = {
        "step": step_tokens if route.get("step") else None,
        "originalStep": _split_step(original_step),
        "uploadStep": _split_step(route["uploadStep"]) if route.get("uploadStep") else None,
    }

    matches = _match_routes(step_tokens, original_coords, text_tokens)
    if not matches:
        # 没有匹配到任何固定路线：原样返回，iscsdl=0
        result = dict(route)
        result["iscsdl"] = 0
        return result

    first = matches[0].route  # 第一条匹配的固定线路记录
    result = dict(route)
    # 按匹配阶段记录的区间直接替换
    if route.get("step"):
        result["step"] = _apply_to_step(text_tokens["step"], matches, "step")
    result["originalStep"] = _apply_to_step(text_tokens["originalStep"], matches, "originalStep")
    if route.get("uploadStep"):
        result["uploadStep"] = _apply_to_step(text_tokens["uploadStep"], matches, "uploadStep")
    result["points"] = {**points, "coordinates": _apply_to_coords(original_coords, matches)}
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
        处理：① iskey==1 校验；
             ② 拿 step 分段与 points 匹配数据库 fixed_route 表（先起点后替换点多级匹配）；
             ③ step / originalStep / uploadStep 处理一致：『起点…终点』段整段
                替换为数据库 fixed_step；
             ④ points.coordinates 中对应坐标段整体替换为 fixed_points.coordinates；
             ⑤ 响应追加 fixedStep / fixedPoints（匹配到的固定线路点位与坐标）；
             ⑥ 匹配标记 iscsdl=1，未匹配 iscsdl=0 原样返回。
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
