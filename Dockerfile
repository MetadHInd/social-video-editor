# social-video-editor (`sve`) on Linux: Python 3.12 + FFmpeg 7 + Rubber Band + fonts.
#
#   docker build -t sve .
#   docker run --rm sve --help
#   docker run --rm -v "$PWD/workspace:/workspace" --user "$(id -u):$(id -g)" sve report myproject
#
# The mounted folder is the workspace (projects/, music/, sfx/, recipes/); downloaded models and
# generated caches go to /workspace/.cache so they survive between runs.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg (7.x in Debian 13), rubberband-cli (AutoMix tempo matching), libgl1/libglib2.0-0 (OpenCV),
# fonts: Inter / Inter Display (default text styles), DejaVu + Liberation (fallbacks),
# Noto Color Emoji (emoji in text cards).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg rubberband-cli libgl1 libglib2.0-0 \
        fonts-inter fonts-dejavu-core fonts-liberation2 fonts-noto-color-emoji fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/sve
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

# Unprivileged by default; `--user "$(id -u):$(id -g)"` keeps files in the mounted workspace yours.
RUN useradd --uid 1000 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin sve
USER sve

ENV SVE_WORKSPACE=/workspace \
    XDG_CACHE_HOME=/workspace/.cache \
    HOME=/tmp
WORKDIR /workspace

ENTRYPOINT ["python", "-m", "social_video_editor"]
CMD ["--help"]
