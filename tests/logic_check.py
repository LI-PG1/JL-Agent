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

from app.core.errors import AppError, E_LLM, E_THEME_BLOCK
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

# ================================================================ P4 生成引擎 DAG（§5.1/§5.4/§5.6）
import shutil
import tempfile
from datetime import datetime

from app.config import Config, Paths
from app.engine.budget import BudgetTracker
from app.engine.cache import GenCache
from app.engine.dag import GenerationRunner
from app.storage import Storage
from app.schemas import Task

JD_RESULT = {
    "direction": "AI Agent / LLM 应用",
    "coreSkills": ["大模型推理部署", "RAG"],
    "jdFocus": "智能体系统与 RAG 落地",
    "projectType": "智能体系统",
    "metricStyle": "延迟降至 100~200ms",
    "domainTags": ["大模型", "Agent"],
}


class DispatchProvider:
    """按 prompt 标记分发结果（模拟 JD 分析/自我评价/实习/项目 4 类 LLM 调用）。"""

    def __init__(self, project_result=None, fail_projects=False):
        self.jd_calls = 0
        self.fail_projects = fail_projects
        self.project_result = project_result

    async def chat(self, messages, **kw):
        text = json.dumps(messages, ensure_ascii=False)
        if "JD 分析器" in text:
            self.jd_calls += 1
            return json.dumps(JD_RESULT, ensure_ascii=False)
        if "自我评价撰写师" in text:
            return json.dumps({"sentences": [
                {"text": "扎实的工程能力与持续学习意愿。", "estimatedLines": 1},
                {"text": "熟悉大模型推理与 RAG 落地。", "estimatedLines": 2},
            ]})
        if "实习经历润色师" in text:
            return json.dumps({"items": [{
                "company": "某科技公司", "position": "算法实习生",
                "startMonth": "2024.06", "endMonth": "2024.09",
                "duties": [{"text": "优化推理服务延迟，吞吐提升 30%。", "estimatedLines": 2}],
            }]})
        if "项目经历撰写师" in text:
            if self.fail_projects:
                raise AppError(E_LLM, "模拟项目块 LLM 失败")
            return json.dumps(self.project_result or {"projects": [{
                "name": "Agent 调度平台", "role": "核心开发",
                "startMonth": "2025.01", "endMonth": "2025.06",
                "techStack": ["FastAPI", "vLLM"], "source": "polished",
                "items": [{"text": "设计分层并行 DAG 调度与实时进度上报。", "estimatedLines": 2},
                          {"text": "端到端吞吐提升 2 倍。", "estimatedLines": 1}],
            }]})
        raise AssertionError(f"未知 prompt 标记: {text[:80]}")


class DummySearch:
    ready = False


