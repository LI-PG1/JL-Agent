"""模板装配（契约 §6 building 步骤 1 / §5.5 编辑锁定展示 / §5.3 预算基线）。

P5 落地：
- 依据 pageOption 选择模板（resume-1page/2pages.html），保留 head（含 ATS 样式），重建 body：
  占位符替换、空区块删除（实习/荣誉未填充整段不输出）、照片位注入、density 档、水印模式。
- 产出 (html, config)：config 携带板块自估行数基线（estimatedLines 汇总），
  供前端实测后经 /api/adjust 回写校准（§5.3 record_actual）。
- 模板缺失为致命错误（E_TEMPLATE，§5.6）。
"""
import html as _html
from pathlib import Path
from typing import Optional, Tuple

from ..core.errors import AppError, E_TEMPLATE
from .budget import BudgetTracker

TEMPLATE_FILES = {"one-page": "resume-1page.html", "two-pages": "resume-2pages.html"}
DENSITY_ORDER = ["compact", "normal", "loose"]   # 紧凑 → 松散（adjust 移动档位）
WATERMARK_TEXT = "本简历部分内容由 AI 生成，请确认真实性后再投递"
SKILL_CATEGORY_ORDER = ["专业技能", "工具与框架", "语言能力"]


def _esc(value) -> str:
    return _html.escape(str(value or ""), quote=True)


def _time_range(start: Optional[str], end: Optional[str]) -> str:
    s, e = (start or "").strip(), (end or "").strip()
    return f"{s}—{e}" if (s or e) else ""


def load_template(templates_dir: str, page_option: str) -> str:
    name = TEMPLATE_FILES.get(page_option, TEMPLATE_FILES["one-page"])
    path = Path(templates_dir) / name
    if not path.exists():
        raise AppError(E_TEMPLATE, f"简历模板缺失: {name}")
    return path.read_text(encoding="utf-8")


