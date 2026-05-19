from datetime import timedelta
import logging
import os
from pathlib import Path

from flask import Flask, send_from_directory
from flask_cors import CORS

from auth import auth_bp
from Database.alerts import alerts_bp
from Database.interface import interface_bp, initialize_default_interfaces
from Database.regles import regles_bp
from Database.traffic import traffic_bp
from Database.vlan import vlan_bp
from equipements_api import equipements_bp
from log_api import log_bp
from network_api import network_bp
from run_bat_api import run_bat_bp
from users import users_bp
from dashboard_api import dashboard_bp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "Frontend"))


def _load_dotenv_file():
    env_path = Path(BASE_DIR) / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv_file()


def _get_cors_origins():
    raw_origins = os.getenv("CORS_ORIGINS", "").strip()
    if not raw_origins:
        return ["http://127.0.0.1:5000", "http://localhost:5000"]
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]





app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
app.url_map.strict_slashes = False
logging.basicConfig(level=logging.INFO)

CORS(app, resources={r"/*": {"origins": _get_cors_origins()}}, supports_credentials=False)

app.register_blueprint(users_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(alerts_bp)
app.register_blueprint(traffic_bp)
app.register_blueprint(regles_bp)
app.register_blueprint(vlan_bp)
app.register_blueprint(interface_bp)
app.register_blueprint(network_bp)
app.register_blueprint(equipements_bp)
app.register_blueprint(run_bat_bp)
app.register_blueprint(log_bp)


try:
    initialize_default_interfaces()
except Exception as exc:
    app.logger.error("Initialisation des interfaces impossible: %s", exc)


# ── Endpoint de santé (utilisé par le notifier) ──────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok"}, 200


@app.get("/")
def serve_index():
    return send_from_directory(FRONTEND_DIR, "login.html")


@app.get("/<path:path>")
def serve_frontend(path):
    target = os.path.normpath(os.path.join(FRONTEND_DIR, path))

    if not target.startswith(FRONTEND_DIR):
        return {"error": "Chemin invalide"}, 404

    if os.path.isfile(target):
        return send_from_directory(FRONTEND_DIR, path)

    return send_from_directory(FRONTEND_DIR, "login.html")


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
