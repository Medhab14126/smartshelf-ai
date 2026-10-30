"""Minimal client for a local Ollama server.

Uses `/api/chat` with a JSON schema in `format`, so the model is constrained
to produce JSON matching our Pydantic models. Nothing here ever leaves the
machine: the base URL must point to a self-hosted Ollama instance.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Ollama is not reachable or the configured model is not installed."""


class OllamaClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Ollama is not reachable at {self.base_url}") from exc
        return [m.get("name", "") for m in response.json().get("models", [])]

    def has_model(self, model: str) -> bool:
        names = self.list_models()
        wanted = model if ":" in model else f"{model}:latest"
        return any(name == wanted or name == model for name in names)

    def chat_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        images: list[bytes] | None = None,
    ) -> str:
        message: dict[str, Any] = {"role": "user", "content": user}
        if images:
            message["images"] = [base64.b64encode(img).decode("ascii") for img in images]
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, message],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": 8192},
        }
        started = time.monotonic()
        try:
            response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError(f"model {model} timed out after {self.timeout:.0f}s") from exc
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(f"local AI model server (Ollama) is not reachable at {self.base_url}") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Ollama request failed: {exc.__class__.__name__}") from exc
        if response.status_code == 404:
            raise LLMUnavailableError(f"model {model} is not installed in Ollama (run: ollama pull {model})")
        if response.status_code >= 400:
            raise LLMUnavailableError(f"Ollama returned HTTP {response.status_code}")
        logger.info("LLM %s answered in %.1fs", model, time.monotonic() - started)
        return response.json().get("message", {}).get("content", "")


def get_llm_client() -> OllamaClient:
    settings = get_settings()
    return OllamaClient(settings.ollama_base_url, settings.llm_timeout_seconds)


def llm_status() -> dict[str, Any]:
    settings = get_settings()
    status: dict[str, Any] = {
        "enabled": settings.llm_enabled,
        "model": settings.ollama_model,
        "vision_model": settings.ollama_vision_model or None,
        "reachable": False,
        "model_available": False,
        "vision_model_available": False,
    }
    if not settings.llm_enabled:
        status["detail"] = "LLM disabled by configuration; rule-based extraction is used"
        return status
    try:
        client = get_llm_client()
        names = client.list_models()
        status["reachable"] = True
        status["model_available"] = client.has_model(settings.ollama_model)
        if settings.ollama_vision_model:
            status["vision_model_available"] = client.has_model(settings.ollama_vision_model)
        if not status["model_available"]:
            status["detail"] = f"Model {settings.ollama_model} not installed. Run: ollama pull {settings.ollama_model}"
        status["installed_models"] = names
    except LLMUnavailableError as exc:
        status["detail"] = f"{exc}. Rule-based extraction is used instead."
    return status
