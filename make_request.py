# -*- coding: utf-8 -*-
"""根据 数据.json 生成固定路线接口的测试请求文件 request.json（纯原始路线数据，不含固定路线字段）"""
import json

with open(r"F:\traffic\数据.json", encoding="utf-8") as f:
    raw = json.load(f)

payload = dict(raw)  # 数据.json 全部字段平铺为原始路线

with open(r"F:\traffic\request.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)

print("已生成 F:\\traffic\\request.json（仅原始路线数据，不含固定路线字段）")
print("原始坐标点数:", len(raw["points"]["coordinates"]))
print("step:", payload.get("step"))
