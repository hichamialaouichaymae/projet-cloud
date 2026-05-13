# Image de base légère et moderne
FROM python:3.9-slim

# Évite la mise en cache des logs Python (utile pour le debug)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Répertoire de travail
WORKDIR /app

# Installation des dépendances système utiles (PostgreSQL client, build tools)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Installation des dépendances Python
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copie du projet
COPY . .

# Exposer le port Flask
EXPOSE 5000

# Commande de démarrage
CMD ["python", "app.py"]