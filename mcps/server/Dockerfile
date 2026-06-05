# Serveur MCP HTTPS read-only — image générique (sert iafec ET projea via env).
FROM python:3.12-slim

# Pas de .pyc, logs non bufferisés (pour voir les logs en temps réel via docker logs).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dépendances d'abord (cache de couche).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code.
COPY server.py .

# Utilisateur non-root (durcissement ; le conteneur n'a besoin d'aucun privilège).
RUN useradd --create-home --uid 10001 mcp
USER mcp

# Le port est interne au conteneur ; exposé seulement en 127.0.0.1 par docker-compose,
# puis fronté par Apache. MCP_PORT par défaut = 8080 (voir server.py / .env).
EXPOSE 8080

CMD ["python", "server.py"]
