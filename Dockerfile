FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

# ensure uv can write its cache dir
RUN mkdir -p /.cache/uv && chmod 0777 /.cache/uv

# Copy the project into the image
COPY . /app

# Sync the project into a new environment, asserting the lockfile is up to date
WORKDIR /app

ARG ADMIN_PASS="password"
ENV ADMIN_PASS=${ADMIN_PASS}

ARG AUTH_USER="admin"
ENV AUTH_USER=${AUTH_USER}

ARG QX_TOKEN=""
ENV QX_TOKEN=${QX_TOKEN}

ARG TEST=0
ENV TEST=${TEST}

ARG DEVICE="simulator"
ENV DEVICE=${DEVICE}

ARG SLURM_PROJECT_ID="demo_project"
ENV SLURM_PROJECT_ID=${SLURM_PROJECT_ID}

RUN uv sync --locked --no-dev

RUN chmod -R a+rX /app && chmod -R a+rwX /app/.venv || true

EXPOSE 8000

# Default command runs fastapi pointing at main.py
CMD ["uv", "run", "fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
