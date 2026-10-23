"""The chat pipeline on llama-server (llama.cpp) instead of Ollama.

Only the client and the config are new. The chat steps themselves
(`chat_stream`, `select_model`, `picker_models`) are the Ollama pipeline's,
used as they are: they talk to whatever client the registry hands them.
Auto-routing is Ollama's `Router`, used as it is, with the `router:` block of
llamacpp/config/models.yaml: it picks between the two models.
"""

from . import server_config
from .client import LlamaCppClient
from .config import LlamaCppConfig, config_dir, load_config
from .registry import Registry

__all__ = ["LlamaCppClient", "LlamaCppConfig", "Registry", "config_dir", "load_config", "server_config"]
