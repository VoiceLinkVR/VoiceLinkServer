import time
import logging
import sys
import os

# 添加当前目录到Python路径，确保相对导入正常工作
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from core.config import settings
from db.base import Base, engine
from routers import api, ui, manage_api

# Create database tables
Base.metadata.create_all(bind=engine)

# Initialize Limiter
limiter = Limiter(key_func=get_remote_address, storage_uri=settings.LIMITER_REDIS_URL)

# FastAPI App Initialization
app = FastAPI(title="VoiceLinkVR Server")

# Add Middlewares
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SessionMiddleware, secret_key=settings.FLASK_SECRET_KEY)

# Custom logging middleware
@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    start_time = time.time()
    # You can add logic here to log request start
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    # And log request end here
    return response

# Include Routers
app.include_router(ui.router, tags=["UI"])
app.include_router(api.router, prefix="/api", tags=["API"])
app.include_router(manage_api.router, prefix="/manageapi", tags=["Management API"])

# Mount static files (if any)
# app.mount("/static", StaticFiles(directory="src/static"), name="static")

@app.get("/", summary="Root", include_in_schema=False)
async def read_root():
    return {"message": "Welcome to VoiceLinkVR FastAPI Server!"}

# --- Add scheduler startup/shutdown events here later ---