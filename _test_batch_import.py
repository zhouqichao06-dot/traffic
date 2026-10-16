# -*- coding: utf-8 -*-
"""batch_import.py 纯逻辑单测：用真实总表 Excel + CSV 验证解析与终点一致性校验"""
import glob
import json
import os
import sys

sys.path.insert(0, r"D:\traffic\traffic_api\apps\core")
import batch_import as bi

ROOT = r"D:\traffic\traffic_api\固定路线段（文本+坐标）"
ok = []
fail = []


def check(name, cond, extra=""):
    if cond:
        ok.append(name)
        print("PASS", name, extra)
    else:
        fail.append(name)
        print("FAIL", name, extra)


# ---------- 1. 真实 Excel 总表解析 ----------
total = 0
per_city = {}
for p in sorted(glob.glob(os.path.join(ROOT, "*", "*.xlsx"))):
    if "$" in os.path.basename(p):
        continue
    city = os.path.basename(os.path.dirname(p))
    with open(p, "rb") as f:
        rows, errs = bi.parse_excel_rows(f.read())
    per_city[city] = (len(rows), len(errs))
    total += len(rows)
    print(f"  [{city}] 解析行数={len(rows)} 错误行={len(errs)}")
    for r in rows:
        print("    ", r["start_name"], "->", r["end_name"], "| toll:", r["toll_point"], "| seg:", r["seg_count"])

check("常州=2", per_city.get("常州", (0, 0))[0] == 2, str(per_city.get("常州")))
check("昆山=8", per_city.get("昆山", (0, 0))[0] == 8, str(per_city.get("昆山")))
check("泰州=14", per_city.get("泰州", (0, 0))[0] == 14, str(per_city.get("泰州")))
check("盐城=25", per_city.get("盐城", (0, 0))[0] == 25, str(per_city.get("盐城")))
check("总行数=49", total == 49, str(total))

# 泰州第14行（文本错列兜底）应解析出 泰州市姜堰华源混凝土构件有限公司 -> S411姜高路
tz_rows = []
with open(os.path.join(ROOT, "泰州", "泰州 固定路线样式表格--1010.xlsx"), "rb") as f:
    tz_rows, tz_errs = bi.parse_excel_rows(f.read())
hit = [r for r in tz_rows if r["start_name"] == "泰州市姜堰华源混凝土构件有限公司" and r["end_name"] == "S411姜高路"]
check("泰州错列文本兜底", len(hit) == 1 and hit[0]["toll_point"] == [120.170466, 32.455747], json.dumps(hit, ensure_ascii=False))

# 昆山 X201城蓬线（CSV 文件名叫太蓬路，坐标一致）
ks_rows = []
with open(os.path.join(ROOT, "昆山", "昆山 固定路线样式表格--1014.xlsx"), "rb") as f:
    ks_rows, _ = bi.parse_excel_rows(f.read())
hit2 = [r for r in ks_rows if r["end_name"] == "X201城蓬线"]
check("昆山X201城蓬线toll", len(hit2) == 1 and hit2[0]["toll_point"] == [121.125212, 31.414668], json.dumps(hit2, ensure_ascii=False))

# 泰州中建钢构行1（坐标是错误值，无匹配CSV）应仍能解析出
hit3 = [r for r in tz_rows if r["start_name"] == "中建钢构江苏有限公司" and r["end_name"] == "S356沿江公路"]
check("泰州中建钢构S356沿江公路行", len(hit3) == 1 and hit3[0]["toll_point"] == [119.872815, 31.8559], json.dumps(hit3, ensure_ascii=False))

# ---------- 2. 收费站坐标一致性校验（任意坐标点匹配） ----------
def load_csv(rel):
    """用 glob 匹配（部分文件名含零宽字符，不能用字面路径）"""
    cands = glob.glob(os.path.join(ROOT, rel)) or glob.glob(os.path.join(ROOT, rel.replace("--", "*--")))
    if not cands:
        raise FileNotFoundError(rel)
    p = cands[0]
    with open(p, "r", encoding="utf-8-sig") as f:
        return bi.parse_csv_coords(f.read())

# 2.1 一致（末点命中）：大明重工 CSV vs 泰州行2 收费站经纬度
coords = load_csv(r"泰州\数据\大明重工有限公司--S356.csv")
pt, dev = bi.find_matching_point(coords, [120.211367, 31.963333])
check("大明重工一致", pt == [120.211367, 31.963333] and dev == 0.0, f"pt={pt} dev={dev}")

# 2.2 一致（末点命中）：华源 S229 CSV vs 泰州行14 S411姜高路 收费站经纬度
coords = load_csv(r"泰州\数据\泰州市姜堰华源混凝土构件有限公司--S229.csv")
pt, dev = bi.find_matching_point(coords, [120.170466, 32.455747])
check("华源S229↔S411姜高路一致", pt == [120.170466, 32.455747] and dev == 0.0, f"pt={pt} dev={dev}")

