FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python -m waterai.build && python -m waterai.runtime && useradd --create-home app && mkdir -p /app/runtime && chown -R app:app /app/runtime
USER app
ENV HOST=0.0.0.0 PORT=8000
EXPOSE 8000
CMD ["python", "-m", "waterai.server"]
