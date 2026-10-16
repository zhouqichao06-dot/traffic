"""管理控制台接口回归测试：登录 + FixedRoute 增删改查（CSV 导入 / 自动起终点坐标）。

运行：.venv\\Scripts\\python manage.py test apps.core.tests_admin -v 2
"""
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from .models import FixedRoute

CSV_TEXT = (
    "X,Y\n"
    "118.751556,34.161008\n"
    "118.749018,34.200000\n"
    "118.748198,34.200000\n"
    "118.747685,34.200000\n"
    "118.747401,34.200000\n"
    "118.743252,34.16809\n"
)
FIRST = [118.751556, 34.161008]
LAST = [118.743252, 34.16809]


class AdminConsoleTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = settings.API_TOKEN

    def auth(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token)

    # ---------------- 登录 ----------------

    def test_login_ok(self):
        r = self.client.post(
            "/api/v1/admin/login/",
            {"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["token"], self.token)

    def test_login_wrong(self):
        r = self.client.post(
            "/api/v1/admin/login/",
            {"username": "admin", "password": "wrong-pass"},
            format="json",
        )
        self.assertEqual(r.status_code, 401)

    def test_login_requires_auth_not(self):
        # 登录接口免鉴权
        r = self.client.post(
            "/api/v1/admin/login/",
            {"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
            format="json",
        )
        self.assertEqual(r.status_code, 200)

    # ---------------- 鉴权门 ----------------

    def test_manage_requires_token(self):
        r = self.client.get("/api/v1/fixed-routes/manage/")
        self.assertEqual(r.status_code, 401)

    # ---------------- 新增（CSV 文本） ----------------

    def test_create_from_csv_text(self):
        self.auth()
        r = self.client.post(
            "/api/v1/fixed-routes/manage/",
            {
                "start_name": "扎下镇",
                "end_name": "江苏沭阳北收费站",
                "fixed_step": "扎下镇--运河路--玉龙南路--龙城大道--江苏沭阳北收费站",
                "fixed_points": CSV_TEXT,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["start_point"], FIRST)   # 自动取第一个坐标
        self.assertEqual(r.data["end_point"], LAST)      # 自动取最后一个坐标
        self.assertEqual(r.data["fixed_points"]["type"], "LineString")
        self.assertEqual(len(r.data["fixed_points"]["coordinates"]), 6)
        self.assertTrue(
            FixedRoute.objects.filter(start_name="扎下镇", end_name="江苏沭阳北收费站").exists()
        )

    # ---------------- 新增（坐标数组 / LineString dict） ----------------

    def test_create_from_coords_array(self):
        self.auth()
        coords = [[120.0, 31.0], [120.1, 31.1]]
        r = self.client.post(
            "/api/v1/fixed-routes/manage/",
            {"start_name": "A", "end_name": "B", "fixed_step": "A--B", "fixed_points": coords},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["start_point"], [120.0, 31.0])
        self.assertEqual(r.data["end_point"], [120.1, 31.1])

    def test_create_from_linestring_dict(self):
        self.auth()
        fp = {"type": "LineString", "coordinates": [[119.9, 31.8], [119.95, 31.85]]}
        r = self.client.post(
            "/api/v1/fixed-routes/manage/",
            {"start_name": "C", "end_name": "D", "fixed_step": "C--D", "fixed_points": fp},
            format="json",
        )
        self.assertEqual(r.status_code, 201)

    # ---------------- 必填与重复 ----------------

    def test_create_missing_fields(self):
        self.auth()
        r = self.client.post(
            "/api/v1/fixed-routes/manage/",
            {"start_name": "", "end_name": "B", "fixed_step": "x", "fixed_points": CSV_TEXT},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_create_duplicate(self):
        self.auth()
        body = {"start_name": "扎下镇", "end_name": "江苏沭阳北收费站",
                "fixed_step": "a--b", "fixed_points": CSV_TEXT}
        self.assertEqual(self.client.post("/api/v1/fixed-routes/manage/", body, format="json").status_code, 201)
        r = self.client.post("/api/v1/fixed-routes/manage/", body, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("已存在", r.data["detail"])

    def test_create_bad_csv(self):
        self.auth()
        r = self.client.post(
            "/api/v1/fixed-routes/manage/",
            {"start_name": "A", "end_name": "B", "fixed_step": "x",
             "fixed_points": "X,Y\nabc,def\n120,31"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    # ---------------- 修改 ----------------

    def test_update_with_new_csv(self):
        self.auth()
        r = self.client.post(
            "/api/v1/fixed-routes/manage/",
            {"start_name": "扎下镇", "end_name": "江苏沭阳北收费站",
             "fixed_step": "old-step", "fixed_points": CSV_TEXT},
            format="json",
        )
        pk = r.data["id"]
        new_csv = "X,Y\n121.0,32.0\n121.1,32.1\n121.2,32.2\n"
        r = self.client.put(
            "/api/v1/fixed-routes/manage/%d/" % pk,
            {"start_name": "扎下镇", "end_name": "江苏沭阳北收费站",
             "fixed_step": "new-step", "fixed_points": new_csv},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["fixed_step"], "new-step")
        self.assertEqual(r.data["start_point"], [121.0, 32.0])
        self.assertEqual(r.data["end_point"], [121.2, 32.2])
        self.assertEqual(len(r.data["fixed_points"]["coordinates"]), 3)

    def test_update_without_coords_keeps_points(self):
        self.auth()
        r = self.client.post(
            "/api/v1/fixed-routes/manage/",
            {"start_name": "扎下镇", "end_name": "江苏沭阳北收费站",
             "fixed_step": "old", "fixed_points": CSV_TEXT},
            format="json",
        )
        pk = r.data["id"]
        r = self.client.put(
            "/api/v1/fixed-routes/manage/%d/" % pk,
            {"start_name": "扎下镇", "end_name": "江苏沭阳北收费站", "fixed_step": "only-step"},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["start_point"], FIRST)
        self.assertEqual(r.data["end_point"], LAST)
        self.assertEqual(len(r.data["fixed_points"]["coordinates"]), 6)

    def test_update_duplicate_names(self):
        self.auth()
        for i, names in enumerate([("A", "B"), ("C", "D")]):
            self.client.post(
                "/api/v1/fixed-routes/manage/",
                {"start_name": names[0], "end_name": names[1], "fixed_step": "s",
                 "fixed_points": CSV_TEXT},
                format="json",
            )
        pk = FixedRoute.objects.get(start_name="A", end_name="B").pk
        r = self.client.put(
            "/api/v1/fixed-routes/manage/%d/" % pk,
            {"start_name": "C", "end_name": "D", "fixed_step": "s"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    # ---------------- 删除与列表 ----------------

    def test_delete(self):
        self.auth()
        r = self.client.post(
            "/api/v1/fixed-routes/manage/",
            {"start_name": "待删", "end_name": "收费站", "fixed_step": "s", "fixed_points": CSV_TEXT},
            format="json",
        )
        pk = r.data["id"]
        r = self.client.delete("/api/v1/fixed-routes/manage/%d/" % pk)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(FixedRoute.objects.filter(pk=pk).exists())

    def test_list(self):
        self.auth()
        self.client.post(
            "/api/v1/fixed-routes/manage/",
            {"start_name": "扎下镇", "end_name": "江苏沭阳北收费站",
             "fixed_step": "s", "fixed_points": CSV_TEXT},
            format="json",
        )
        r = self.client.get("/api/v1/fixed-routes/manage/")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.data), 1)
        row = r.data[0]
        for key in ("id", "start_name", "end_name", "start_point", "end_point",
                    "fixed_step", "fixed_points", "created_at", "updated_at"):
            self.assertIn(key, row)
