import json
import os
from datetime import datetime

def load_json(path: str, default=None):
    """
    Charger un fichier JSON en toute sécurité.
    - path : chemin du fichier
    - default : valeur par défaut si le fichier n'existe pas ou est invalide
    """
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        print(f"[load_json] Erreur de parsing JSON ({path}): {e}")
        return default


def log_event(message: str, log_path: str = None):
    """
    Enregistrer un événement horodaté dans un fichier de logs.
    - message  : texte de l'événement
    - log_path : chemin absolu du fichier de logs.
                 Si None, utilise logs/events.log relatif à ce fichier.
    """
    # Chemin par défaut absolu, cohérent entre Flask et les subprocesses
    if log_path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_path = os.path.join(base, "logs", "events.log")

    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        ts = datetime.utcnow().isoformat() + "Z"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} {message}\n")
        print(f"[log_event] {ts} {message}")
    except OSError as e:
        print(f"[log_event] Erreur écriture log: {e}")