FROM python:3.12-slim
RUN apt-get update && apt-get install -y curl \
    && curl -sL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g npm@latest \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /usr/src/app
COPY ./src/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY ./src .
EXPOSE 8980
ENV WHISPER_HOST=host.docker.internal
ENV WHISPER_PORT=8000
ENV LIBRETRANSLATE_HOST=host.docker.internal
ENV LIBRETRANSLATE_PORT=5000
ENV translators_default_region=CN
ENV TRANSLATOR_SERVICE=alibaba
ENV SQL_PATH=sqlite:////usr/src/app/data/db/users.db
CMD [ "python", "serverstart.py" ]