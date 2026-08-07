"""实习美化（第一层，有则做）：仅优化措辞补量化，不创造事实。"""
from ..prompts import internship_messages
from .base import GenContext, as_list, llm_with_degrade, normalize_text_item


async def gen_internship(ctx: GenContext) -> dict:
    internships = ctx.resume.get("internship") or []
    if not internships:
        return {"items": [], "skipped": True}

    messages = internship_messages(internships, ctx.industry_rules)
    parsed = await llm_with_degrade(
        ctx.provider, messages, max_tokens=2048, temperature=0.4,
        degrade={"items": internships},  # 失败降级：保留用户原文
    )
    items = []
    src_by_company = {i.get("company", ""): i for i in internships}
    for it in as_list(parsed.get("items")):
        src = src_by_company.get(it.get("company", ""), {})
        duties = []
        for d in as_list(it.get("duties")):
            text = str(d.get("text", "")).strip()
            if text:
                duties.append({**normalize_text_item(d), "text": text[:300]})
        # 公司/职位/时间以用户原值为准，LLM 输出仅作措辞参考
        items.append({
            "company": src.get("company", it.get("company", "")),
            "position": src.get("position", it.get("position", "")),
            "startMonth": src.get("startMonth", it.get("startMonth", "")),
            "endMonth": src.get("endMonth", it.get("endMonth", "")),
            "duties": duties or [normalize_text_item({"text": "（待补充）"})],
        })
    return {"items": items, "degraded": bool(parsed.get("degraded"))}
