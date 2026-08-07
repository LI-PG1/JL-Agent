"""Prompt 组装（契约 §6.10 八层结构）。

第 1 层 系统人设 / 第 2 层 简历数据 / 第 3 层 JD / 第 4 层 共享事实表 /
第 5 层 规则与风格 / 第 6 层 输出格式（含自估协议）/ 第 7 层 预算约束 / 第 8 层 合规边界。

P3 落地：JD 分析、技能相关性评分。P4 扩展各生成板块。
"""
import json
from typing import List

SYSTEM_PERSONA = (
    "你是一名资深 HR 与求职导师，精通中文简历撰写与 ATS（申请追踪系统）解析规则。"
    "你坚持「真实优先」：绝不虚构经历、公司、职级、奖项与业务数据；"
    "需要数值时给出合理、符合行业常规精度（如百分比 1 位小数、延迟 100~200ms、QPS 数百）的数字，"
    "并始终以「可被面试追问验证」为标准措辞。"
)


def jd_analysis_messages(jobs: List[dict], rules: dict, factsheet_input: dict) -> List[dict]:
    """JD 分析 → 共享事实表（§5.2）。factsheet_input 提供 identity/pageOption/density 上下文。"""
    system = SYSTEM_PERSONA + (
        "\n\n你是 JD 分析器：从 1~5 套岗位 JD 中提炼职业方向与简历生成所需的关键事实，"
        "输出严格 JSON，不要输出任何解释或 markdown。"
    )
    user = f"""请分析以下目标岗位 JD（{len(jobs)} 套，属同一职业方向），输出共享事实表 JSON：

【JSON 输出结构】
{{
  "direction": "职业方向（如 AI Agent / LLM 应用）",
  "coreSkills": ["岗位最看重的 3~5 个技能/领域，用于定向优化简历"],
  "jdFocus": "JD 的核心诉求（1 句话）",
  "projectType": "最匹配的项目类型（参考可用类型）",
  "metricStyle": "该岗位成果量化的风格约定（参考给定风格，可改写为贴合 JD）",
  "domainTags": ["领域标签 2~4 个，用于主题一致性校验"],
  "keywordCoverage": 0.0
}}

【岗位 JD】
{"\n---\n".join(f"岗位：{j['title']}\nJD：{j['jdText']}" for j in jobs)}

【行业规则参考】
可用项目类型：{rules.get('project_types', [])}
量化风格参考：{rules.get('metric_style', '')}
主题一致性方法：{rules.get('jobs', {}).get('method', 'shared-domain-tag')}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def skill_validate_messages(skills: List[dict], jobs: List[dict], rules: dict) -> List[dict]:
    """技能相关性评分（§3.1.4 / §4.2 /api/skills/validate）。"""
    system = SYSTEM_PERSONA + (
        "\n\n你是技能匹配评估器：评估「用户技能列表」与「目标岗位 JD」的相关度，"
        "输出严格 JSON：{\"score\": 0~1, \"reason\": \"中文理由\"}。"
        "分数含义：≥0.6 强相关；0.3~0.6 部分相关；<0.3 明显不相关。"
    )
    user = f"""【用户技能】{", ".join(f"{s.get('name','')}" for s in skills)}

【目标岗位 JD】
{"\n---\n".join(f"岗位：{j.get('title','')}\nJD：{j.get('jdText','')}" for j in jobs)}

【评分要求】结合 JD 核心关键词评估相关度，仅输出 JSON。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def theme_check_messages(jd_tags: List[str], resume_tags: List[str], threshold: float) -> List[dict]:
    """主题一致性语义兜底（§3.1.6）：领域标签共享 <1 时评估语义相关度。"""
    system = SYSTEM_PERSONA + (
        "\n\n你是主题一致性评估器：判断「目标岗位领域标签」与「求职者经历领域标签」是否属于同一方向，"
        "输出严格 JSON：{\"score\": 0~1, \"reason\": \"中文理由\"}。"
        f"score ≥{threshold} 视为同一方向。"
    )
    user = f"""【岗位领域标签】{", ".join(jd_tags) if jd_tags else "（无）"}
【简历领域标签】{", ".join(resume_tags) if resume_tags else "（无）"}

仅输出 JSON。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def skill_extend_messages(skills: List[dict], jobs: List[dict]) -> List[dict]:
    """技能拓展（§4.2 /api/skills/extend）：基于 JD 推荐补充技能。"""
    system = SYSTEM_PERSONA + (
        "\n\n你是技能规划导师：基于目标岗位 JD 与用户现有技能，推荐 3~6 个补充技能，"
        "输出严格 JSON：{\"recommended\": [{\"category\": \"专业技能|工具与框架|语言能力\", \"name\": \"技能名\", \"level\": \"精通|熟练|熟悉|了解\"}]}。"
        "仅推荐与 JD 强相关、用户真实可具备（学习/练习可掌握）的技能，不虚构资质。"
    )
    user = f"""【用户现有技能】{", ".join(f"{s.get('name','')}" for s in skills)}

