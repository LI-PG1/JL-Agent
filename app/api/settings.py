"""设置控制台（§5.4 本地配置）：多 Provider 管理 + 插件默认值。

存储于 data/settings.json（git 忽略）。结构：
{
  "apiKey": "",              # 兼容旧版：单 Key（等价于一个 DeepSeek provider）
  "deepSearchDefault": true,
  "watermarkDefault": "formal",
  "searchApiKey": "",        # 联网搜索（Tavily）Key
  "providers": [{id, name, baseUrl, model, apiKey, capabilities, enabled, order}],
  "activeProviderId": "..."
}
- 激活 provider 的 Key 写入环境变量（os.environ），无需重启即对 LLMProvider 生效。
- 自检：POST /api/settings/providers/test 用最小请求验证 Key / Base URL / 模型。
"""
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from pydantic import Field

from ..config import Config, mask_key
from ..core.errors import AppError
from ..schemas import CamelModel

router = APIRouter(prefix="/api/settings", tags=["settings"])

PROVIDER_FIELDS = ("id", "name", "baseUrl", "model", "apiKey", "capabilities", "enabled", "order")


def _providers_view(s: dict) -> list[dict]:
    """脱敏视图：apiKey 只保留掩码，供前端展示。"""
    out = []
    for p in s.get("providers") or []:
        row = {k: p.get(k) for k in PROVIDER_FIELDS if k in p}
        row["apiKeyMasked"] = mask_key(p.get("apiKey", ""))
        row["apiKey"] = None
        out.append(row)
    return out


def _inject_active_key(s: dict, cfg: Config) -> None:
    """激活 provider 的 Key 注入环境变量（LLMProvider 的 env 兜底路径）。"""
    providers = s.get("providers") or []
    aid = s.get("activeProviderId")
    key = ""
    for p in providers:
        if p.get("id") == aid and p.get("enabled", True):
            key = p.get("apiKey", "")
            break
    if key:
        os.environ[cfg.provider.api_key_env] = key
    else:
        os.environ.pop(cfg.provider.api_key_env, None)


def _next_id(s: dict) -> str:
    from datetime import datetime
    import uuid
    return f"p_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}"


class SettingsBody(CamelModel):
    api_key: Optional[str] = Field(default=None, max_length=512)
    search_api_key: Optional[str] = Field(default=None, max_length=512)
    deep_search_default: Optional[bool] = None
    watermark_default: Optional[str] = Field(default=None, pattern="^(formal|practice)$")


class ProviderBody(CamelModel):
    id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=256)
    model: str = Field(min_length=1, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=512)
    capabilities: str = Field(default="text", max_length=64)
    enabled: bool = True


class TestBody(CamelModel):
    base_url: str = Field(min_length=1, max_length=256)
    model: str = Field(min_length=1, max_length=128)
    api_key: str = Field(min_length=1, max_length=512)


@router.get("", response_model=dict)
def get_settings(request: Request):
    s = request.app.state.storage.load_settings()
    key = s.get("apiKey", "")
    skey = s.get("searchApiKey", "")
    providers = s.get("providers") or []
    # 旧版单 Key 迁移：无 providers 时折叠为一条默认配置（便于前端展示/管理）
    if not providers and key:
        providers = [{
            "id": "p_default", "name": "DeepSeek（默认）",
            "baseUrl": request.app.state.config.provider.base_url,
            "model": request.app.state.config.provider.model,
            "apiKey": key, "capabilities": "text", "enabled": True, "order": 0,
        }]
        s["providers"] = providers
        s["activeProviderId"] = "p_default"
        request.app.state.storage.save_settings(s)
    return {"code": 0, "message": "ok", "data": {
        "hasKey": bool(key),
        "apiKeyMasked": mask_key(key),
        "searchHasKey": bool(skey),
        "searchApiKeyMasked": mask_key(skey),
        "deepSearchDefault": bool(s.get("deepSearchDefault", True)),
        "watermarkDefault": s.get("watermarkDefault", "formal"),
        "providers": _providers_view(s),
        "activeProviderId": s.get("activeProviderId", ""),
    }}


