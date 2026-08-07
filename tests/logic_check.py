"""P3 核心逻辑回归验证（无 LLM 依赖，FakeProvider 模拟）：判定边界/容错/纯函数。

覆盖冒烟无法触达的分支：技能三档边界、关键词兜底、JD 分析→事实表、领域标签写回、
主题一致性（共享标签直通 / 语义兜底通过 / 40003 拦截）。

运行：.venv\\Scripts\\python.exe tests\\logic_check.py
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.errors import AppError, E_THEME_BLOCK
from app.core.rules import RulesLoader
from app.core.validation import project_count_for
from app.engine.analysis import JDAnalyzer, extract_json
from app.engine.skills import validate_skills
from app.schemas import Job, Resume

passed = 0


def ok(name, cond, detail=""):
    global passed
    assert cond, f"{name}: {detail}"
    passed += 1
    print(f"  [PASS] {name}")


class FakeProvider:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        return json.dumps(self.result, ensure_ascii=False)


loader = RulesLoader(str(ROOT / "rules"))
loader.load_all()

# 1) 数量硬性约束表（§3.5）
ok("一页/0实习 → 2 条", project_count_for("one-page", 0) == 2)
ok("一页/1实习 → 1 条", project_count_for("one-page", 1) == 1)
ok("一页/2实习 → 1 条", project_count_for("one-page", 2) == 1)
ok("两页/0实习 → 3 条", project_count_for("two-pages", 0) == 3)
ok("两页/1实习 → 2 条", project_count_for("two-pages", 1) == 2)
ok("两页/2实习 → 1 条", project_count_for("two-pages", 2) == 1)

# 2) JSON 容错提取
ok("围栏 JSON", extract_json('```json\n{"a": 1}\n```') == {"a": 1})
ok("前缀文本 JSON", extract_json('结果如下：{"a": 1} 完毕') == {"a": 1})

# 3) 技能三档边界 + 关键词兜底
jobs = [{"title": "大模型实习生", "jdText": "Python PyTorch Docker 大模型"}]
skills = [{"category": "专业技能", "name": "Python"}]
sr_rules = loader.skills_rules()


async def t1():
    p = FakeProvider({"score": 0.8, "reason": "强相关"})
    r = await validate_skills(p, skills, jobs, sr_rules)
    ok("LLM 0.8 → pass", r["verdict"] == "pass" and r["score"] == 0.8)

    p = FakeProvider({"score": 0.5, "reason": "部分相关"})
    r = await validate_skills(p, [{"category": "专业技能", "name": "Java"}], jobs, sr_rules)
    ok("LLM 0.5 无命中 → weak", r["verdict"] == "weak")

    p = FakeProvider({"score": 0.1, "reason": "不相关"})
    r = await validate_skills(p, [{"category": "专业技能", "name": "Java"}],
                              [{"title": "A", "jdText": "Python"}], sr_rules)
    ok("LLM 0.1 无命中 → block", r["verdict"] == "block")

    # 关键词兜底：技能名直接命中 JD，LLM 低分仍 pass
    p = FakeProvider({"score": 0.1, "reason": "评估"})
    r = await validate_skills(p, skills, jobs, sr_rules)
    ok("关键词兜底 → pass", r["verdict"] == "pass", str(r))


asyncio.run(t1())

# 4) JD 分析：行业匹配 + 关键词覆盖率 + 领域标签写回（FakeProvider 模拟 LLM）
resume = Resume(
    basicInfo={"name": "张三", "age": 24, "email": "a@b.com", "phone": "13800138000"},
    skill=[{"category": "专业技能", "name": "PyTorch"}, {"category": "工具与框架", "name": "Docker"}],
    project=[{"name": "RAG 知识库", "tech_stack": ["Milvus"]}],
)
jd = Job(title="大模型应用开发实习生",
         jdText="负责 LLM Agent 与 RAG 系统开发，熟悉 Python、PyTorch、Docker、vLLM")
p = FakeProvider({
    "direction": "AI Agent / LLM 应用",
    "coreSkills": ["大模型推理部署", "RAG 检索增强", "PyTorch"],
    "jdFocus": "智能体系统与 RAG 落地",
    "projectType": "智能体系统",
    "metricStyle": "延迟降至 100~200ms",
    "domainTags": ["大模型", "Agent", "RAG"],
    "keywordCoverage": 0.0,
})
analyzer = JDAnalyzer(p, loader)


async def t2():
    fs = await analyzer.analyze([jd], resume, "one-page")
    ok("方向解析", fs.direction == "AI Agent / LLM 应用")
    ok("数量映射写入", fs.quantity["projectCount"] == 2 and fs.quantity["internshipCount"] == 0)
    ok("领域标签写回 JD", jd.domain_tags == ["大模型", "Agent", "RAG"])
    ok("覆盖率计算（PyTorch 命中 1/3 → 0.33）", abs(fs.keyword_coverage - 0.33) < 1e-6, str(fs.keyword_coverage))

    # 主题一致性：共享标签 ≥1 → 直接通过（不调 LLM）
    await analyzer.check_theme(resume, [jd])
    ok("共享标签命中不调 LLM", p.calls == 1, f"calls={p.calls}")

    # 语义兜底：无共享标签但简历有领域标签 → 调 LLM，score ≥0.4 通过
    # 注意 mock 需同时充当 JD 分析（返回 domainTags）与主题评分（返回 score）
    _jd_result = {"direction": "后端开发", "domainTags": ["后端", "Java"],
                  "coreSkills": ["Java"], "jdFocus": "", "projectType": "", "metricStyle": ""}
    p2 = FakeProvider({**_jd_result, "score": 0.6, "reason": "同方向"})
    a2 = JDAnalyzer(p2, loader)
    resume2 = Resume(
        basicInfo={"name": "李四", "age": 25, "email": "b@c.com", "phone": "13800138001"},
        skill=[{"category": "专业技能", "name": "PyTorch"}],
        project=[{"name": "图像分类服务", "tech_stack": ["Docker"]}],
    )
    jd2 = Job(title="Java 后端开发", jdText="Java Spring 高并发")
    await a2.analyze([jd2], resume2, "one-page")
    await a2.check_theme(resume2, [jd2])
    ok("语义兜底通过（calls=2）", p2.calls == 2, f"calls={p2.calls}")

    # 语义兜底拒绝：score <0.4 → 40003 拦截
    p3 = FakeProvider({**_jd_result, "score": 0.2, "reason": "方向不同"})
    a3 = JDAnalyzer(p3, loader)
    try:
        await a3.check_theme(resume2, [jd2])
        ok("语义兜底拒绝 → 40003", False, "未拦截")
    except AppError as exc:
        ok("语义兜底拒绝 → 40003", exc.code == E_THEME_BLOCK, str(exc))


asyncio.run(t2())
print(f"\n逻辑验证: {passed} 通过")
