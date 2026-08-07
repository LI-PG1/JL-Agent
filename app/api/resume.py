"""Resume CRUD（契约 §4.2）：POST/GET/PUT/DELETE + 列表。"""
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..core.validation import check_resume
from ..schemas import Resume

router = APIRouter(prefix="/api/resume", tags=["resume"])


class ResumeIdResp(BaseModel):
    resume_id: str


class DeletedResp(BaseModel):
    deleted: bool


class ResumeListItem(BaseModel):
    id: str
    name: str = ""
    updated_at: Optional[str] = None


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
        name = (data.get("basic_info") or {}).get("name", "")
        items.append(ResumeListItem(id=rid, name=name, updated_at=data.get("updated_at")).model_dump())
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