class Assembler:
    """模板装配器（无状态，可复用）。"""

    def __init__(self, templates_dir: str, storage=None):
        self.templates_dir = templates_dir
        self.storage = storage

    # ------------------------------------------------------------ 入口

    def render(self, resume: dict, blocks: dict, *, density: str = "normal",
               watermark_mode: str = "practice") -> Tuple[str, dict]:
        """装配完整 HTML 与 config。blocks 用于提取自估行数基线（§5.3）。"""
        page_option = resume.get("pageOption", "one-page")
        template = load_template(self.templates_dir, page_option)
        head = template[: template.index("<body")]

        parts = [self._header(resume), self._contact(resume), self._summary(resume, page_option)]
        parts += [self._education(resume), self._internship(resume),
                  self._projects(resume), self._skills(resume), self._honor(resume)]
        if watermark_mode == "practice":
            parts.append(self._watermark())

        html = (head + f'<body data-density="{_esc(density)}">\n'
                + "\n".join(p for p in parts if p) + "\n</body>\n</html>")

        config = {
            "pageOption": page_option,
            "density": density,
            "direction": resume.get("direction", ""),
            "contentPlan": resume.get("contentPlan") or {},
            "blocks": self._estimated_baseline(blocks),
        }
        return html, config

    # ------------------------------------------------------------ 头部/联系

    def _header(self, resume: dict) -> str:
        basic = resume.get("basicInfo") or {}
        name = _esc(basic.get("name"))
        photo = self._photo(resume.get("photo") or {})
        return (
            '<div class="header">\n'
            f'  <div class="name">{name}个人简历</div>\n'
            f"  {photo}\n"
            "</div>"
        )

    def _photo(self, photo: dict) -> str:
        if not photo or not photo.get("filePath"):
            return '<div class="photo empty" id="photo-slot"></div>'
        data_url = ""
        if self.storage is not None:
            try:
                data_url = self.storage.photo_to_data_url(photo["filePath"])
            except OSError:
                data_url = ""
        if not data_url:
            return '<div class="photo empty" id="photo-slot"></div>'
        return f'<div class="photo" id="photo-slot"><img src="{data_url}" alt="照片"></div>'

    def _contact(self, resume: dict) -> str:
        basic = resume.get("basicInfo") or {}
        spans = [
            f"电话：<b>{_esc(basic.get('phone'))}</b>",
            f"邮箱：<b>{_esc(basic.get('email'))}</b>",
        ]
        if basic.get("website"):
            spans.append(_esc(basic["website"]))
        if basic.get("base"):
            spans.append(f"base：{_esc(basic['base'])}")
        if basic.get("internshipDuration"):
            spans.append(_esc(basic["internshipDuration"]))
        if basic.get("startAvailable"):
            spans.append(_esc(basic["startAvailable"]))
        body = "\n".join(f"  <span>{s}</span>" for s in spans)
        return f'<div class="contact">\n{body}\n</div>'

    # ------------------------------------------------------------ 各板块

    def _summary(self, resume: dict, page_option: str) -> str:
        sentences = [str(s.get("text", "")).strip() for s in (resume.get("summary") or [])]
        sentences = [s for s in sentences if s]
        if not sentences:
            return ""
        # 逐句渲染（§5.5）：data-block/data-index 供前端定位点击编辑
        if page_option == "two-pages":
            body = "\n".join(
                f'  <p class="summary-sentence" data-block="summary" data-index="{i}">{_esc(s)}</p>'
                for i, s in enumerate(sentences))
            return ('<div class="section" id="sec-summary">\n  <div class="sec-title">自我评价</div>\n'
                    f'  <div class="item-body">\n{body}\n  </div>\n</div>')
        body = "\n".join(
            f'  <span class="summary-sentence" data-block="summary" data-index="{i}">{_esc(s)}</span>'
            for i, s in enumerate(sentences))
        return f'<div class="summary" id="sec-summary">\n{body}\n</div>'

    def _education(self, resume: dict) -> str:
        items = []
        for e in (resume.get("education") or []):
            sub = " · ".join(x for x in (_esc(e.get("major")), _esc(e.get("degree"))) if x)
            items.append(
                '  <div class="item">\n'
                '    <div class="item-head">\n'
                f'      <span class="item-title">{_esc(e.get("school"))}</span>\n'
                f'      <span class="item-sub">{sub}</span>\n'
                f'      <span class="item-time">{_esc(_time_range(e.get("startMonth"), e.get("endMonth")))}</span>\n'
                "    </div>\n"
                "  </div>"
            )
        if not items:
            return ""
        return ('<div class="section">\n  <div class="sec-title">教育背景</div>\n'
                + "\n".join(items) + "\n</div>")

    def _internship(self, resume: dict) -> str:
        items = []
        for i, it in enumerate(resume.get("internship") or []):
            lis = "\n".join(
                f'        <li data-block="internship" data-index="{i}" data-sub-index="{j}">{_esc(d.get("text"))}</li>'
                for j, d in enumerate(it.get("duties") or []))
            items.append(
                '  <div class="item">\n'
                '    <div class="item-head">\n'
                f'      <span class="item-title">{_esc(it.get("company"))}</span>\n'
                f'      <span class="item-sub">{_esc(it.get("position"))}</span>\n'
                f'      <span class="item-time">{_esc(_time_range(it.get("startMonth"), it.get("endMonth")))}</span>\n'
                "    </div>\n"
                f'    <div class="item-body">\n      <ul>\n{lis}\n      </ul>\n    </div>\n'
                "  </div>"
            )
        if not items:
            return ""   # 空区块删除（非常驻板块）
        return ('<div class="section" id="sec-internship">\n  <div class="sec-title">实习经历</div>\n'
                + "\n".join(items) + "\n</div>")

    def _projects(self, resume: dict) -> str:
        items = []
        for i, p in enumerate(resume.get("project") or []):
            tech = ""
            stack = [str(t) for t in (p.get("techStack") or []) if str(t).strip()]
            if stack:
                tech = f'      <div class="tech"><b>技术栈：</b>{_esc("、".join(stack))}</div>\n'
            lis = "\n".join(
                f'        <li data-block="project" data-index="{i}" data-sub-index="{j}">{_esc(x.get("text"))}</li>'
                for j, x in enumerate(p.get("items") or []))
            items.append(
                '  <div class="item">\n'
                '    <div class="item-head">\n'
                f'      <span class="item-title">{_esc(p.get("name"))}</span>\n'
                f'      <span class="item-sub">{_esc(p.get("role"))}</span>\n'
                f'      <span class="item-time">{_esc(_time_range(p.get("startMonth"), p.get("endMonth")))}</span>\n'
                "    </div>\n"
                f'    <div class="item-body">\n{tech}      <ul>\n{lis}\n      </ul>\n    </div>\n'
                "  </div>"
            )
        if not items:
            return ""
        return ('<div class="section" id="sec-projects">\n  <div class="sec-title">项目经验</div>\n'
                + "\n".join(items) + "\n</div>")

    def _skills(self, resume: dict) -> str:
        groups: dict[str, list[str]] = {}
        for s in (resume.get("skill") or []):
            cat = str(s.get("category") or "其他")
            name = str(s.get("name") or "").strip()
            if not name:
                continue
            groups.setdefault(cat, []).append(name)
        if not groups:
            return ""
        cats = sorted(groups, key=lambda c: (SKILL_CATEGORY_ORDER.index(c)
                                             if c in SKILL_CATEGORY_ORDER else len(SKILL_CATEGORY_ORDER)))
        rows = "\n".join(
            f'  <div class="skill-row"><span class="skill-cat">{_esc(c)}</span>{_esc("、".join(groups[c]))}</div>'
            for c in cats)
        return f'<div class="section" id="sec-skills">\n  <div class="sec-title">技能特长</div>\n{rows}\n</div>'

    def _honor(self, resume: dict) -> str:
        parts = []
        for h in (resume.get("honor") or []):
            name = str(h.get("name") or "").strip()
            if not name:
                continue
            seg = " · ".join(x for x in (_esc(name), _esc(h.get("org")), _esc(h.get("time"))) if x)
            parts.append(f'    <span class="honor">{seg}</span>')
        if not parts:
            return ""   # 空区块删除
        return ('<div class="section" id="sec-honors">\n  <div class="sec-title">证书荣誉</div>\n'
                '  <div class="honors">\n' + "\n".join(parts) + "\n  </div>\n</div>")

    def _watermark(self) -> str:
        return f'<div class="watermark on" id="watermark">{_esc(WATERMARK_TEXT)}</div>'

    # ------------------------------------------------------------ 预算基线（§5.3）

    def _estimated_baseline(self, blocks: dict) -> dict:
        """各描述性板块的自估行数汇总（前端实测后与 actual 比对校准）。"""
        out = {}
        for block in ("summary", "internship", "projects"):
            output = blocks.get(block) or {}
            entries = BudgetTracker.collect_estimated(block, output)
            out[block] = sum(e["estimatedLines"] for e in entries)
        return out
