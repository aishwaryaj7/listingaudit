# syntax=docker/dockerfile:1
# One image for the review API, which starts the toolbox as its own process, and for the
# review screen. The embedding model and CLIP come from data/models, so build it after
# the quick start (see README):
#
#   docker build --platform linux/amd64 -t listingaudit .

FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# PyTorch's Linux wheels on PyPI bring about 4 GB of CUDA libraries that a CPU never
# uses. With TORCH=cpu, the default, every package comes from uv.lock, with its hash,
# except PyTorch, which comes in its locked version from PyTorch's CPU index.
# TORCH=locked installs uv.lock as it is.
ARG TORCH=cpu
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv <<'EOF'
set -eu
skip=""
if [ "$TORCH" = cpu ]; then
    skip=$(sed -n -E 's/^name = "(torch|torchvision|triton|cuda-.+|nvidia-.+)"$/--no-emit-package \1/p' uv.lock)
fi
uv export --frozen --no-dev --no-emit-project $skip -o /tmp/app.txt
uv venv
uv pip install --no-deps -r /tmp/app.txt
if [ "$TORCH" = cpu ]; then
    uv export --frozen --no-dev --no-emit-project --no-hashes \
        | grep -E '^torch(vision)?==' | cut -d' ' -f1 | sed 's/$/+cpu/' > /tmp/torch.txt
    uv pip install --no-deps --index-url https://download.pytorch.org/whl/cpu -r /tmp/torch.txt
fi
EOF

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-deps -e . && python -m compileall -q src


FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /app ./
COPY db ./db
# The app writes only to data/: photos from Blob Storage are kept there after first use
COPY --chown=app:app data/models ./data/models
RUN chown app:app data

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_CLIENT_TOOLBAR_MODE=viewer \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
USER app
EXPOSE 8000 8501

# The review API; the screen runs from the same image with
#   streamlit run src/listingaudit/review/screen.py --server.address 0.0.0.0
CMD ["uvicorn", "listingaudit.review.api:app", "--host", "0.0.0.0", "--port", "8000"]