【目标岗位 JD】
{"\n---\n".join(f"岗位：{j.get('title','')}\nJD：{j.get('jdText','')}" for j in jobs)}

仅输出 JSON。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------- P4 分块生成（§5.1/§5.3）


def _estimated_protocol() -> str:
    """自估协议（§5.3）：每个文本条目输出预估渲染行数。"""
    return (
        "【自估协议】每条正文（text/sentences/duties/items）必须附带 estimatedLines（整数 1~8），"
        "表示该条在 A4 单栏、正文 10.5pt、默认行距下预估渲染的行数，供排版预算校准。"
    )


def summary_messages(user_brief: dict, rules: dict, factsheet: dict) -> List[dict]:
    """自我评价生成（第一层，无 JD 依赖）：1~3 句，突出可迁移优势。"""
    system = SYSTEM_PERSONA + (
        "\n\n你是简历自我评价撰写师：基于用户教育/实习/技能/项目概要，写 1~3 句简洁有力的自我评价，"
        "突出真实存在的优势与学习能力，不虚构奖项与经历，输出严格 JSON。"
    )
    user = f"""{_estimated_protocol()}

【用户概要】
{json.dumps(user_brief, ensure_ascii=False, indent=2)}

【风格参考】{rules.get('tone', '')}

【JSON 输出结构】
{{"sentences": [{{"text": "自我评价句子（40~90 字）", "estimatedLines": 1}}]}}

仅输出 JSON。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def internship_messages(internships: List[dict], rules: dict) -> List[dict]:
    """实习美化（第一层，有则做）：仅优化措辞、补合理量化，不创造经历。"""
    system = SYSTEM_PERSONA + (
        "\n\n你是实习经历润色师：在**不改变公司/职位/时间/职责事实**的前提下优化职责描述措辞，"
        "可为可量化成果补合理数值（符合行业精度，见风格），每条职责 1 句话，输出严格 JSON。"
    )
    user = f"""{_estimated_protocol()}

【用户实习】
{json.dumps(internships, ensure_ascii=False, indent=2)}

【量化风格】{rules.get('metric_style', '')}

【JSON 输出结构】
{{"items": [{{"company": "公司", "position": "职位", "startMonth": "2024.06", "endMonth": "2024.09",
  "duties": [{{"text": "职责描述", "estimatedLines": 1}}]}}]}}

仅输出 JSON；保留全部公司/职位/时间原值。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def projects_messages(
    seeds: List[dict],
    skeleton: str,
    factsheet: dict,
    rules: dict,
    count: int,
    search_results: List[dict],
) -> List[dict]:
    """项目生成（第二层，依赖共享事实表）：已有项目润色补齐，空位按骨架创造。"""
    system = SYSTEM_PERSONA + (
        "\n\n你是项目经历撰写师：面向目标岗位定向产出 {count} 条 STAR 结构项目。"
        "用户已有项目 → 真实润色（source=polished）；空位 → 基于给定骨架创作可验证的课程/竞赛/自研项目（source=ai-created），"
        "不虚构公司职级。每条 2~4 行（一页）或 3~6 行（两页）。输出严格 JSON。"
    ).format(count=count)
    user = f"""{_estimated_protocol()}

【共享事实表】{json.dumps(factsheet, ensure_ascii=False, indent=2)}

【用户已有项目（可作种子）】
{json.dumps(seeds, ensure_ascii=False, indent=2) if seeds else "（无）"}

【空位骨架】{skeleton or "（无，按事实表 projectType 创作）"}

【量化风格】{rules.get('metric_style', '')}

【联网参考（标注待核实，不得照抄）】
{json.dumps(search_results, ensure_ascii=False, indent=2) if search_results else "（无）"}

【评估标准】{json.dumps(rules.get('evaluation', []), ensure_ascii=False, indent=2)}

【JSON 输出结构】
{{"projects": [{{"name": "项目名", "role": "角色", "startMonth": "2024.07", "endMonth": "2024.09",
  "techStack": ["技术1", "技术2"], "source": "polished|ai-created",
  "items": [{{"text": "STAR 要点（含量化）", "estimatedLines": 1}}]}}]}}

仅输出 JSON。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
