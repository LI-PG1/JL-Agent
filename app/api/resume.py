"""Resume CRUD（契约 §4.2）：POST/GET/PUT/DELETE + 列表 + 条目编辑锁定（§5.5）+ 重装配渲染（§6）。"""
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..core.errors import AppError, E_PARAM
from ..core.validation import check_resume
from ..engine.assembly import Assembler
from ..schemas import CamelModel, Resume

router = APIRouter(prefix="/api/resume", tags=["resume"])

# 可编辑条目块（§5.5）：summary 句 / internship duty / project item
EDITABLE_BLOCKS = ("summary", "internship", "project")


class ResumeIdResp(BaseModel):
    resume_id: str


class DeletedResp(BaseModel):
    deleted: bool


class ResumeListItem(BaseModel):
    id: str
    name: str = ""
    updated_at: Optional[str] = None


class ItemEditBody(CamelModel):
    """编辑锁定（§5.5）：summary 用 index；internship/project 用 index + sub_index 定位叶子。"""
    block: str = Field(min_length=1)                       # summary/internship/project
    index: int = Field(ge=0)                               # 父级下标（summary 为句子下标）
    sub_index: Optional[int] = Field(default=None, ge=0)   # 叶子下标（实习职责/项目要点）
    text: str = Field(min_length=1, max_length=500)


class ItemUnlockBody(CamelModel):
    block: str = Field(min_length=1)
    index: int = Field(ge=0)
    sub_index: Optional[int] = Field(default=None, ge=0)


class RenderBody(BaseModel):
    density: Optional[str] = Field(default=None, pattern="^(compact|normal|loose)$")


@router.post("", response_model=dict)
def create_resume(body: Resume, request: Request):
    """新建简历：无 id 则生成；集中校验后落库。"""
    storage = request.app.state.storage
    now = request.app.state.now
    if not body.id:
        body.id = storage.new_resume_id()
    body.created_at = body.created_at or now()
    body.updated_at = now()
    check_resume(body, request.app.state.config.limits)
    storage.save_resume(body.model_dump(mode="json", by_alias=True, exclude_none=False))
    return {"code": 0, "message": "ok", "data": {"resumeId": body.id}}


@router.get("", response_model=dict)
def list_resumes(request: Request):
    """简历列表（轻量）：前端工作台使用。"""
    storage = request.app.state.storage
    items = []
    for rid in storage.list_resumes():
        data = storage.load_resume(rid)
        name = (data.get("basicInfo") or {}).get("name", "")
        items.append(ResumeListItem(id=rid, name=name, updated_at=data.get("updatedAt")).model_dump())
    items.sort(key=lambda x: x["updated_at"] or "", reverse=True)
    return {"code": 0, "message": "ok", "data": {"items": items}}


@router.get("/{resume_id}", response_model=dict)
def get_resume(resume_id: str, request: Request):
    storage = request.app.state.storage
    data = storage.load_resume(resume_id)
    return {"code": 0, "message": "ok", "data": data}


@router.put("/{resume_id}", response_model=dict)
def update_resume(resume_id: str, body: Resume, request: Request):
    """整存更新：保留 id/created_at，刷新 updated_at。"""
    storage = request.app.state.storage
    now = request.app.state.now
    old = storage.load_resume(resume_id)
    body.id = resume_id
    body.created_at = body.created_at or old.get("created_at")
    body.updated_at = now()
    check_resume(body, request.app.state.config.limits)
    storage.save_resume(body.model_dump(mode="json", by_alias=True, exclude_none=False))
    return {"code": 0, "message": "ok", "data": {"updatedAt": body.updated_at}}


@router.delete("/{resume_id}", response_model=dict)
def delete_resume(resume_id: str, request: Request):
    storage = request.app.state.storage
    storage.load_resume(resume_id)  # 不存在 → 40008
    deleted = storage.delete_resume(resume_id)
    return {"code": 0, "message": "ok", "data": DeletedResp(deleted=deleted).model_dump()}


# ---------------------------------------------------------------- 编辑锁定（§5.5）与重装配（§6）

def _leaf(resume: dict, block: str, index: int, sub_index: Optional[int]) -> dict:
    """定位可编辑叶子条目：summary 句 / 实习职责 / 项目要点。非法定位 → 40001。"""
    if block == "summary":
        if sub_index is not None:
            raise AppError(E_PARAM, "summary 是单层列表，无需 subIndex", {"block": block})
        items = resume.get("summary") or []
        idx = index
    elif block in ("internship", "project"):
        parents = resume.get(block) or []
        if not (0 <= index < len(parents)):
            raise AppError(E_PARAM, f"{block} 下标越界", {"block": block, "index": index})
        key = "duties" if block == "internship" else "items"
        items = parents[index].get(key) or []
        if sub_index is None:
            raise AppError(E_PARAM, f"{block} 需要 subIndex 定位叶子", {"block": block})
        idx = sub_index
    else:
        raise AppError(E_PARAM, "不可编辑板块", {"block": block})
    if not (0 <= idx < len(items)):
        raise AppError(E_PARAM, "条目下标越界",
                       {"block": block, "index": index, "subIndex": sub_index})
    return items[idx]


def _rendered(resume: dict, app) -> dict:
    """重装配（§6）：density 取 resume 现值，返回 {resume, html, config} 供预览。"""
    assembler = Assembler(app.state.config.paths.templates_dir, app.state.storage)
    generation = resume.get("generation") or {}
    html, config = assembler.render(
        resume, {},
        density=str(resume.get("density") or "normal"),
        watermark_mode=str(generation.get("watermarkMode") or "practice"),
    )
    return {"code": 0, "message": "ok",
            "data": {"resume": resume, "html": html, "config": config}}


@router.put("/{resume_id}/item", response_model=dict)
def edit_item(resume_id: str, body: ItemEditBody, request: Request):
    """单条编辑（§5.5）：改文本 + edited:true + criticality 强制 critical → 不可被自动重写。"""
    storage = request.app.state.storage
    resume = storage.load_resume(resume_id)
    leaf = _leaf(resume, body.block, body.index, body.sub_index)
    text = body.text.strip()
    if not text:
        raise AppError(E_PARAM, "文本不能为空", {"block": body.block})
    leaf["text"] = text[: (300 if body.block == "summary" else 500)]
    leaf["edited"] = True
    leaf["criticality"] = "critical"
    resume["updatedAt"] = request.app.state.now()
    storage.save_resume(resume)
    return _rendered(resume, request.app)


@router.post("/{resume_id}/item/unlock", response_model=dict)
def unlock_item(resume_id: str, body: ItemUnlockBody, request: Request):
    """解锁（§5.5）：edited:false → 下次自动生成可重写该条目。"""
    storage = request.app.state.storage
    resume = storage.load_resume(resume_id)
    leaf = _leaf(resume, body.block, body.index, body.sub_index)
    leaf["edited"] = False
    resume["updatedAt"] = request.app.state.now()
    storage.save_resume(resume)
    return _rendered(resume, request.app)


@router.post("/{resume_id}/render", response_model=dict)
def render_resume(resume_id: str, body: RenderBody, request: Request):
    """重装配渲染（§6）：支持 density 手动调整后重新出图。"""
    storage = request.app.state.storage
    resume = storage.load_resume(resume_id)
    if body.density:
        resume["density"] = body.density
        resume["updatedAt"] = request.app.state.now()
        storage.save_resume(resume)
    return _rendered(resume, request.app)
