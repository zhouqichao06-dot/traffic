from django.db import models


class FixedRoute(models.Model):
    """固定路线表：增加/替换固定路线（按 起点名称+终点名称 唯一，重复提交即覆盖）。"""

    start_name = models.CharField("起点名称", max_length=100)
    end_name = models.CharField("终点名称", max_length=100)
    start_point = models.JSONField("起点坐标", help_text="[经度, 纬度]")
    end_point = models.JSONField("终点坐标", help_text="[经度, 纬度]")
    fixed_step = models.TextField(
        "固定路线",
        help_text="示例：扎下镇--运河路--玉龙南路--龙城大道--江苏沭阳北收费站",
    )
    fixed_points = models.JSONField(
        "固定路线坐标",
        help_text='GeoJSON LineString：{"type": "LineString", "coordinates": [[lng, lat], ...]}',
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "fixed_route"
        verbose_name = "固定路线"
        verbose_name_plural = "固定路线"
        constraints = [
            models.UniqueConstraint(
                fields=["start_name", "end_name"], name="uniq_fixed_route_start_end"
            )
        ]

    def __str__(self):
        return f"{self.start_name} → {self.end_name}"
