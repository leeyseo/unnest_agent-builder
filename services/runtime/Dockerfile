# 표준 런타임 이미지 — 전체 컴포넌트 의존성 + 임베딩 모델을 빌드 타임에 포함 (원칙 1).
# 빌드 컨텍스트는 모노레포 루트: docker build -f services/runtime/Dockerfile -t agent-runtime .
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY packages/sdk /build/packages/sdk
COPY packages/engine /build/packages/engine
COPY packages/components /build/packages/components
RUN pip install /build/packages/sdk /build/packages/engine /build/packages/components \
    "fastapi>=0.111" "uvicorn>=0.30" && rm -rf /build

# 임베딩 모델을 이미지에 굽는다 — 기동 후 네트워크 다운로드 금지 (원칙 1)
ARG EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV EMBED_MODEL=${EMBED_MODEL} \
    HF_HOME=/opt/models \
    FASTEMBED_CACHE_PATH=/opt/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='${EMBED_MODEL}')"
# 이후 오프라인 강제 — 캐시에 없으면 기동 실패가 낫다 (조용한 다운로드 금지)
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

COPY services/runtime/app.py /app/app.py

ENV FLOW_PATH=/flows/agent.json
EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
