FROM node:22-bookworm-slim AS web-build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts ./
COPY web ./web
RUN npm run build

FROM python:3.15-slim-bookworm AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV WEB_DIST_PATH=/app/web-dist MIGRATIONS_PATH=/app/migrations
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY migrations ./migrations
COPY --from=web-build /app/web-dist ./web-dist
EXPOSE 8000
CMD ["uvicorn", "health.main:app", "--host", "0.0.0.0", "--port", "8000"]
