FROM python:3.12-slim
RUN sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list.d/debian.sources
RUN apt-get update && apt-get install -y curl \
    && curl -sL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get install -y libopus-dev \
    && npm install -g npm@latest \
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
ENV WHISPER_HOST=host.docker.internal
ENV WHISPER_PORT=8000
ENV LIBRETRANSLATE_HOST=host.docker.internal
ENV LIBRETRANSLATE_PORT=5000
ENV translators_default_region=CN
ENV TRANSLATOR_SERVICE=alibaba
ENV SQL_PATH=sqlite:////usr/src/app/data/db/users.db

# 使用 Uvicorn 启动 - main.py 在工作目录中
CMD ["python", "run.py"]