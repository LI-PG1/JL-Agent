"""LLM provider 适配层（契约 §1/§5.3）：OpenAI 兼容接口，JSON 模式支持。

默认 DeepSeek；UI 不展示模型名。所有 LLM 调用统一走本类，便于后续多 provider 冗余。
"""
import httpx

from ..config import Config, api_key
from .errors import AppError, E_LLM


class LLMProvider:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def ready(self) -> bool:
        return bool(api_key(self.cfg))

    async def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = False,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        key = api_key(self.cfg)
        if not key:
            raise AppError(E_LLM, "未配置模型 API Key（见 .env）")
        payload = {
            "model": self.cfg.provider.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    f"{self.cfg.provider.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppError(E_LLM, f"LLM 调用失败: {exc}") from exc
