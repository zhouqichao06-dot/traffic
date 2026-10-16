# -*- coding: utf-8 -*-
"""批量导入纯逻辑：Excel（总表）解析 + CSV 终点坐标一致性校验。

本模块不依赖 Django/ORM，可独立单测；视图层（admin_views.py）只负责
取文件、调本模块纯函数、读写数据库。
"""
import io
import re
import zipfile
from xml.etree import ElementTree as ET

#: 终点坐标一致性容差（度）。约 0.0002° ≈ 20 米，用于允许来源间的取整差异。
BATCH_COORD_TOL = 0.0002

_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def clean_text(s):
    """去零宽字符/全角空格/BOM，压缩内部空白，去首尾空白。"""
    if s is None:
        return ""
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        s = s.replace(ch, "")
    return re.sub(r"\s+", " ", s.replace("\u3000", " ")).strip()


def split_step_text(text):
    """路线文本按 - / -- 切分，返回非空段列表。"""
    return [t for t in clean_text(text).replace("--", "-").split("-") if t]


def looks_like_route_text(val):
    segs = split_step_text(val)
    return len(segs) >= 2 and len(clean_text(val)) >= 8


# ---------------------------------------------------------------- Excel（xlsx）解析
def _parse_xlsx_sheet(data):
    """解析 xlsx 字节流：返回所有工作表里第一个含『固定段路线文本』表头的工作表单元格列表。

    单元格以 {列字母: 清洗后文本} 形式返回（仅含非空单元格）。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("文件不是有效的 .xlsx（zip）格式")
    shared = []
    if "xl/sharedStrings.xml" in zf.namelist():
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        for si in root.iter("{%s}si" % _XLSX_NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % _XLSX_NS)))
    try:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        sheet_list = [
            (sh.get("name"), sh.get("{%s}id" % _XLSX_REL))
            for sh in wb.iter("{%s}sheet" % _XLSX_NS)
        ]
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    except KeyError as e:
        raise ValueError("xlsx 结构缺失必要文件：%s" % e)
    rid2target = {rel.get("Id"): rel.get("Target") for rel in rels}
    for _name, rid in sheet_list:
        target = (rid2target.get(rid) or "").lstrip("/")
        if not target:
            continue
        if not target.startswith("xl/"):
            target = "xl/" + target
        try:
            root = ET.fromstring(zf.read(target))
        except KeyError:
            continue
        rows = []
        for row in root.iter("{%s}row" % _XLSX_NS):
            cells = {}
            for c in row.iter("{%s}c" % _XLSX_NS):
                col = "".join(ch for ch in (c.get("r") or "") if ch.isalpha())
                if not col:
                    continue
                t = c.get("t")
                v = c.find("{%s}v" % _XLSX_NS)
                is_ = c.find("{%s}is" % _XLSX_NS)
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif t == "inlineStr" and is_ is not None:
                    val = "".join(x.text or "" for x in is_.iter("{%s}t" % _XLSX_NS))
                elif v is not None:
                    val = v.text
                else:
                    val = ""
                cells[col] = clean_text(val)
            if cells:
                rows.append(cells)
        return rows
    return []


def parse_toll_cell(raw):
    """解析收费站经纬度单元格。

    支持两种写法：
      ① '江苏龙城大道收费站：119.872815,31.855900'  → ("江苏龙城大道收费站", [119.872815, 31.8559])
      ② 裸 '119.872815,31.855900'                    → ("", [119.872815, 31.8559])
    解析失败返回 (None, None)。
    """
    s = clean_text(raw)
    if not s:
        return None, None
    m = re.match(
        r"(.+?)[：:]\s*([-+]?\d+(?:\.\d+)?)\s*[,，]\s*([-+]?\d+(?:\.\d+)?)",
        s,
    )
    if m:
        try:
            return clean_text(m.group(1)), [float(m.group(2)), float(m.group(3))]
        except ValueError:
            pass
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", s)
    if len(nums) >= 2:
        try:
            return "", [float(nums[0]), float(nums[1])]
        except ValueError:
            pass
    return None, None


def parse_excel_rows(data):
    """解析总表 Excel → (rows, errors)。

    rows: [{row, fixed_step, start_name, end_name, seg_count, toll_name, toll_point, toll_raw}]
    errors: [{row, text, reason}]（有文本但无法切出起点/终点的行）
    """
    sheet_rows = _parse_xlsx_sheet(data)
    # 定位表头列
    text_col = None
    toll_col = None
    for cells in sheet_rows:
        tc = next((c for c, v in cells.items() if "固定段路线文本" in v), None)
        oc = next((c for c, v in cells.items() if "收费站经纬度" in v), None)
        if tc is not None:
            text_col = tc
            toll_col = oc
            break
    if text_col is None:
        raise ValueError("未找到表头列『固定段路线文本』")

    rows, errors = [], []
    started = False
    for cells in sheet_rows:
        if not started:
            if "固定段路线文本" in cells.get(text_col, ""):
                started = True
            continue
        text = cells.get(text_col, "")
        if not text:
            # 兜底：文本被填错列时（如泰州样例曾把文本填进“途经路线”列）
            cands = [v for v in cells.values() if looks_like_route_text(v)]
            if cands:
                text = max(cands, key=lambda v: len(split_step_text(v)))
        if not text:
            continue
        segs = split_step_text(text)
        if len(segs) < 2:
            errors.append({
                "row": cells.get("A", ""),
                "text": text,
                "reason": "段数 < 2，无法切出起点/终点",
            })
            continue
        toll_name, toll_point = parse_toll_cell(cells.get(toll_col, "")) if toll_col else (None, None)
        rows.append({
            "row": cells.get("A", ""),
            "fixed_step": "-".join(segs),
            "start_name": segs[0],
            "end_name": segs[-1],
            "seg_count": len(segs),
            "toll_name": toll_name or "",
            "toll_point": toll_point,
            "toll_raw": cells.get(toll_col, ""),
        })
    return rows, errors


# ---------------------------------------------------------------- CSV 坐标解析
def is_header(x, y):
    xl = (x or "").lower()
    yl = (y or "").lower()
    return (
        xl in ("x", "lng", "lon", "longitude", "经度", "经度(")
        and yl in ("y", "lat", "latitude", "纬度")
    )


def parse_csv_coords(text):
    """解析 CSV 文本为坐标数组：每行『经度,纬度』，自动跳过表头与空行。"""
    text = (text or "").lstrip("\ufeff")
    coords = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace("\t", ",").split(",")]
        if len(parts) != 2:
            raise ValueError("CSV 行格式错误（应为 经度,纬度）：%s" % line)
        if is_header(parts[0], parts[1]):
            continue
        try:
            lng, lat = float(parts[0]), float(parts[1])
        except ValueError:
            raise ValueError("CSV 行坐标非数字：%s" % line)
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise ValueError("CSV 行坐标超出合法范围：%s" % line)
        coords.append([lng, lat])
    if len(coords) < 2:
        raise ValueError("CSV 中至少需要 2 个坐标点")
    return coords


# ---------------------------------------------------------------- 收费站坐标一致性校验
def normalize_coord(value):
    """把坐标数值归一化为 float（去掉无效尾 0）。

    如 "119.872940" → 119.87294、119.872940 → 119.87294，非数字返回 None。
    匹配前统一归一化，避免数据库/输入中尾 0 或字符串形式导致的比对差异。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coord_distance(a, b):
    """两点欧氏距离；任一点含非法坐标时返回 None。"""
    a_lng, a_lat = normalize_coord(a[0]), normalize_coord(a[1])
    b_lng, b_lat = normalize_coord(b[0]), normalize_coord(b[1])
    if None in (a_lng, a_lat, b_lng, b_lat):
        return None
    return ((a_lng - b_lng) ** 2 + (a_lat - b_lat) ** 2) ** 0.5


def find_matching_point(csv_coords, toll_point, tol=BATCH_COORD_TOL):
    """在 CSV 坐标序列中查找与总表收费站经纬度一致的坐标点。

    规则：只要 CSV 中存在**任意一个**坐标点与 toll_point 偏差 ≤ tol 即视为一致。
    匹配前坐标统一转 float 归一化（去掉无效尾 0），数据库中的 119.872940 与
    119.87294 视为同一坐标。
    返回 (matched_point, min_deviation)：matched_point 为命中的坐标点（无匹配为 None），
    min_deviation 为全序列中的最小偏差。
    """
    if not toll_point or len(toll_point) < 2:
        return None, None
    toll = [normalize_coord(toll_point[0]), normalize_coord(toll_point[1])]
    if None in toll:
        return None, None
    best_pt, best_dev = None, None
    for c in csv_coords:
        dev = coord_distance(c, toll)
        if dev is None:
            continue
        if best_dev is None or dev < best_dev:
            best_dev = dev
            best_pt = c
        if dev <= tol:
            return c, round(dev, 6)
    return None, (round(best_dev, 6) if best_dev is not None else None)
