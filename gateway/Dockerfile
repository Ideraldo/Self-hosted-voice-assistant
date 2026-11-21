# Imagem do gateway. O contexto de build e a raiz do projeto, porque `common/`
# e o contrato compartilhado e precisa entrar junto.
FROM python:3.12-slim

# Sem .pyc no disco da imagem, e log sem buffer para `docker compose logs`
# mostrar o que esta acontecendo em tempo real.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# So as dependencias do gateway (D13): sem torch, sem modelo, sem bancada.
COPY requirements-gateway.txt .
RUN pip install --no-cache-dir -r requirements-gateway.txt

COPY common/ ./common/
COPY gateway/ ./gateway/

# O processo nao precisa de root, e um dia isso roda exposto numa VPS.
# `gateway/data` nasce aqui, ja com o dono certo: o volume do compose herda o
# dono do diretorio da imagem, e sem isso o refresh token do Spotify nao
# conseguiria ser regravado por um usuario que nao e root.
RUN useradd --create-home --uid 1000 ideraldinho \
    && mkdir -p gateway/data \
    && chown -R ideraldinho:ideraldinho /app
USER ideraldinho

EXPOSE 8000

# Sem curl na imagem slim; o proprio Python responde se o app subiu.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"]

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
