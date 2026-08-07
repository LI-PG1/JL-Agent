"""设置控制台（§5.4 本地配置）：API Key 管理与插件默认值。

存储于 data/settings.json（git 忽略）。apiKey 写入后立即注入环境变量，
无需重启即对 LLMProvider / SearchClient（os.getenv）生效。
"""
import os
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import Field

from ..config import mask_key
from ..schemas import CamelModel

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsBody(CamelModel):
    api_key: Optional[str] = Field(default=None, max_length=512)
    deep_search_default: Optional[bool] = None
    watermark_default: Optional[str] = Field(default=None, pattern="^(formal|practice)$")


@router.get("", response_model=dict)
def get_settings(request: Request):
    s = request.app.state.storage.load_settings()
    key = s.get("apiKey", "")
    return {"code": 0, "message": "ok", "data": {
        "hasKey": bool(key),
        "apiKeyMasked": mask_key(key),
        "deepSearchDefault": bool(s.get("deepSearchDefault", True)),
        "watermarkDefault": s.get("watermarkDefault", "formal"),
    }}


@router.put("", response_model=dict)
def put_settings(body: SettingsBody, request: Request):
    storage = request.app.state.storage
    cfg = request.app.state.config
    s = storage.load_settings()
    if body.api_key is not None:
        key = body.api_key.strip()
        s["apiKey"] = key
        if key:
            os.environ[cfg.provider.api_key_env] = key   # 立即生效（无需重启）
        else:
            os.environ.pop(cfg.provider.api_key_env, None)
    if body.deep_search_default is not None:
        s["deepSearchDefault"] = body.deep_search_default
    if body.watermark_default is not None:
        s["watermarkDefault"] = body.watermark_default
    storage.save_settings(s)
    return {"code": 0, "message": "ok", "data": {
        "hasKey": bool(s.get("apiKey", "")),
        "apiKeyMasked": mask_key(s.get("apiKey", "")),
    }}
