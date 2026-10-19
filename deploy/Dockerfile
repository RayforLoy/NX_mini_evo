FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml /app/
COPY nxmini /app/nxmini
RUN pip install --no-cache-dir .
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["nxmini", "serve"]
