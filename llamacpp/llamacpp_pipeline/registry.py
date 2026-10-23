"""The model registry for llama-server: Ollama's registry with a llama.cpp client in place of Ollama's."""

from __future__ import annotations

from ollama_pipeline import ModelEntry, ModelNotFound, OllamaUnavailable
from ollama_pipeline import Registry as OllamaRegistry
from ollama_pipeline.opencode import OpenCodeClient

from .client import LlamaCppClient
from .config import EMBED, LlamaCppConfig


class Registry(OllamaRegistry):
    def __init__(self, config: LlamaCppConfig):
        # Not super().__init__(): that would open an Ollama client for each host.
        self.config = config
        self._clients = {
            name: LlamaCppClient(url, config.api_key, config.capabilities) for name, url in config.hosts.items()
        }
        self.opencode = OpenCodeClient(config.opencode)  # never used: no cloud models here, nothing is started
        self._capabilities = {}

    async def _installed(self, host: str):
        # The embedding server (start-embed.sh) serves no chat model: it is not asked each time models are listed.
        return [] if host == EMBED else await super()._installed(host)

    async def resolve(self, model_id: str | None) -> ModelEntry:
        """As Ollama's, with messages that point at llama-server instead of `ollama serve` and `ollama pull`."""
        url = self.config.hosts[self.config.default_host]
        try:
            return await super().resolve(model_id)
        except OllamaUnavailable as exc:
            raise OllamaUnavailable(f"Can't reach llama-server at {url}. Is its container running?") from exc
        except ModelNotFound:
            entry = next((e for e in await self.list_models() if e.id == model_id), None)
            if entry and not entry.available:
                raise ModelNotFound(
                    f"{entry.label} is not served by llama-server at {url}: `{entry.model}` must be a section "
                    "of llamacpp/server/models.ini."
                ) from None
            raise