def make_runner(tmp: str, provider):
    cfg = Config(paths=Paths(data_dir=tmp, rules_dir=str(ROOT / "rules"),
                             templates_dir=str(ROOT / "templates")))
    storage = Storage(tmp)
    cache = GenCache(tmp)
    budget = BudgetTracker(tmp)
    runner = GenerationRunner(
        storage=storage, rules=loader, config=cfg, provider=provider,
        analyzer=JDAnalyzer(provider, loader), search_client=DummySearch(),
        cache=cache, budget=budget,
        now=lambda: datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    return storage, cache, runner


def seed_resume(storage: Storage, *, internship=True, honor=True) -> str:
    rid = storage.new_resume_id()
    data = {
        "id": rid, "identity": "intern", "pageOption": "one-page", "density": "normal",
        "basicInfo": {"name": "张三", "age": 24, "email": "a@b.com", "phone": "13800138000"},
        "education": [{"school": "安徽大学", "major": "应用统计", "degree": "学士",
                       "startMonth": "2020.09", "endMonth": "2024.06"}],
        "skill": [{"category": "专业技能", "name": "Python", "skillExtend": False},
                  {"category": "工具与框架", "name": "Docker", "skillExtend": False}],
        "internship": [{"company": "某科技公司", "position": "算法实习生",
                        "startMonth": "2024.06", "endMonth": "2024.09",
                        "duties": [{"text": "负责推理服务开发。"}]}] if internship else [],
        "project": [{"name": "RAG 知识库", "role": "开发", "startMonth": "2024.07", "endMonth": "2024.09",
                     "techStack": ["FastAPI", "Milvus"],
                     "items": [{"text": "构建检索增强问答系统。"}]}],
        "honor": [{"name": "国家奖学金", "time": "2023"}] if honor else [],
        "jobs": [{"title": "大模型应用开发实习生",
                  "jdText": "负责 LLM Agent 与 RAG 系统开发，熟悉 Python、PyTorch、Docker"}],
        "contentPlan": {"projectCount": project_count_for("one-page", 1)},
        "generation": {"deepSearch": False},
        "createdAt": "2026-01-01T00:00:00", "updatedAt": "2026-01-01T00:00:00",
    }
    storage.save_resume(data)
    return rid


def make_task(storage: Storage, rid: str) -> str:
    task = Task(id=storage.new_task_id(), resume_id=rid,
                state="pending", created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
    storage.save_task(task.model_dump(mode="json", by_alias=True))
    return task.id


async def t3():
    tmp = tempfile.mkdtemp(prefix="jl_agent_")
    try:
        # A) 全链路：JD 缓存写入 → 分层并行生成 → 装配回写 → done
        provider = DispatchProvider()
        storage, cache, runner = make_runner(tmp, provider)
        rid = seed_resume(storage)
        tid = make_task(storage, rid)
        await runner.run(tid)

        task = storage.load_task(tid)
        ok("任务终态 done", task["state"] == "done", str(task.get("error")))
        ok("进度收敛 1.0", abs(task["progress"] - 1.0) < 1e-6, str(task["progress"]))

        names = [e["event"] for e in task["events"]]
        ok("阶段事件 ×3", names.count("task.stage") == 3, str(names))
        for blk in ("analysis", "summary", "education", "internship", "skills", "honor", "projects", "build"):
            ok(f"block.done[{blk}] 已发", any(e["event"] == "block.done" and e["data"].get("block") == blk for e in task["events"]))
        ok("终态事件 task.done", "task.done" in names, str(names))

        r = storage.load_resume(rid)
        ok("自我评价 2 句写回", len(r["summary"]) == 2)
        ok("实习润色写回（公司原值）", r["internship"][0]["company"] == "某科技公司" and "延迟" in r["internship"][0]["duties"][0]["text"])
        ok("项目条数 = 硬性约束 1", len(r["project"]) == 1)
        ok("项目来源 polished", r["project"][0]["source"] == "polished")
        ok("荣誉保留", r["honor"][0]["name"] == "国家奖学金")
        ok("装配元数据（一页 4 要点）", r["contentPlan"]["bulletCountPerProject"] == 4)

        jd_key = GenCache.jd_key(
            [j for j in r["jobs"]], "one-page", "intern",
            str(loader.jobs_rules().get("version", "1.0")))
        ok("JD 事实表已写缓存", cache.get(jd_key) is not None)

        # B) JD 缓存命中：同简历二次生成 → 不再调 JD 分析
        provider2 = DispatchProvider()
        storage2, cache2, runner2 = make_runner(tmp, provider2)
        tid2 = make_task(storage2, rid)
        await runner2.run(tid2)
        ok("二次生成 JD 分析缓存命中（jd_calls=0）", provider2.jd_calls == 0, f"jd_calls={provider2.jd_calls}")
        ok("二次任务终态 done", storage2.load_task(tid2)["state"] == "done")

        # C) 模块级失败隔离：项目块 LLM 失败 → 降级 + 种子兜底，整单继续
        provider3 = DispatchProvider(fail_projects=True)
        storage3, _, runner3 = make_runner(tmp, provider3)
        rid3 = seed_resume(storage3)
        tid3 = make_task(storage3, rid3)
        await runner3.run(tid3)
        ok("项目块失败 → 任务仍 done", storage3.load_task(tid3)["state"] == "done")
        ev = [e for e in storage3.load_task(tid3)["events"]
              if e["event"] == "block.done" and e["data"].get("block") == "projects"]
        ok("项目块事件标记降级", bool(ev) and ev[0]["data"].get("degraded") is True, str(ev))
        r3 = storage3.load_resume(rid3)
        ok("降级兜底：种子补足 1 条（user-input）", len(r3["project"]) == 1 and r3["project"][0]["source"] == "user-input", str(r3["project"]))

        # D) 荣誉为空 → 整块跳过，不覆盖简历
        provider4 = DispatchProvider()
        storage4, _, runner4 = make_runner(tmp, provider4)
        rid4 = seed_resume(storage4, honor=False)
        tid4 = make_task(storage4, rid4)
        await runner4.run(tid4)
        ev4 = [e for e in storage4.load_task(tid4)["events"]
               if e["event"] == "block.done" and e["data"].get("block") == "honor"]
        ok("荣誉空 → block.done skipped", bool(ev4) and ev4[0]["data"].get("skipped") is True, str(ev4))
        ok("荣誉空 → 简历未写空覆盖", storage4.load_resume(rid4).get("honor") in (None, []))

        # E) 取消：analyzing 阶段取消 → 不再产出 done
        provider5 = DispatchProvider()
        storage5, _, runner5 = make_runner(tmp, provider5)
        rid5 = seed_resume(storage5)
        tid5 = make_task(storage5, rid5)
        t = storage5.load_task(tid5)
        t["state"] = "canceled"
        t.setdefault("events", []).append({"event": "task.canceled", "data": {"taskId": tid5}})
        storage5.save_task(t)
        await runner5.run(tid5)
        ok("已取消 → 不覆盖终态", storage5.load_task(tid5)["state"] == "canceled")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


asyncio.run(t3())
print(f"\n逻辑验证: {passed} 通过")
