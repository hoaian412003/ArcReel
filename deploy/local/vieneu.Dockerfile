# VieNeu-TTS 的 OpenAI 兼容语音服务（CPU / ONNX，无 torch）。
# 构建上下文是 third_party/VieNeu-TTS 检出目录；上游 docker/Dockerfile.cpu 跑的是 Gradio 页面，
# 这里沿用它的依赖安装方式，只把入口换成 ArcReel 需要的 apps.openai_speech。
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT="/opt/venv" \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/workspace/src:/workspace" \
    HF_HOME=/home/app/.cache/huggingface \
    HOST=0.0.0.0 \
    PORT=8000 \
    VIENEU_BACKEND=onnx \
    VIENEU_DEVICE=cpu

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /workspace /opt/venv /home/app/.cache/huggingface \
    && chown -R app:app /workspace /opt/venv /home/app/.cache
WORKDIR /workspace

COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-build

COPY --chown=app:app src/ src/
COPY --chown=app:app apps/ apps/
COPY --chown=app:app config.yaml ./
COPY --chown=app:app examples/audio_ref/ examples/audio_ref/

USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["python", "-m", "apps.openai_speech"]
