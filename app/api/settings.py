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
import shutil
import subprocess
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from pydantic import Field

from ..config import Config, mask_key
from ..core.errors import AppError
from ..schemas import CamelModel

router = APIRouter(prefix="/api/settings", tags=["settings"])

PROVIDER_FIELDS = ("id", "name", "baseUrl", "model", "apiKey", "capabilities", "enabled", "order")

# 可集成插件注册表（外部 CLI 工具；双层启动：一键配置 + 手动勾选）
# runtime.manager/bin 用于依赖检测与自动安装；features 为功能模块（精细控制）；defaultConfig 为默认参数。
PLUGIN_REGISTRY = [
    {
        "id": "zhihu-cli",
        "name": "zhihu-cli",
        "category": "内容获取",
        "source": "https://github.com/dawnswwwww/zhihu-cli",
        "description": "知乎内容获取：按关键词搜索高赞回答与资料。npm 一键安装、免 Cookie（基于知乎开放平台 API）。",
        "runtime": {"manager": "npm", "bin": "zhihu-cli",
                    "install": ["npm", "install", "-g", "zhihu-cli"]},
        "features": [
            {"id": "search", "name": "关键词搜索", "default": True},
            {"id": "hot", "name": "热榜获取", "default": False},
            {"id": "article", "name": "回答/文章下载", "default": False},
        ],
        "defaultConfig": {"language": "zh", "maxResults": 10, "format": "json"},
    },
    {
        "id": "ats-checker",
        "name": "ats-checker",
        "category": "ATS 预检",
        "source": "https://github.com/pranavraut033/ats-checker",
        "description": "投递前简历 ATS 兼容性评分（0-100），零依赖 npm 工具。",
        "runtime": {"manager": "npm", "bin": "ats-checker",
                    "install": ["npm", "install", "-g", "ats-checker"]},
        "features": [
            {"id": "score", "name": "ATS 评分", "default": True},
            {"id": "report", "name": "详细报告", "default": False},
        ],
        "defaultConfig": {"format": "json"},
    },
    {
        "id": "markdown-cv",
        "name": "markdown-cv",
        "category": "模板输出",
        "source": "https://github.com/elipapa/markdown-cv",
        "description": "将简历输出为 Markdown 格式，便于网页/文档场景复用。",
        "runtime": {"manager": "git", "bin": "markdown-cv",
                    "install": ["git", "clone", "--depth", "1",
                                "https://github.com/elipapa/markdown-cv.git", "markdown-cv"]},
        "features": [
            {"id": "render", "name": "Markdown 渲染", "default": True},
            {"id": "pdf", "name": "PDF 导出", "default": False},
        ],
        "defaultConfig": {"format": "markdown"},
    },
]


def _plugin_or_404(plugin_id: str) -> dict:
    for p in PLUGIN_REGISTRY:
        if p["id"] == plugin_id:
            return p
    raise AppError(40001, f"插件不存在: {plugin_id}", {"pluginId": plugin_id})


def _plugins_view(s: dict) -> list[dict]:
    """插件注册表 + 双层启动状态（启用勾选 + 一键配置结果）。"""
    enabled = s.get("pluginsEnabled") or {}
    states = s.get("pluginState") or {}
    out = []
    for p in PLUGIN_REGISTRY:
        row = dict(p)
        row["enabled"] = bool(enabled.get(p["id"], False))
        st = states.get(p["id"]) or {}
        row["configured"] = bool(st.get("configured", False))
        row["installStatus"] = st.get("installStatus", "not-configured")
        row["installMsg"] = st.get("installMsg", "")
        row["features"] = st.get("features") or {
            f["id"]: bool(f.get("default", False)) for f in p.get("features") or []}
        row["featuresList"] = p.get("features") or []
        row["config"] = st.get("config") or {}
        out.append(row)
    return out


