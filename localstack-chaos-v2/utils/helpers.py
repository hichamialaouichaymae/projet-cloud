import json
import os
from datetime import datetime

# Chemin absolu unique, résolu une seule fois au chargement du module.
# Tous les fichiers Python (app.py, chaos_recovery.py, chaos_engine.py,
# monitoring.py) importent ce module → ils partagent tous le même chemin.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG_PATH = os.path.join(_BASE_DIR, "logs", "events.log")


def load_json(path: str, default=None):
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        print(f"[load_json] Erreur parsing JSON ({path}): {e}")
        return default


def log_event(message: str, log_path: str = None):
    """
    Écrit une ligne horodatée dans le fichier de log.
    Utilise toujours DEFAULT_LOG_PATH si log_path n'est pas fourni,
    ce qui garantit que Flask ET les subprocesses écrivent au même endroit.
    """
    if log_path is None:
        log_path = DEFAULT_LOG_PATH
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        ts = datetime.utcnow().isoformat() + "Z"
        line = f"{ts} {message}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()          # flush immédiat → lisible par d'autres processus
            os.fsync(f.fileno())  # force l'écriture sur disque
        print(f"[log] {ts} {message}")
    except OSError as e:
        print(f"[log_event] Erreur écriture: {e}")