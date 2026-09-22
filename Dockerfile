# Single production service: Starlette API + the built React UI, one origin. Read-only; no secrets in the image (TG_* come from the platform at run time).
FROM node:20-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM python:3.10-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY README.md case_pack.csv ./
COPY config/calibration_v1.json ./config/calibration_v1.json
COPY cases ./cases
COPY demo/records ./demo/records
COPY --from=ui /ui/dist ./ui/dist
RUN useradd --system --no-create-home app
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/api/health',timeout=4)"
CMD ["sh", "-c", "exec uvicorn ui_api.server:app --app-dir src --host 0.0.0.0 --port ${PORT}"]
