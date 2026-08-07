"""JL-Agent 后端入口（P1 骨架）：/api/health + 静态前端 + 规则加载。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .core.errors import AppError
from .core.rules import RulesLoader


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    rules = RulesLoader(cfg.paths.rules_dir)
    rules.load_all()  # 规则缺失/非法 → 启动即报错（fail fast）
    app.state.config = cfg
    app.state.rules = rules
    yield


app = FastAPI(title="JL-Agent", version="0.1.0", lifespan=lifespan)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    status = 400 if exc.code < 50000 else 500
    return JSONResponse(
        status_code=status,
        content={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


@app.get("/api/health")
def health():
    rules = app.state.rules
    return {
        "code": 0,
        "message": "ok",
        "data": {"status": "up", "rules": rules.versions},
    }


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
