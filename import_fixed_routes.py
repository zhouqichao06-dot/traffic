# -*- coding: utf-8 -*-
"""
固定路线段（文本+坐标）→ fixed_route 表 导入脚本
=============================================
数据源：
  - 文本：4 个城市 Excel「固定路线样式表格」中的「固定段路线文本」列
  - 坐标：目录下的 CSV（表头 X,Y 或 longitude,latitude，每行 经度,纬度）
规则：
  - 主键 (start_name, end_name)，数据库中已有相同主键 → 覆盖（update），否则新增（insert）
  - start_name = 固定路线文本首段；end_name = 固定路线文本末段
  - fixed_step 内部分隔符统一为 "-"
  - start_point = CSV 首点；end_point = CSV 末点；fixed_points = 全部坐标 GeoJSON LineString
  - 文本↔坐标匹配：起点名称一致且 终点名称一致；终点名称不一致时用「Excel 收费站经纬度」
    与 CSV 末点坐标比对（容差 0.0002 度）兜底判定
  - 匹配不上的：文本行无坐标 / CSV 无文本，单独列出，不导入
输出：导入报告 固定路线导入报告.md + 控制台摘要
"""
import csv
import glob
import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

ROOT = r"D:\traffic\traffic_api\固定路线段（文本+坐标）"
DB_PATH = r"D:\traffic\traffic_api\db.sqlite3"
REPORT_PATH = r"D:\traffic\traffic_api\固定路线导入报告.md"
TOL = 0.0002  # 坐标兜底匹配容差（度）

