"""生成提交关卡与任务接口（契约 §4.2/§4.3/§4.4）。

POST /api/generate —— 提交关卡（通过才创建任务）：
  1. 简历存在（40008）+ 基础校验（40001/40007/40011）
  2. JD 分析 → 共享事实表 + 领域标签（写回简历，供 P4 复用）
  3. 技能相关性三档：block → 40002
  4. 主题一致性：跨领域 → 40003
  5. 数量约束（§3.5）：contentPlan.projectCount 按页数/实习条数定死
GET /api/task/{id} / POST /api/task/{id}/cancel / GET /api/task/{id}/events（基础 SSE，P4 扩展实时进度）
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from ..core.errors import AppError, E_PARAM, E_SKILL_BLOCK, E_TASK_STATE
from ..core.providers import LLMProvider
from ..core.validation import check_resume, project_count_for
from ..engine.analysis import JDAnalyzer
from ..engine.skills import validate_skills
from ..schemas import (
    CamelModel,
    ContentPlan,
    GenerationInfo,
    Job,
    PageOption,
    Resume,
    Task,
    TaskState,
)

router = APIRouter(prefix="/api", tags=["generate"])


class GenerateBody(CamelModel):
    resume_id: str = Field(min_length=1)
    page_option: PageOption = PageOption.one_page
    watermark_mode: str = Field(default="practice", pattern="^(practice|formal)$")
    deep_search: bool = False


@router.post("/generate", response_model=dict)
async def generate(body: GenerateBody, request: Request):
    """提交关卡 → 创建 pending 任务（单任务串行；P4 调度执行 analyzing→done）。"""
    app = request.app
    storage = app.state.storage
    cfg = app.state.config
    now = app.state.now

    data = storage.load_resume(body.resume_id)          # 40008
    resume = Resume(**data)
    check_resume(resume, cfg.limits)

    jobs: list[Job] = resume.jobs
    if not jobs:
        raise AppError(E_PARAM, "请至少填写 1 套目标岗位 JD")

    provider = LLMProvider(cfg)
    analyzer = JDAnalyzer(provider, app.state.rules)

    # 1) JD 分析 → 共享事实表（领域标签写回各 Job）
    factsheet = await analyzer.analyze(jobs, resume, body.page_option.value, body.deep_search)

    # 2) 技能相关性三档（≥0.6 pass / 0.3~0.6 weak / <0.3 block + 关键词兜底）
    sr = await validate_skills(
        provider,
        [s.model_dump() for s in resume.skill],
        [j.model_dump(by_alias=True) for j in jobs],
        app.state.rules.skills_rules(),
    )
    if sr["verdict"] == "block":
        raise AppError(E_SKILL_BLOCK, "技能与目标岗位相关性不足，请补充相关技能后重试", sr)

    # 3) 主题一致性（领域标签共享 ≥1 或语义 ≥0.4）
    await analyzer.check_theme(resume, jobs)

    # 4) 数量约束（§3.5）：contentPlan.projectCount 定死，压缩/扩充不增减条数
    project_count = project_count_for(body.page_option.value, len(resume.internship))

    # 创建任务（pending，串行环境无队列）
    task = Task(
        id=storage.new_task_id(),
        resume_id=body.resume_id,
        state=TaskState.pending,
        stage="",
        created_at=now(),
        updated_at=now(),
    )
    storage.save_task(task.model_dump(mode="json", by_alias=True))

    # 分析结果与生成上下文写回简历（P4 复用，避免重复分析）
    resume.direction = factsheet.direction
    resume.content_plan = ContentPlan(project_count=project_count)
    resume.generation = GenerationInfo(
        task_id=task.id,
        stages=["analyzing", "generating", "building"],
        watermark_mode=body.watermark_mode,
        deep_search=body.deep_search,
    )
    data["direction"] = factsheet.direction
    data["jobs"] = [j.model_dump(mode="json", by_alias=True) for j in jobs]
    data["contentPlan"] = resume.content_plan.model_dump(mode="json", by_alias=True)
    data["generation"] = resume.generation.model_dump(mode="json", by_alias=True)
    data["updated_at"] = now()
    storage.save_resume(data)

    return {"code": 0, "message": "ok", "data": {"taskId": task.id}}


@router.get("/task/{task_id}", response_model=dict)
def get_task(task_id: str, request: Request):
    """任务快照：{state, progress, stage, stageIndex, stageTotal}。"""
    data = request.app.state.storage.load_task(task_id)  # 40008
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "state": data.get("state"),
            "progress": data.get("progress", 0.0),
            "stage": data.get("stage", ""),
            "stageIndex": data.get("stage_index", 0),
            "stageTotal": data.get("stage_total", 1),
            "error": data.get("error"),
            "updatedAt": data.get("updated_at"),
        },
    }


@router.post("/task/{task_id}/cancel", response_model=dict)
def cancel_task(task_id: str, request: Request):
    """取消任务：仅非终态可取消（pending/analyzing/generating/building）。"""
    storage = request.app.state.storage
    task = storage.load_task(task_id)  # 40008
    if task["state"] in ("done", "failed", "canceled"):
        raise AppError(E_TASK_STATE, f"任务已处于 {task['state']} 状态，无法取消",
                       {"state": task["state"]})
    task["state"] = TaskState.canceled
    task["updated_at"] = request.app.state.now()
    storage.save_task(task)
    return {"code": 0, "message": "ok", "data": {"canceled": True}}


@router.get("/task/{task_id}/events")
async def task_events(task_id: str, request: Request):
    """SSE 事件流（§4.4）。P3 提供当前状态快照；P4 接入实时板块进度。"""
    data = request.app.state.storage.load_task(task_id)  # 40008

    def _event(name: str, payload: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    state = data.get("state")
    base = {"taskId": task_id}
    if state == "done":
        event = _event("task.done", {**base, "resumeId": data.get("resume_id")})
    elif state == "failed":
        event = _event("task.failed", {**base, "error": data.get("error")})
    elif state == "canceled":
        event = _event("task.canceled", base)
    else:
        event = _event("task.created", {**base, "state": state})

    async def gen():
        yield event

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
