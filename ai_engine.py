# -*- coding: utf-8 -*-
"""AI-провайдеры: Gemini (основной, включая Vision) → OpenRouter (текст).

Gemini вызывается напрямую по REST, без устаревшего google-generativeai:
меньше зависимостей и не ломается при смене SDK. Ключи необязательны —
без них AI-команды отвечают понятной ошибкой.
"""

import asyncio
import base64

import aiohttp

from config import (
    AI_TEMPERATURE,
    GEMINI_API_KEY,
    GEMINI_FALLBACK_MODELS,
    GEMINI_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    logger,
)

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _sniff_image_mime(data):
    """MIME по magic bytes — Gemini Vision требует корректный inlineData."""
    if data:
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return 'image/png'
        if data[:3] == b'\xff\xd8\xff':
            return 'image/jpeg'
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            return 'image/webp'
        if data[:6] in (b'GIF87a', b'GIF89a'):
            return 'image/gif'
        if data[:2] == b'BM':
            return 'image/bmp'
    return 'image/png'


class AIError(Exception):
    """Ни один провайдер не дал ответа."""


class AIEngine:
    def __init__(self):
        self._sem = asyncio.Semaphore(2)
        self._model_idx = 0
        seen = []
        for name in (GEMINI_MODEL, *GEMINI_FALLBACK_MODELS):
            if name and name not in seen:
                seen.append(name)
        self._models = tuple(seen)

    async def generate(self, prompt, system_prompt=None, image_bytes=None):
        """Вернуть (текст, провайдер). Сначала Gemini, затем OpenRouter.

        Картинку понимает только Gemini: для OCR нельзя молча отдавать
        запрос текстовой модели (так делал Prim и получал выдуманный текст).
        """
        async with self._sem:
            gem_err = None
            if GEMINI_API_KEY:
                try:
                    return await self._gemini(prompt, system_prompt, image_bytes), "Gemini"
                except Exception as ex:
                    gem_err = ex
                    logger.warning(f"Gemini недоступен: {ex}")
            else:
                gem_err = AIError("GEMINI_API_KEY не задан.")

            if image_bytes:
                raise AIError(
                    f"Для распознавания изображения нужен Gemini: {self._fmt(gem_err)}"
                )

            if OPENROUTER_API_KEY:
                try:
                    return await self._openrouter(prompt, system_prompt), "OpenRouter"
                except Exception as ex:
                    logger.warning(f"OpenRouter недоступен: {ex}")
                    raise AIError(
                        f"AI недоступен: Gemini ({self._fmt(gem_err)}) | "
                        f"OpenRouter ({self._fmt(ex)})"
                    ) from ex

            raise AIError(
                "AI не настроен: задай GEMINI_API_KEY и/или OPENROUTER_API_KEY в .env. "
                f"Gemini: {self._fmt(gem_err)}"
            )

    async def _gemini(self, prompt, system_prompt, image_bytes):
        parts = []
        if prompt:
            parts.append({"text": prompt})
        if image_bytes:
            parts.append({
                "inlineData": {
                    "mimeType": _sniff_image_mime(image_bytes),
                    "data": base64.b64encode(image_bytes).decode('ascii'),
                }
            })
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": AI_TEMPERATURE},
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        timeout = aiohttp.ClientTimeout(total=120)
        last_err = None
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for idx in range(self._model_idx, len(self._models)):
                model = self._models[idx]
                url = _GEMINI_URL.format(model=model)
                try:
                    async with session.post(
                        url,
                        params={"key": GEMINI_API_KEY},
                        json=payload,
                    ) as resp:
                        body = await resp.text()
                        if resp.status != 200:
                            last_err = AIError(f"{model}: HTTP {resp.status} — {body[:300]}")
                            logger.warning(f"Gemini {model} недоступен: HTTP {resp.status}")
                            continue
                        data = await resp.json(content_type=None)
                except (aiohttp.ClientError, asyncio.TimeoutError) as ex:
                    last_err = AIError(f"{model}: сеть недоступна — {ex}")
                    continue

                try:
                    chunks = data["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in chunks).strip()
                except (KeyError, IndexError, TypeError):
                    text = ""
                if not text:
                    last_err = AIError(f"{model}: пустой ответ")
                    logger.warning(f"Gemini {model} вернул пустой ответ")
                    continue
                self._model_idx = idx
                return text

        raise AIError(
            f"Все Gemini-модели недоступны: {self._fmt(last_err)}"
        ) from last_err

    async def _openrouter(self, prompt, system_prompt):
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": messages,
            "temperature": AI_TEMPERATURE,
        }
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(_OPENROUTER_URL, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise AIError(f"HTTP {resp.status} — {body[:300]}")
                    data = await resp.json(content_type=None)
        except aiohttp.ClientError as ex:
            raise AIError(f"сеть недоступна — {ex}") from ex

        try:
            text = (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as ex:
            raise AIError(f"неожиданный ответ — {ex}") from ex
        if not text:
            raise AIError("пустой ответ")
        return text

    @staticmethod
    def _fmt(ex):
        if not ex:
            return '?'
        return str(ex).splitlines()[0][:150]
