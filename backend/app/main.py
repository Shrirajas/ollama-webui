import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .settings import settings  # isort: skip - loads .env before the pipeline reads the environment
from document_engine import DocumentService
from document_engine.invoices import InvoiceService
from document_engine.word import WordService
from ollama_pipeline import Registry, Router, load_config
from sql_connector import ConnectionStore
from workspace_engine import ChatFiles, Embedder, WorkspaceService
from agent_skills import MemoryStore, SkillSettings

from .auth import configure as configure_auth, current_user, init_db, load_secret, router as auth_router, seed_admin
from .access import router as access_router
from .code_exec import init as init_code_exec, router as code_router
from .chats import init as init_chats, router as chats_router
from .model_settings import init as init_model_settings, router as model_settings_router
from .model_server import router as model_server_router
from . import tools
from .tools import admin as tools_admin_router, init as init_tools, router as tools_router
from .documents import Library, Store, router as documents_router
from .documents.invoices import router as invoices_router
from .documents.word import router as word_router
from .routes import public, router
from .skills import admin as features_router, memories as memories_router, router as skills_router
from .sql import router as sql_router
from .workspaces import router as workspaces_router

# uvicorn only sets up its own loggers; without this the pipeline's INFO lines
# (e.g. "Router: Von is ready") never reach the server log.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def init_features(app: FastAPI, data: Path | None = None) -> None:
    """Databases, workspaces, chat files, skills and memories, on `app.state.registry`.
    `data` puts all their folders under one folder instead of settings' (the tests use that)."""
    folder = (lambda name, default: data / name) if data else (lambda name, default: default)
    registry = app.state.registry
    app.state.sql_store = ConnectionStore(folder("sql", settings.sql_dir))
    embedding = Embedder(registry)  # one, so workspaces and chat files share its state
    app.state.workspaces = WorkspaceService(registry, folder("workspaces", settings.workspaces_dir), embedding)
    app.state.chatfiles = ChatFiles(registry, folder("chatfiles", settings.chatfiles_dir), embedding,
                                    registry.config.documents.keep_hours)
    app.state.skills = SkillSettings(folder("skills", settings.skills_dir))
    app.state.memory = MemoryStore(folder("skills", settings.skills_dir))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.pipeline == "llamacpp":
        # Everything on llama-server (llamacpp/), without auto-routing. Ollama is not used.
        from llamacpp_pipeline import Registry as LlamaCppRegistry, load_config as load_llamacpp_config

        config = load_llamacpp_config()
        app.state.registry = LlamaCppRegistry(config)
        logging.info("Pipeline: llama-server at %s", config.hosts[config.default_host])
    else:
        config = load_config()
        app.state.registry = Registry(config)
    app.state.registry.warm_up()
    app.state.router = Router(config.router)
    app.state.router.warm_up()
    app.state.documents = DocumentService(app.state.registry)
    app.state.invoices = InvoiceService(app.state.registry)
    app.state.word = WordService(app.state.registry)
    app.state.store = Store(settings.documents_dir, config.documents.keep_hours)
    app.state.library = Library(settings.documents_dir.parent / "templates")
    init_features(app)
    init_db(settings.auth_db)
    init_chats()
    init_model_settings()
    init_code_exec()
    init_tools()
    tools.KEEP_HOURS = config.documents.keep_hours
    configure_auth(load_secret(settings.auth_secret, settings.auth_db.parent / "auth_secret"), settings.token_hours)
    if seed_admin(settings.admin_email, settings.admin_password):
        logging.info("Auth: first admin %s created", settings.admin_email)
    yield
    await app.state.registry.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Fastshot API", version="0.1.0", lifespan=lifespan)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    @app.middleware("http")
    async def pyodide_cors(request, call_next):
        # The code sandbox page has an opaque origin ("null"), so it needs this to fetch Python's files.
        # Only those files, and read-only: nothing else here is opened to it.
        response = await call_next(request)
        if request.url.path.startswith("/pyodide/"):
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    app.include_router(access_router, prefix="/api/access")  # for n8n: its own key, not a login
    login = [Depends(current_user)]  # every /api route below needs a signed-in user
    app.include_router(public, prefix="/api")
    app.include_router(auth_router, prefix="/api/auth")  # sign in and user admin (admin routes check the role themselves)
    app.include_router(router, prefix="/api", dependencies=login)
    app.include_router(model_settings_router, prefix="/api/admin/models", dependencies=login)  # each route also checks admin
    app.include_router(model_server_router, prefix="/api/admin/llama-server", dependencies=login)  # admin only
    app.include_router(code_router, prefix="/api/code", dependencies=login)  # PUT also checks admin
    app.include_router(tools_admin_router, prefix="/api/admin/tools", dependencies=login)  # the router itself checks admin
    app.include_router(tools_router, prefix="/api/tools", dependencies=login)  # enabled tools, no code
    app.include_router(sql_router, prefix="/api/sql", dependencies=login)  # writes also check admin
    app.include_router(workspaces_router, prefix="/api/workspaces", dependencies=login)  # writes also check admin
    app.include_router(skills_router, prefix="/api/skills", dependencies=login)
    app.include_router(features_router, prefix="/api/admin/features", dependencies=login)  # the router itself checks admin
    app.include_router(memories_router, prefix="/api/memories", dependencies=login)  # each user's own
    app.include_router(chats_router, prefix="/api/chats")  # per-user; each route takes current_user itself
    app.include_router(invoices_router, prefix="/api/documents/invoices", dependencies=login)  # before the /{job} routes below
    app.include_router(word_router, prefix="/api/documents/word", dependencies=login)  # the same
    app.include_router(documents_router, prefix="/api/documents", dependencies=login)

    # Mounted last so /api and /docs win over the static catch-all.
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")

    return app


app = create_app()
