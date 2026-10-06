# 后端 API / 调度器镜像（文档 10.2）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# tzdata 用于统一时区；psycopg 使用 binary wheel，无需 libpq-dev
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# PIP_INDEX_URL 为可选构建参数：国内网络可指定镜像源加速，默认仍走 PyPI
ARG PIP_INDEX_URL=
RUN pip install ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY samples ./samples

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]