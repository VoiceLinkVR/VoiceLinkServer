FROM python:3.12-slim
WORKDIR /usr/src/app
COPY ./src/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY ./src .
EXPOSE 8980
ENV WHISPER_HOST=host.docker.internal
ENV WHISPER_PORT=8000
ENV LIBRETRANSLATE_HOST=host.docker.internal
ENV LIBRETRANSLATE_PORT=5000
ENV SQLITE_PATH=/usr/src/app/data/db/
CMD [ "python", "serverstart.py" ]