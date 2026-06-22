FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list.d/debian.sources
RUN apt-get update && apt-get install -y curl \
    && curl -sL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get install -y libopus-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /usr/src/app

# 设置 Pypi 镜像源 (可选)
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY ./src/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ./src .
COPY run.py .
EXPOSE 8980

# 环境变量保持不变
ENV VOICELINK_RUNTIME_MODE=all
ENV VOICELINK_RUN_SCHEDULER=true
ENV VOICELINK_RUN_SCHEMA_UPGRADE=true
ENV WHISPER_HOST=host.docker.internal
ENV WHISPER_PORT=8000
ENV WHISPER_ENDPOINTS=
ENV WHISPER_BACKEND=faster-whisper
ENV WHISPER_ASR_TIMEOUT=30
ENV ASR_BACKEND_STATUS_ENABLED=true
ENV ASR_BACKEND_STATUS_TTL_SECONDS=1.5
ENV ASR_BACKEND_STATUS_TIMEOUT_SECONDS=0.6
ENV ASR_BACKEND_STATUS_OVERLOAD_ACTIVE_REQUESTS=4
ENV ASR_BACKEND_STATUS_OVERLOAD_OLDEST_SECONDS=30
ENV ASR_BACKEND_STATUS_OVERLOAD_MIN_SECONDS=12
ENV ASR_BACKEND_STATUS_RECOVERY_ACTIVE_REQUESTS=2
ENV ASR_BACKEND_STATUS_RECOVERY_OLDEST_SECONDS=12
ENV WHISPER_TRT_HOST=host.docker.internal
ENV WHISPER_TRT_PORT=8000
ENV ASR_DEFAULT_ENGINE=firered
ENV FIRERED_ASR_HOST=192.168.2.81
ENV FIRERED_ASR_PORT=8008
ENV FIRERED_ASR_TIMEOUT=120
ENV translators_default_region=CN
ENV TRANSLATOR_SERVICE=alibaba
ENV SQL_PATH=sqlite:////usr/src/app/data/db/users.db

# 使用 Uvicorn 启动 - main.py 在工作目录中
CMD ["python", "run.py"]
