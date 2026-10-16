# -*- coding: utf-8 -*-
"""本地验证 /api/v1/fixed-routes/ 接口（新版：请求只发原始路线，固定路线数据来自数据库匹配）"""
import json
import os
import sys

sys.path.insert(0, r"F:\traffic\traffic_api")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "traffic_api.settings")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "*")

import django

django.setup()

from django.conf import settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from apps.core.models import FixedRoute  # noqa: E402
from apps.core.views import _find_point_index  # noqa: E402

TOKEN = settings.API_TOKEN

with open(r"F:\traffic\数据.json", encoding="utf-8") as f:
    raw = json.load(f)

coords = raw["points"]["coordinates"]
start_pt = coords[0]  # [118.751556, 34.161008] 扎下镇

# 找到用户给出的终点坐标 [118.743252, 34.16809] 在原始坐标中的索引
end_idx = _find_point_index(coords, [118.743252, 34.16809])
assert end_idx is not None, "数据.json 中未找到终点坐标 [118.743252, 34.16809]"
end_pt = coords[end_idx]
print(f"终点坐标 {end_pt} 位于原始坐标索引 {end_idx}")

# 固定路线数据（模拟数据库中已存的记录）
fixed_step = "扎下镇--运河路--玉龙南路--龙城大道--江苏沭阳北收费站"
fixed_coords = [
    start_pt,
    [round(start_pt[0] + (end_pt[0] - start_pt[0]) * 0.25, 6), round(start_pt[1] + (end_pt[1] - start_pt[1]) * 0.25, 6)],
    [round(start_pt[0] + (end_pt[0] - start_pt[0]) * 0.50, 6), round(start_pt[1] + (end_pt[1] - start_pt[1]) * 0.50, 6)],
    [round(start_pt[0] + (end_pt[0] - start_pt[0]) * 0.75, 6), round(start_pt[1] + (end_pt[1] - start_pt[1]) * 0.75, 6)],
    end_pt,
]
fixed_points = {"type": "LineString", "coordinates": fixed_coords}

# ---- 0. 准备数据库：清空并插入一条固定路线记录 ----
FixedRoute.objects.all().delete()
FixedRoute.objects.update_or_create(
    start_name="扎下镇",
    end_name="江苏沭阳北收费站",
    defaults={
        "start_point": start_pt,
        "end_point": end_pt,
        "fixed_step": fixed_step,
        "fixed_points": fixed_points,
    },
)
print("0) 数据库已插入固定路线: 扎下镇 → 江苏沭阳北收费站")

payload = dict(raw)  # 数据.json 平铺为原始路线（不含任何固定路线字段）
assert "fixedStep" not in payload and "startName" not in payload

client = APIClient()

# ---- 1. 无 Token -> 401 ----
r = client.post("/api/v1/fixed-routes/", data=json.dumps(payload), content_type="application/json")
print("1) 无Token:", r.status_code, "(期望 401)")
assert r.status_code == 401

# ---- 2. 带 Token POST 纯原始数据 -> 200 ----
client.credentials(HTTP_AUTHORIZATION="Token " + TOKEN)
r = client.post("/api/v1/fixed-routes/", data=json.dumps(payload), content_type="application/json")
print("2) 带Token:", r.status_code)
assert r.status_code == 200, r.content
res = r.json()

# 2.1 step / uploadStep 保持不变
print("   step 未变:", res["step"] == payload["step"])
assert res["step"] == payload["step"]
print("   uploadStep 未变:", res["uploadStep"] == payload["uploadStep"])
assert res["uploadStep"] == payload["uploadStep"]

# 2.2 originalStep 只替换匹配段
expected_original = "扎下镇--运河路--玉龙南路--龙城大道--江苏沭阳北收费站-G2-G30-G3-S69-江苏丰县北收费站-G237济宁线-G518-X208王宋线-徐州能动电动车工业园"
print("   originalStep:", res["originalStep"])
print("   originalStep 符合预期:", res["originalStep"] == expected_original)
assert res["originalStep"] == expected_original

# 2.3 points 坐标段替换
nc = res["points"]["coordinates"]
expect_len = len(coords) - (end_idx + 1) + len(fixed_coords)
print(f"   坐标点数: {len(coords)} -> {len(nc)} (期望 {expect_len})")
assert len(nc) == expect_len
assert nc[:len(fixed_coords)] == fixed_coords, "替换段前 N 点应为固定路线坐标"
assert nc[len(fixed_coords):] == coords[end_idx + 1:], "替换段后应接原始路线剩余点位"
print("   坐标替换段校验: OK")

# 2.4 响应不再追加 fixedStep / fixedPoints（仅返回替换后的原始路线）
print("   响应不含 fixedStep:", "fixedStep" not in res)
print("   响应不含 fixedPoints:", "fixedPoints" not in res)
assert "fixedStep" not in res
assert "fixedPoints" not in res

# ---- 3. GET 列表 -> 1 条 ----
r = client.get("/api/v1/fixed-routes/")
print("3) GET 列表条数:", len(r.json()), "(期望 1)")
assert len(r.json()) == 1
print("   GET[0]:", r.json()[0]["startName"], "→", r.json()[0]["endName"])

# ---- 4. 未匹配场景 -> 原样返回、无 fixedStep ----
payload2 = dict(raw)
payload2["step"] = "苏州工业园区-XZ04天津北路-G205山深线-江苏沭阳北收费站-G2-G30-G3-S69-江苏丰县北收费站-G237济宁线-G518-X208王宋线-徐州能动电动车工业园"
r = client.post("/api/v1/fixed-routes/", data=json.dumps(payload2), content_type="application/json")
print("4) 未匹配:", r.status_code, "含 fixedStep:", "fixedStep" in r.json())
assert r.status_code == 200
assert "fixedStep" not in r.json()
assert r.json()["step"] == payload2["step"]

# ---- 5. 清理测试数据 ----
FixedRoute.objects.all().delete()
print("5) 已清理测试数据")

print("\n全部断言通过 ✔")
