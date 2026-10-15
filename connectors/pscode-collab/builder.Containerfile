# Builder image for the Code-OSS vscode-reh-web server bundle.
# Carries the documented Linux build prerequisites so no host package set changes.
FROM docker.io/library/node:24-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential pkg-config python3 git ca-certificates \
      libx11-dev libxkbfile-dev libsecret-1-dev libkrb5-dev \
    && rm -rf /var/lib/apt/lists/*
