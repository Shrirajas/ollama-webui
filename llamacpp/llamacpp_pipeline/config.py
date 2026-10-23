"""Loads llamacpp/config/models.yaml into the config object the chat pipeline already uses.

The result is an `ollama_pipeline.PipelineConfig` with one host (the
llama-server), the `router:` block (Auto between its two models) and no cloud models, so the pipeline, the backend
and the documents services read it exactly as they read Ollama's.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from ollama_pipeline import PipelineConfig
from ollama_pipeline.config import (
    DocumentsConfig, InvoicesConfig, ModelAlias, Theme, _documents, _invoices, _normalize_url, _router, _sql, _workspaces,
)
from ollama_pipeline.config import config_dir as ollama_config_dir

from . import server_config

log = logging.getLogger(__name__)

HOST = "llamacpp"  # the one host name every model here is on
EMBED = "embed"  # the embedding model's own llama-server (llamacpp/server/start-embed.sh), when `embed:` is set


@dataclass(frozen=True)
class LlamaCppConfig(PipelineConfig):
    api_key: str = ""
    # model name on the server -> what it can do ("completion", "vision", "thinking")
    capabilities: dict[str, list[str]] = field(default_factory=dict)


def config_dir() -> Path:
    """llamacpp/config/, or the folder LLAMACPP_PIPELINE_CONFIG names."""
    override = os.environ.get("LLAMACPP_PIPELINE_CONFIG")
    return Path(override) if override else Path(__file__).resolve().parent.parent / "config"


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_config(directory: Path | None = None) -> LlamaCppConfig:
    directory = directory or config_dir()
    log.info("llama.cpp config: %s", directory)
    raw = _read(directory / "models.yaml")

    server = raw.get("server") or {}
    url = _normalize_url(os.environ.get("LLAMACPP_URL") or server.get("url") or "http://127.0.0.1:8101")
    api_key = os.environ.get(server.get("api_key_env") or "LLAMACPP_API_KEY", "")

    hosts = {HOST: url}
    if "embed" in raw:
        hosts[EMBED] = _normalize_url((raw["embed"] or {}).get("url") or "http://127.0.0.1:8102")

    # num_ctx is what one request can use: the model's ctx-size in models.ini (kv-unified, shared by its slots).
    contexts = {name.lower(): v["ctx_size"] for name, v in server_config.read().items() if v["ctx_size"]}
    default_options = dict(raw.get("default_options") or {})
    if contexts:
        default_options.setdefault("num_ctx", min(contexts.values()))

    models: list[ModelAlias] = []
    capabilities: dict[str, list[str]] = {}
    for item in raw.get("models") or []:
        vision, thinking = bool(item.get("vision")), bool(item.get("thinking"))
        tools = item.get("tools", True)  # llama-server's Jinja templates call tools; `tools: false` for a model that cannot
        options = dict(item.get("options") or {})
        if item["model"].lower() in contexts:
            options.setdefault("num_ctx", contexts[item["model"].lower()])
        models.append(
            ModelAlias(
                id=item["id"],
                model=item["model"],
                host=HOST,
                label=item.get("label") or item["id"],
                description=item.get("description") or "",
                system=(item.get("system") or "").strip(),
                options=options,
                vision=vision,
                tools=bool(tools),
                provider="ollama",  # "local, on this machine" to the rest of the app; the host decides the server
            )
        )
        capabilities[item["model"].lower()] = ["completion", *(["vision"] if vision else []), *(["thinking"] if thinking else []),
                                              *(["tools"] if tools else [])]

    ids = {m.id for m in models}
    default_model = raw.get("default_model")
    if default_model and default_model not in ids:
        raise ValueError(f"llamacpp models.yaml: default_model '{default_model}' is not a defined model")

    # The daily themes are shared with the Ollama side; they name no model.
    themes_file = directory / "themes.yaml"
    if not themes_file.exists():
        themes_file = ollama_config_dir() / "themes.yaml"
    themes = [Theme(t["title"], t["prompt"]) for t in _read(themes_file).get("themes") or []]

    by_id = {m.id: m for m in models}
    documents, invoices = raw.get("documents"), raw.get("invoices")
    return LlamaCppConfig(
        hosts=hosts,
        default_host=HOST,
        default_model=default_model,
        discover=False,  # only the models listed in models.yaml
        models=models,
        themes=themes,
        router=_router(raw.get("router") or {}, ids),
        default_options=default_options,
        documents=_documents(documents, by_id) if documents else DocumentsConfig(),
        invoices=_invoices(invoices, by_id) if invoices else InvoicesConfig(),
        sql=_sql(raw.get("sql") or {}, by_id),
        workspaces=_workspaces(raw.get("workspaces") or {}, hosts, HOST),
        api_key=api_key,
        capabilities=capabilities,
    )