@router.put("", response_model=dict)
def put_settings(body: SettingsBody, request: Request):
    storage = request.app.state.storage
    cfg = request.app.state.config
    s = storage.load_settings()
    if body.api_key is not None:
        key = body.api_key.strip()
        s["apiKey"] = key
        # 同步为默认 provider（无 providers 时），保证生成链路可用
        if key:
            os.environ[cfg.provider.api_key_env] = key
        else:
            os.environ.pop(cfg.provider.api_key_env, None)
    if body.search_api_key is not None:
        skey = body.search_api_key.strip()
        s["searchApiKey"] = skey
        if skey:
            os.environ[cfg.search.api_key_env] = skey
        else:
            os.environ.pop(cfg.search.api_key_env, None)
    if body.deep_search_default is not None:
        s["deepSearchDefault"] = body.deep_search_default
    if body.watermark_default is not None:
        s["watermarkDefault"] = body.watermark_default
    storage.save_settings(s)
    return {"code": 0, "message": "ok", "data": {
        "hasKey": bool(s.get("apiKey", "")),
        "apiKeyMasked": mask_key(s.get("apiKey", "")),
    }}


@router.put("/providers", response_model=dict)
def upsert_provider(body: ProviderBody, request: Request):
    """新增 / 更新 provider（body.apiKey 留空 = 更新时保留原 Key）。"""
    storage = request.app.state.storage
    cfg = request.app.state.config
    s = storage.load_settings()
    providers = s.get("providers") or []

    if body.id:
        target = next((p for p in providers if p.get("id") == body.id), None)
        if not target:
            raise AppError(40001, f"provider 不存在: {body.id}", {"id": body.id})
        target.update({
            "name": body.name.strip(), "baseUrl": body.base_url.strip(),
            "model": body.model.strip(), "capabilities": body.capabilities.strip() or "text",
            "enabled": body.enabled,
        })
        if body.api_key is not None and body.api_key.strip():
            target["apiKey"] = body.api_key.strip()
        pid = body.id
    else:
        pid = _next_id(s)
        providers.append({
            "id": pid, "name": body.name.strip(), "baseUrl": body.base_url.strip(),
            "model": body.model.strip(), "apiKey": (body.api_key or "").strip(),
            "capabilities": body.capabilities.strip() or "text",
            "enabled": body.enabled, "order": len(providers),
        })
    s["providers"] = providers
    # 新增配置 → 自动激活（用户刚配置的 Key 立即生效）；更新当前激活项 → 保持激活
    if not body.id or s.get("activeProviderId") == body.id:
        s["activeProviderId"] = pid
    storage.save_settings(s)
    _inject_active_key(s, cfg)
    return {"code": 0, "message": "ok", "data": {
        "providers": _providers_view(s),
        "activeProviderId": s.get("activeProviderId", ""),
    }}


@router.delete("/providers/{provider_id}", response_model=dict)
def delete_provider(provider_id: str, request: Request):
    storage = request.app.state.storage
    cfg = request.app.state.config
    s = storage.load_settings()
    providers = s.get("providers") or []
    keep = [p for p in providers if p.get("id") != provider_id]
    if len(keep) == len(providers):
        raise AppError(40001, f"provider 不存在: {provider_id}", {"id": provider_id})
    s["providers"] = keep
    if s.get("activeProviderId") == provider_id:
        # 删除激活项 → 自动指向剩余第一个启用项
        s["activeProviderId"] = next((p.get("id") for p in keep if p.get("enabled", True)), "")
    storage.save_settings(s)
    _inject_active_key(s, cfg)
    return {"code": 0, "message": "ok", "data": {
        "providers": _providers_view(s),
        "activeProviderId": s.get("activeProviderId", ""),
    }}


@router.post("/providers/{provider_id}/activate", response_model=dict)
def activate_provider(provider_id: str, request: Request):
    storage = request.app.state.storage
    cfg = request.app.state.config
    s = storage.load_settings()
    providers = s.get("providers") or []
    if not any(p.get("id") == provider_id for p in providers):
        raise AppError(40001, f"provider 不存在: {provider_id}", {"id": provider_id})
    s["activeProviderId"] = provider_id
    storage.save_settings(s)
    _inject_active_key(s, cfg)
    return {"code": 0, "message": "ok", "data": {
        "providers": _providers_view(s),
        "activeProviderId": s.get("activeProviderId", ""),
    }}


@router.post("/providers/test", response_model=dict)
async def test_provider(body: TestBody, request: Request):
    """配置自检：向 Base URL 发一次最小 chat 请求，验证 Key / 模型可用。"""
    payload = {
        "model": body.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{body.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {body.api_key}"},
                json=payload,
            )
            r.raise_for_status()
            return {"code": 0, "message": "ok", "data": {"ok": True}}
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"code": 0, "message": "ok", "data": {"ok": False, "error": str(exc)}}
