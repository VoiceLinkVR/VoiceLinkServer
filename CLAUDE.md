# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VoiceLinkVR Server is a Python-based voice transcription and translation service that provides:
- Speech recognition using Whisper and SenseVoice
- Multi-language translation (LibreTranslate + online services)
- User management and authentication (JWT-based)
- Rate limiting and request logging
- Web management interface

**Architecture**: Modern FastAPI application (migrated from Flask) with async support, dependency injection, and automatic API documentation.

## Development Commands

### Local Development
```bash
# Install dependencies
pip install -r src/requirements.txt

# Run development server with hot reload
python run.py

# Run with custom workers (production mode)
UVICORN_WORKERS=4 python run.py

# Run with specific reload setting
UVICORN_RELOAD=true python run.py
```

### Testing
```bash
# Test translation functionality
python test_translate.py
```

### Docker Deployment
```bash
# CPU-only deployment
docker-compose -f docker-compose-cpu.yml up -d

# CUDA deployment (requires CUDA 12.2+)
docker-compose -f docker-compose-cuda.yml up -d

# China mirror deployments
docker-compose -f docker-compose-cpu-cn.yml up -d
docker-compose -f docker-compose-cuda-cn.yml up -d
```

### Production Server
```bash
# Direct uvicorn command
uvicorn src.main:app --host 0.0.0.0 --port 8980 --workers 4
```

## Architecture & Code Structure

### Core Architecture Pattern
- **Framework**: FastAPI with async/await support
- **Database**: SQLAlchemy ORM with SQLite/MySQL support
- **Authentication**: JWT tokens using python-jose
- **Password Hashing**: Werkzeug (bcrypt)
- **Rate Limiting**: slowapi with Redis backend
- **Template Engine**: Jinja2
- **Server**: Uvicorn ASGI server

### Directory Structure
```
src/
├── core/           # Core functionality
│   ├── config.py   # Pydantic settings management
│   ├── dependencies.py # FastAPI dependency injection
│   ├── services.py # Business logic (translation, audio processing)
│   └── limiter.py  # Rate limiting configuration
├── db/             # Database layer
│   ├── base.py     # SQLAlchemy base configuration
│   └── models.py   # ORM models (User, RequestLog)
├── routers/        # API routes
│   ├── api.py      # Main API endpoints (/api/*)
│   ├── manage_api.py # Admin API endpoints (/manageapi/*)
│   └── ui.py       # Web interface routes (/ui/*)
├── schemas/        # Pydantic data models
├── templates/      # HTML templates
├── static/         # Static files
├── main.py         # FastAPI app entry point
└── requirements.txt # Python dependencies
```

### Key Dependencies
- **fastapi**: Modern web framework
- **uvicorn[standard]**: ASGI server
- **sqlalchemy**: ORM and database toolkit
- **python-jose[cryptography]**: JWT implementation
- **slowapi**: Rate limiting
- **httpx**: Async HTTP client
- **translators**: Translation services integration
- **openai**: OpenAI client for Whisper API
- **apscheduler**: Background task scheduling

### External Service Dependencies
- **Whisper Service**: Speech recognition (default: localhost:8000)
- **SenseVoice Service**: Alternative speech recognition (default: localhost:8800)
- **LibreTranslate**: Local translation service (default: localhost:5000)
- **Redis**: Rate limiting backend (optional)

## Key Implementation Details

### Configuration Management
All configuration is managed through `src/core/config.py` using Pydantic Settings:
- Environment variables take precedence over defaults
- `.env` file support for local development
- Service URLs, API keys, database paths, rate limiting settings

### Authentication Flow
1. Users login via `/api/login` with username/password
2. Server returns JWT access token (7-day expiry)
3. Subsequent API calls include token in Authorization header
4. Admin users have `is_admin=True` flag in database

### Rate Limiting
- Global rate limiting via slowapi middleware
- Per-user rate limiting based on `limit_rule` field
- Redis backend for distributed rate limiting
- Default limits: "10000/day;1000/hour"

### Request Logging
- All API requests automatically logged to `request_log` table
- Includes username, IP, endpoint, duration, status
- Middleware-based implementation in `src/main.py`

### Translation Services
1. **Local LibreTranslate**: Primary translation service
2. **Online Translators**: Fallback via `translators` library
3. **Service Priority**: Configurable via `TRANSLATOR_SERVICES_LIST`
4. **Timeout Handling**: 1.5s timeout with failover

### Audio Processing
- Supports WAV and OPUS audio formats
- OPUS decoding via `opuslib`
- Automatic format detection and conversion
- Content filtering for error results

## Common Development Tasks

### Adding New API Endpoints
1. Add route to appropriate router file (`api.py` or `manage_api.py`)
2. Use Pydantic models for request/response validation
3. Implement authentication with `Depends(get_current_user)`
4. Add rate limiting decorator if needed

### Database Schema Changes
1. Update models in `src/db/models.py`
2. Add migration logic to `check_and_update_db()` in `main.py`
3. Test with existing database to ensure compatibility

### Modifying Business Logic
1. Core functions go in `src/core/services.py`
2. Use async/await for I/O operations
3. Add proper error handling and logging
4. Test with `test_translate.py` for translation functions

### Template Modifications
1. HTML templates in `src/templates/`
2. Use `request.url_for()` for URL generation
3. Access session data via `request.session`
4. Pass messages through template context

## Environment Variables

Key environment variables for configuration:
- `WHISPER_HOST`, `WHISPER_PORT`: Whisper service location
- `LIBRETRANSLATE_HOST`, `LIBRETRANSLATE_PORT`: Translation service
- `JWT_SECRET_KEY`: JWT signing key (change in production)
- `SQL_PATH`: Database connection string
- `LIMITER_REDIS_URL`: Redis for rate limiting
- `UVICORN_WORKERS`: Number of worker processes
- `UVICORN_RELOAD`: Enable hot reload (development only)

## Important Notes

- **Security**: Change default JWT_SECRET_KEY in production
- **Database**: SQLite default, MySQL supported via SQLALCHEMY_DATABASE_URL
- **Rate Limiting**: Requires Redis for production deployments
- **Service Dependencies**: Ensure Whisper and LibreTranslate services are running
- **First Admin**: First login creates admin account automatically
- **User Expiration**: Daily cron job disables expired users at midnight UTC
- 这个项目不要本地验证，让我手动验证