# 2.3 无任何点匹配：中建钢构--S356.csv vs 错误收费站坐标（泰州行1 的复制粘贴值）→ 应拒绝
coords = load_csv(r"泰州\数据\中建钢构江苏有限公司--S356.csv")
pt, dev = bi.find_matching_point(coords, [119.872815, 31.8559])
check("中建钢构无点匹配被拒", pt is None and dev > 0.3, f"pt={pt} dev={dev}")

# 2.4 容差内一致：常州西电 CSV vs 总表收费站经纬度（差异约0.0002°）
coords = load_csv(r"常州\常州西电变压器有限责任公司--江苏龙城大道收费站.csv")
pt, dev = bi.find_matching_point(coords, [119.872815, 31.855900])
check("常州西电容差内一致", pt is not None, f"pt={pt} dev={dev}")

# 2.5 完全一致的常州东智
coords = load_csv(r"常州\常州东智变压器有限公司--江苏龙城大道收费站.csv")
pt, dev = bi.find_matching_point(coords, [119.872815, 31.8559])
check("常州东智完全一致", pt is not None and dev == 0.0, f"pt={pt} dev={dev}")

# 2.6 明显不一致：东台 CSVs vs 盐城西收费站 → 拒绝
coords = load_csv(r"盐城\东台市\江苏德东电机有限公司--江苏东台收费站.csv")
pt, dev = bi.find_matching_point(coords, [120.09929, 33.317655])
check("东台CSV vs 盐城西收费站无点匹配", pt is None, f"pt={pt} dev={dev}")

# 2.7 新规则核心：末点不一致、但中间某个坐标点与收费站一致 → 应放行
coords = load_csv(r"泰州\数据\中建钢构江苏有限公司--S356.csv")
mid_pt = coords[1]  # 第二个点作为收费站坐标
pt, dev = bi.find_matching_point(coords, mid_pt)
check("中间点命中放行（末点不同）", pt == mid_pt and dev == 0.0,
      f"pt={pt} last={coords[-1]} dev={dev}")
check("末点确实与toll不同", bi.coord_distance(coords[-1], mid_pt) > 0.0002)

# 2.8 构造用例：序列中有一个点命中即通过
coords2 = [[1.0, 1.0], [120.5, 31.5], [3.0, 3.0]]
pt, dev = bi.find_matching_point(coords2, [120.5, 31.5])
check("构造命中", pt == [120.5, 31.5] and dev == 0.0)
pt, dev = bi.find_matching_point(coords2, [2.0, 2.0])
check("构造无命中", pt is None and dev is not None)

# 2.9 归一化：尾0 字符串坐标 → 数值（"119.872940" → 119.87294）
check("normalize去尾0", bi.normalize_coord("119.872940") == 119.87294)
check("normalize数值", bi.normalize_coord(119.872940) == 119.87294)
check("normalize非法", bi.normalize_coord("abc") is None)

# 2.10 数据库尾0字符串形式 vs 数值输入 → 仍精确匹配
coords = load_csv(r"常州\常州东智变压器有限公司--江苏龙城大道收费站.csv")
pt, dev = bi.find_matching_point(coords, ["119.8728150", "31.855900"])
check("字符串尾0toll匹配数值CSV", pt == [119.872815, 31.8559] and dev == 0.0, f"pt={pt} dev={dev}")

# ---------- 3. CSV 解析边界 ----------
check("表头X,Y跳过", bi.parse_csv_coords("X,Y\n1.0,2.0\n3.0,4.0\n") == [[1.0, 2.0], [3.0, 4.0]])
check("表头longitude,latitude跳过", bi.parse_csv_coords("longitude,latitude\n1,2\n3,4\n") == [[1.0, 2.0], [3.0, 4.0]])
check("BOM+制表符", bi.parse_csv_coords("\ufeffX\tY\n1\t2\n3\t4\n") == [[1.0, 2.0], [3.0, 4.0]])
try:
    bi.parse_csv_coords("1,2\n")
    check("少于2点报错", False)
except ValueError:
    check("少于2点报错", True)

# ---------- 4. 收费站经纬度解析 ----------
check("命名格式", bi.parse_toll_cell("江苏龙城大道收费站：119.872815,31.855900") == ("江苏龙城大道收费站", [119.872815, 31.8559]))
check("全角冒号", bi.parse_toll_cell("G328：120.151175,32.484398") == ("G328", [120.151175, 32.484398]))
check("裸坐标", bi.parse_toll_cell("120.151175,32.484398") == ("", [120.151175, 32.484398]))
check("空格", bi.parse_toll_cell("江苏大丰收费站 ：119.718556, 33.725997")[1] == [119.718556, 33.725997])
check("空串", bi.parse_toll_cell("") == (None, None))

print("\n===== 结果: %d 通过, %d 失败 =====" % (len(ok), len(fail)))
sys.exit(1 if fail else 0)
