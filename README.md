# CSC ACF Quantum Demo

Backend for a Quantum entanglement demo game for CSC ACF conference 2025

# Running locally

## Set environment variables

Create a `.env` file in `/backend` and add your variables accroding to `backend/.example-env`

## Docker

```bash
docker compose up --build
```

## UV

### Install backend depenencies

Recommended way to install python dependencies is via uv.
Uv can be installed by running:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

With uv one can install dependencies with:

```bash
uv sync #or 'uv sync --no-dev' for no dev dependencies
```

### Running manually

```bash
uv run fastapi run main.py --host 0.0.0.0 --port 8000
```