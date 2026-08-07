"""技能拓展（第二层，skillExtend=true 时）：基于 JD 推荐补充技能。"""
from ..skills import extend_skills
from .base import GenContext


async def gen_skill_extend(ctx: GenContext) -> dict:
    if not ctx.skill_extend_enabled:
        return {"skills": [], "skipped": True}
    recommended = await extend_skills(ctx.provider, ctx.resume.get("skill") or [], ctx.jobs)
    existing = {s.get("name", "") for s in (ctx.resume.get("skill") or [])}
    fresh = [r for r in recommended if r.get("name") not in existing]
    return {"skills": fresh, "degraded": False}
