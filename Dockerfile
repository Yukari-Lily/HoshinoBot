FROM python:3.8-slim

ENV PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# gcc/libc6-dev/git for C extensions (python-Levenshtein, Pillow, lxml);
# tzdata + localtime so scheduled tasks use Asia/Shanghai.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libc6-dev git libffi-dev tzdata \
    && ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /HoshinoBot

# Install deps first for better layer caching.
COPY requirements.txt /HoshinoBot/requirements.txt
RUN pip install -r /HoshinoBot/requirements.txt \
    && pip install msgpack ujson python-Levenshtein

COPY . /HoshinoBot

RUN chmod +x /HoshinoBot/entrypoint.sh

ENTRYPOINT ["/HoshinoBot/entrypoint.sh"]
CMD ["python3", "run.py"]
