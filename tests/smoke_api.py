"""API 冒烟/回归测试（P2 起）：CRUD + 校验错误 + 照片上传。

运行：先启动服务（uvicorn app.main:app），再执行
    .venv\\Scripts\\python.exe tests\\smoke_api.py
"""
import io
import json
import urllib.request

import httpx
from PIL import Image

BASE = "http://127.0.0.1:8000"
ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {detail}")


def post(path, payload):
    r = httpx.post(BASE + path, json=payload, timeout=10)
    return r.status_code, r.json()


def build_resume(**over):
    base = {
        "basicInfo": {"name": "张三", "age": 24, "email": "zhangsan@example.com", "phone": "13800138000"},
        "education": [
            {"school": "安徽大学", "major": "应用统计", "degree": "学士",
             "start_month": "2020.09", "end_month": "2024.06"}
        ],
        "skill": [
            {"category": "专业技能", "name": "Python", "level": "熟练"},
            {"category": "工具与框架", "name": "PyTorch"},
        ],
        "project": [
            {"name": "RAG 检索系统", "role": "开发", "start_month": "2024.07", "end_month": "2024.09",
             "tech_stack": ["FastAPI", "Chroma"], "items": [{"text": "构建检索增强生成系统"}]}
        ],
    }
    base.update(over)
    return base


print("== 1. 健康检查 ==")
r = httpx.get(BASE + "/api/health", timeout=10)
check("GET /api/health", r.status_code == 200 and r.json()["code"] == 0)

print("== 2. 创建简历 ==")
sc, j = post("/api/resume", build_resume())
rid = j.get("data", {}).get("resumeId") if sc == 200 else None
check("POST /api/resume 创建成功", sc == 200 and bool(rid), str(j))
if not rid:
    raise SystemExit("无法继续：创建失败")

print("== 3. 读取简历 ==")
r = httpx.get(f"{BASE}/api/resume/{rid}", timeout=10)
j = r.json()
check("GET /api/resume/{id}", j["code"] == 0 and j["data"]["basicInfo"]["name"] == "张三")
check("教育/技能/项目条数回读", len(j["data"]["education"]) == 1 and len(j["data"]["skill"]) == 2)

print("== 4. 校验错误（教育时间 end<=start → 40007） ==")
bad = build_resume(education=[{"school": "A", "major": "B", "degree": "学士",
                               "start_month": "2024.06", "end_month": "2023.09"}])
sc, j = post("/api/resume", bad)
check("40007 教育时间非法", sc == 400 and j["code"] == 40007, str(j))

print("== 5. 校验错误（教育 4 条 → 40011） ==")
four = [{"school": f"S{i}", "major": "M", "degree": "学士", "start_month": "2020.09", "end_month": "2024.06"} for i in range(4)]
sc, j = post("/api/resume", build_resume(education=four))
check("40011 教育数量超限", sc == 400 and j["code"] == 40011, str(j))

print("== 6. 校验错误（技能为空 → 40001） ==")
sc, j = post("/api/resume", build_resume(skill=[]))
check("40001 技能必填", sc == 400 and j["code"] == 40001, str(j))

print("== 7. 校验错误（邮箱非法 → 40001） ==")
sc, j = post("/api/resume", build_resume(basicInfo={"name": "张三", "age": 24, "email": "bad-email",
                                                    "phone": "13800138000"}))
check("40001 邮箱格式", sc == 400 and j["code"] == 40001, str(j))

print("== 8. 校验错误（实习 end<=start → 40007） ==")
sc, j = post("/api/resume", build_resume(
    internship=[{"company": "C", "position": "P", "start_month": "2024.05", "end_month": "2024.01"}]))
check("40007 实习时间非法", sc == 400 and j["code"] == 40007, str(j))

print("== 9. 更新简历 ==")
upd = build_resume()
upd["skill"] = [{"category": "专业技能", "name": "Python"}, {"category": "工具与框架", "name": "FastAPI"},
                {"category": "语言能力", "name": "英语", "level": "熟悉"}]
r = httpx.put(f"{BASE}/api/resume/{rid}", json=upd, timeout=10)
check("PUT /api/resume/{id}", r.status_code == 200 and r.json()["code"] == 0, str(r.json()))
r = httpx.get(f"{BASE}/api/resume/{rid}", timeout=10)
check("更新后技能 3 条", len(r.json()["data"]["skill"]) == 3)

print("== 10. 照片上传（有效 PNG 600x800） ==")
buf = io.BytesIO()
Image.new("RGB", (600, 800), (200, 200, 220)).save(buf, format="PNG")
png_bytes = buf.getvalue()
r = httpx.post(f"{BASE}/api/upload/photo", data={"resume_id": rid},
               files={"file": ("photo.png", png_bytes, "image/png")}, timeout=10)
j = r.json()
check("上传成功并返回元数据", r.status_code == 200 and j["code"] == 0 and j["data"]["ratio"] == "3:4",
      str(j))
r = httpx.get(f"{BASE}/api/resume/{rid}", timeout=10)
photo = r.json()["data"].get("photo") or {}
check("简历 photo 字段已回写", photo.get("filePath") and photo.get("format") == "png", str(photo))

print("== 11. 照片上传（非法格式 txt → 40004） ==")
r = httpx.post(f"{BASE}/api/upload/photo", data={"resume_id": rid},
               files={"file": ("a.txt", b"not an image", "text/plain")}, timeout=10)
check("40004 格式不支持", r.status_code == 400 and r.json()["code"] == 40004, str(r.json()))

print("== 12. 照片上传（超大 → 40006） ==")
big = b"\x00" * (5 * 1024 * 1024 + 10)
r = httpx.post(f"{BASE}/api/upload/photo", data={"resume_id": rid},
               files={"file": ("big.png", big, "image/png")}, timeout=10)
check("40006 大小超限", r.status_code == 400 and r.json()["code"] == 40006, str(r.json()))

print("== 13. 列表 + 删除 ==")
r = httpx.get(f"{BASE}/api/resume", timeout=10)
check("列表包含新建简历", r.status_code == 200 and any(i["id"] == rid for i in r.json()["data"]["items"]))
r = httpx.delete(f"{BASE}/api/resume/{rid}", timeout=10)
check("DELETE 成功", r.status_code == 200 and r.json()["data"]["deleted"] is True)
r = httpx.get(f"{BASE}/api/resume/{rid}", timeout=10)
check("删除后 404（40008）", r.status_code == 400 and r.json()["code"] == 40008, str(r.json()))

print(f"\n结果: {ok} 通过, {fail} 失败")
raise SystemExit(1 if fail else 0)
