# Casebook API — 단일 컨테이너. sqlite 는 /data 볼륨에 둔다.
FROM python:3.13-slim
WORKDIR /srv
# git — 데모 시드(tools/demo_seed.py)가 임시 저장소를 만들어 커밋을 얹는다. 그래야 데모 스레드의
# 저장소 식별·커밋 연결·anchor 가 실제와 같은 길로 생긴다(확장 105호). slim 에는 git 이 없다.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY casebook ./casebook
COPY main.py ./
RUN pip install --no-cache-dir ".[mcp]" && mkdir -p /data   # [mcp] — 원격 MCP 문(확장 27호)도 같은 이미지에서 뜬다
ENV CASEBOOK_HOST=0.0.0.0 CASEBOOK_PORT=8787 CASEBOOK_DB=/data/casebook.db PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=3).status == 200 else 1)"
CMD ["python", "main.py"]