def _run_install(runtime: dict) -> tuple[str, str]:
    """执行自动安装（列表参数、无 shell、超时 180s）；返回 (installStatus, msg)。"""
    cmd = list(runtime.get("install") or [])
    if not cmd:
        return "failed", "未配置自动安装命令"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        if proc.returncode == 0 and shutil.which(runtime.get("bin", "")):
            return "installed", "自动安装完成：" + " ".join(cmd)
        return "failed", f"自动安装失败(exit={proc.returncode})：{tail or '详见终端'}"
    except FileNotFoundError:
        return "failed", "无法执行安装命令，请手动安装：" + " ".join(cmd)
    except subprocess.TimeoutExpired:
        return "failed", "安装超时（180s），请手动安装：" + " ".join(cmd)


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


class PluginBody(CamelModel):
    enabled: bool


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
        "plugins": _plugins_view(s),
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


@router.put("/plugins/{plugin_id}", response_model=dict)
def toggle_plugin(plugin_id: str, body: PluginBody, request: Request):
    """第二层：手动勾选启用/停用插件（精细控制，不与一键配置冲突）。"""
    storage = request.app.state.storage
    s = storage.load_settings()
    _plugin_or_404(plugin_id)
    enabled = s.setdefault("pluginsEnabled", {})
    enabled[plugin_id] = body.enabled
    storage.save_settings(s)
    return {"code": 0, "message": "ok", "data": {"plugins": _plugins_view(s)}}


class FeatureBody(CamelModel):
    enabled: bool


@router.put("/plugins/{plugin_id}/features/{feature_id}", response_model=dict)
def toggle_feature(plugin_id: str, feature_id: str, body: FeatureBody, request: Request):
    """第二层：功能模块级精细控制（单模块启用/停用）。"""
    storage = request.app.state.storage
    p = _plugin_or_404(plugin_id)
    if not any(f["id"] == feature_id for f in p.get("features") or []):
        raise AppError(40001, f"功能模块不存在: {feature_id}", {"pluginId": plugin_id, "featureId": feature_id})
    s = storage.load_settings()
    state = s.setdefault("pluginState", {}).setdefault(plugin_id, {})
    feats = state.setdefault("features", {})
    feats[feature_id] = body.enabled
    storage.save_settings(s)
    return {"code": 0, "message": "ok", "data": {"plugins": _plugins_view(s)}}


@router.post("/plugins/{plugin_id}/configure", response_model=dict)
def configure_plugin(plugin_id: str, request: Request, auto_install: bool = True):
    """第一层：一键配置——依赖环境检测 → 自动安装 → 默认参数写入 → 基础功能预激活。

    幂等可重复执行；auto_install=False 仅检测不安装（供测试/预检）；安装失败时
    configured=False 并返回手动安装指引，不影响手动勾选。
    """
    storage = request.app.state.storage
    p = _plugin_or_404(plugin_id)
    s = storage.load_settings()
    state = s.setdefault("pluginState", {}).setdefault(plugin_id, {})
    runtime = p.get("runtime") or {}
    bin_name = runtime.get("bin", plugin_id)

    # 1) 依赖环境检测
    installed = shutil.which(bin_name) is not None
    status, msg = ("installed", "运行环境已就绪") if installed else (None, "")

    # 2) 自动安装（缺依赖、开启 auto_install 且存在包管理器时）
    if not installed and auto_install:
        manager = shutil.which(runtime.get("manager", ""))
        if not manager:
            status, msg = "failed", f"缺少包管理器 {runtime.get('manager', '')}，请先安装后重试"
        else:
            status, msg = _run_install(runtime)
            installed = status == "installed"
    elif not installed and not auto_install:
        status, msg = "failed", f"未检测到可执行程序 {bin_name}（自动安装已跳过）"

    # 3) 默认参数写入 + 基础功能预激活
    state["config"] = dict(p.get("defaultConfig") or {})
    state["features"] = {f["id"]: bool(f.get("default", False)) for f in p.get("features") or []}
    state["installStatus"] = status
    state["installMsg"] = msg
    if installed:
        state["configured"] = True
        state["installTime"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        s.setdefault("pluginsEnabled", {})[plugin_id] = True   # 联动：预激活 → 启用
    else:
        state["configured"] = False
    storage.save_settings(s)
    return {"code": 0, "message": "ok", "data": {"plugins": _plugins_view(s)}}