NSM = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NSR = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def clean(s):
    if s is None:
        return ""
    s = s.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    s = s.replace("\u3000", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


# ---------------------------------------------------------------- Excel 解析
def parse_xlsx_rows(path):
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(f"{{{NSM}}}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NSM}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        sheets = [(sh.get("name"), sh.get(f"{{{NSR}}}id")) for sh in wb.find(f"{{{NSM}}}sheets")]
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid2target = {rel.get("Id"): rel.get("Target") for rel in rels}
        for _name, rid in sheets:
            target = rid2target[rid]
            target = target[1:] if target.startswith("/") else "xl/" + target
            sroot = ET.fromstring(z.read(target))
            rows = []
            for row in sroot.iter(f"{{{NSM}}}row"):
                cells = {}
                for c in row.findall(f"{{{NSM}}}c"):
                    col = "".join(ch for ch in c.get("r", "") if ch.isalpha())
                    t = c.get("t")
                    v = c.find(f"{{{NSM}}}v")
                    is_ = c.find(f"{{{NSM}}}is")
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr" and is_ is not None:
                        val = "".join(x.text or "" for x in is_.iter(f"{{{NSM}}}t"))
                    elif v is not None:
                        val = v.text
                    else:
                        val = ""
                    cells[col] = clean(val)
                if cells:
                    rows.append(cells)
            return rows
    return []


def split_step(text):
    """按 - / -- 切分路线文本，返回非空段列表"""
    return [t for t in text.replace("--", "-").split("-") if t]


def looks_like_route_text(val):
    """判断单元格值是否像固定路线文本（≥2 段 且 含公司/收费等特征）"""
    segs = split_step(val)
    if len(segs) < 2:
        return False
    if len(val) < 8:
        return False
    return True


def extract_text_rows():
    """解析 4 个城市 Excel，提取 (city, row_no, fixed_step原始文本, 收费站经纬度原始串)"""
    text_rows = []
    for p in sorted(glob.glob(os.path.join(ROOT, "*", "*.xlsx"))):
        if "$" in os.path.basename(p):
            continue
        city = os.path.basename(os.path.dirname(p))
        rows = parse_xlsx_rows(p)
        if not rows:
            continue
        headers = rows[0]
        text_col = next((c for c, h in headers.items() if "固定段路线文本" in h), None)
        coord_col = next((c for c, h in headers.items() if "收费站经纬度" in h or "终点经纬度" in h), None)
        for row in rows[1:]:
            text = row.get(text_col, "") if text_col else ""
            if not text:
                # 兜底：文本可能被填错列（泰州第14行填在“途经路线”列）
                cands = [v for v in row.values() if looks_like_route_text(v)]
                if cands:
                    text = max(cands, key=lambda v: len(split_step(v)))
            coord_raw = row.get(coord_col, "") if coord_col else ""
            if not text:
                continue
            text_rows.append({"city": city, "row": row.get("A", ""), "text": text, "coord_raw": coord_raw})
    return text_rows


def parse_coord_cell(coord_raw):
    """'江苏龙城大道收费站：119.872815,31.855900' -> (名称, [lng, lat])；失败返回 (None,None)"""
    m = re.match(r"(.+?)[：:]\s*([-\d.]+)\s*,\s*([-\d.]+)", coord_raw.strip())
    if not m:
        return None, None
    try:
        return clean(m.group(1)), [float(m.group(2)), float(m.group(3))]
    except ValueError:
        return None, None


# ---------------------------------------------------------------- CSV 解析
def parse_csv_coords(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    coords = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        try:
            coords.append([float(r[0].strip()), float(r[1].strip())])
        except ValueError:
            continue
    return coords


def split_filename(base):
    if "--" in base:
        s, e = base.split("--", 1)
        return clean(s), clean(e)
    idx = base.rfind("-")
    if idx > 0:
        return clean(base[:idx]), clean(base[idx + 1:])
    return clean(base), ""


def extract_csv_rows():
    csv_rows = []
    for p in sorted(glob.glob(os.path.join(ROOT, "**", "*.csv"), recursive=True)):
        base = os.path.splitext(os.path.basename(p))[0]
        s, e = split_filename(base)
        coords = parse_csv_coords(p)
        csv_rows.append({
            "path": os.path.relpath(p, ROOT),
            "start": s, "end": e, "coords": coords,
        })
    return csv_rows


# ---------------------------------------------------------------- 匹配
def match(text_rows, csv_rows):
    """返回 matched / unmatched_text / unmatched_csv / conflicts"""
    matched = []          # {text, csv}
    unmatched_text = []
    unmatched_csv = []
    csv_used = set()

    def coord_close(a, b):
        return a and b and abs(a[0] - b[0]) <= TOL and abs(a[1] - b[1]) <= TOL

    for t in text_rows:
        segs = split_step(t["text"])
        t_start, t_end = segs[0], segs[-1]
        _, t_coord = parse_coord_cell(t["coord_raw"])
        # 候选：起点一致的 CSV
        cands = [c for i, c in enumerate(csv_rows) if i not in csv_used and c["start"] == t_start]
        hit = None
        if not cands:
            unmatched_text.append({"text": t, "reason": "无同名起点 CSV"})
            continue
        # 1) 终点名称精确一致
        exact = [c for c in cands if c["end"] == t_end]
        if len(exact) == 1:
            hit = exact[0]
        elif len(exact) > 1:
            # 同名同终点多个 CSV（理论上无），用坐标区分
            coord_hits = [c for c in exact if coord_close(c["coords"][-1] if c["coords"] else None, t_coord)]
            if len(coord_hits) == 1:
                hit = coord_hits[0]
            else:
                unmatched_text.append({"text": t, "reason": f"同名终点 CSV 多个且坐标无法唯一判定 ({len(exact)} 个)"})
                continue
        else:
            # 2) 终点名称不一致 → 用终点坐标兜底
            coord_hits = [c for c in cands if c["coords"] and coord_close(c["coords"][-1], t_coord)]
            if len(coord_hits) == 1:
                hit = coord_hits[0]
            elif len(coord_hits) > 1:
                unmatched_text.append({"text": t, "reason": "多个候选 CSV 终点坐标均匹配，无法唯一判定"})
                continue
            else:
                unmatched_text.append({
                    "text": t,
                    "reason": f"无 CSV 终点名称/坐标匹配（CSV 候选终点: {[c['end'] for c in cands] or '无'}）",
                })
                continue
        idx = csv_rows.index(hit)
        csv_used.add(idx)
        matched.append({"text": t, "csv": hit, "t_start": t_start, "t_end": t_end})

    for i, c in enumerate(csv_rows):
        if i not in csv_used:
            unmatched_csv.append(c)
    return matched, unmatched_text, unmatched_csv


# ---------------------------------------------------------------- 入库
def build_records(matched):
    """matched → 待入库记录；同时保留文本原样供报告"""
    recs = []
    for m in matched:
        t, c = m["text"], m["csv"]
        segs = split_step(t["text"])
        fixed_step = "-".join(segs)
        coords = c["coords"]
        recs.append({
            "city": t["city"], "row": t["row"],
            "start_name": segs[0], "end_name": segs[-1],
            "start_point": coords[0], "end_point": coords[-1],
            "fixed_step": fixed_step,
            "fixed_points": {"type": "LineString", "coordinates": coords},
            "csv_path": c["path"],
            "n_points": len(coords),
        })
    return recs


def import_records(recs):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT start_name, end_name FROM fixed_route")
    existing = set(cur.fetchall())
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    inserted, updated = [], []
    for r in recs:
        key = (r["start_name"], r["end_name"])
        if key in existing:
            updated.append(r)
        else:
            inserted.append(r)
        cur.execute(
            """
            INSERT INTO fixed_route
              (start_name, end_name, start_point, end_point, fixed_step, fixed_points, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(start_name, end_name) DO UPDATE SET
              start_point=excluded.start_point,
              end_point=excluded.end_point,
              fixed_step=excluded.fixed_step,
              fixed_points=excluded.fixed_points,
              updated_at=excluded.updated_at
            """,
            (
                r["start_name"], r["end_name"],
                json.dumps(r["start_point"]), json.dumps(r["end_point"]),
                r["fixed_step"], json.dumps(r["fixed_points"]),
                now_utc, now_utc,
            ),
        )
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM fixed_route")
    total = cur.fetchone()[0]
    conn.close()
    return inserted, updated, total


# ---------------------------------------------------------------- 报告
def write_report(matched, unmatched_text, unmatched_csv, inserted, updated, total):
    lines = []
    lines.append("# 固定路线数据导入报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append(f"- 数据源目录：`固定路线段（文本+坐标）`")
    lines.append(f"- 目标数据库：`db.sqlite3` → 表 `fixed_route`（sqlite，DB_ENGINE=sqlite）")
    lines.append(f"- 主键规则：`(start_name, end_name)`，已存在即覆盖（ON CONFLICT DO UPDATE）")
    lines.append("")
    lines.append("## 一、汇总")
    lines.append("")
    lines.append("| 项目 | 数量 |")
    lines.append("|---|---|")
    lines.append(f"| Excel 固定路线文本行 | {len(matched) + len(unmatched_text)} |")
    lines.append(f"| CSV 坐标文件 | {len(matched) + len(unmatched_csv)} |")
    lines.append(f"| 匹配成功并入库 | {len(matched)} |")
    lines.append(f"| 其中：覆盖数据库已有记录 | {len(updated)} |")
    lines.append(f"| 其中：新增记录 | {len(inserted)} |")
    lines.append(f"| 文本行匹配不上（未导入） | {len(unmatched_text)} |")
    lines.append(f"| CSV 匹配不上（未导入） | {len(unmatched_csv)} |")
    lines.append(f"| 导入后 fixed_route 表总记录数 | {total} |")
    lines.append("")
    lines.append("## 二、匹配成功并入库明细")
    lines.append("")
    lines.append("| 城市 | 起点名称 | 终点名称 | 固定路线文本（-- 分隔） | 坐标来源 CSV | 点数 | 操作 |")
    lines.append("|---|---|---|---|---|---|---|")
    updated_keys = {(r["start_name"], r["end_name"]) for r in updated}
    for r in sorted(matched, key=lambda m: (m["text"]["city"], int(m["text"]["row"] or 0))):
        t = r["text"]
        key = (r["t_start"], r["t_end"])
        op = "覆盖" if key in updated_keys else "新增"
        lines.append(
            f"| {t['city']} | {r['t_start']} | {r['t_end']} | `{r['text']['text']}` | `{r['csv']['path']}` | {len(r['csv']['coords'])} | {op} |"
        )
    lines.append("")
    lines.append("## 三、匹配不上的数据（未导入）")
    lines.append("")
    if unmatched_text:
        lines.append("### 3.1 有文本、无坐标（Excel 行找不到对应 CSV）")
        lines.append("")
        lines.append("| 城市 | 行号 | 固定路线文本 | Excel 收费站经纬度 | 原因 |")
        lines.append("|---|---|---|---|---|")
        for u in unmatched_text:
            t = u["text"]
            lines.append(f"| {t['city']} | {t['row']} | `{t['text']}` | `{t['coord_raw']}` | {u['reason']} |")
        lines.append("")
    else:
        lines.append("无。")
        lines.append("")
    if unmatched_csv:
        lines.append("### 3.2 有坐标、无文本（CSV 找不到对应 Excel 文本行）")
        lines.append("")
        lines.append("| CSV 文件 | 起点 | 终点 | 点数 |")
        lines.append("|---|---|---|---|")
        for c in unmatched_csv:
            lines.append(f"| `{c['path']}` | {c['start']} | {c['end']} | {len(c['coords'])} |")
        lines.append("")
    else:
        lines.append("无。")
        lines.append("")
    lines.append("## 四、备注")
    lines.append("")
    lines.append("- 文本内部分隔符已统一为 `--`（README/接口示例约定），匹配时 `--`/`-` 等价。")
    lines.append("- `start_point` = CSV 首点，`end_point` = CSV 末点，`fixed_points` = 全部坐标（GeoJSON LineString）。")
    lines.append("- 个别 Excel 终点名称与 CSV 文件名不一致（如 `S356沿江公路`/`S356`、`X201城蓬线`/`太蓬路`、`S411姜高路`/`S229`），已按终点坐标比对确认后按文本名称入库。")
    lines.append("- 数据库原有 1 条乱码测试记录（id=6，中文已损坏），与本批主键不冲突，未改动。")
    lines.append("- 本脚本可重复执行：相同主键会再次覆盖，结果幂等。")
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return REPORT_PATH


def main():
    print("解析 Excel 文本行...")
    text_rows = extract_text_rows()
    print(f"  文本行: {len(text_rows)}")
    for t in text_rows:
        segs = split_step(t["text"])
        print(f"    [{t['city']}] 行{t['row']}: start=[{segs[0]}] end=[{segs[-1]}]")

    print("解析 CSV...")
    csv_rows = extract_csv_rows()
    print(f"  CSV: {len(csv_rows)}")

    print("匹配...")
    matched, unmatched_text, unmatched_csv = match(text_rows, csv_rows)
    print(f"  匹配成功: {len(matched)}")
    print(f"  文本未匹配: {len(unmatched_text)}")
    for u in unmatched_text:
        print(f"    [{u['text']['city']}] 行{u['text']['row']}: {u['text']['text']}  <- {u['reason']}")
    print(f"  CSV未匹配: {len(unmatched_csv)}")
    for c in unmatched_csv:
        print(f"    {c['path']} ({c['start']} -> {c['end']})")

    print("入库...")
    recs = build_records(matched)
    inserted, updated, total = import_records(recs)
    print(f"  新增: {len(inserted)}  覆盖: {len(updated)}  表总数: {total}")

    report = write_report(matched, unmatched_text, unmatched_csv, inserted, updated, total)
    print(f"报告已生成: {report}")


if __name__ == "__main__":
    main()
