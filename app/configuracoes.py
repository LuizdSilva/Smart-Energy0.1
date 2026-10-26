import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    # login
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
    APP_USER = os.environ.get("APP_USER", "facens")
    APP_PASSWORD = os.environ.get("APP_PASSWORD", "facens2025")
    APP_USER_NAME = os.environ.get("APP_USER_NAME", "Usuário")
    SESSION_COOKIE_SAMESITE = "Lax"
    MAX_CONTENT_LENGTH = 1024 * 1024 

    # banco de dados
    DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "data" / "smart_energy.db"))
    SEED_FILE = str(BASE_DIR / "data" / "dados_reais_2024.json")

    # MQTT
    MQTT_ENABLED = os.environ.get("MQTT_ENABLED", "1") == "1"
    MQTT_HOST = os.environ.get("MQTT_HOST", "broker.emqx.io")
    MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
    MQTT_BASE_TOPIC = os.environ.get("MQTT_BASE_TOPIC", "smartenergy/facens/demo-48271").strip("/")

    # Power BI (link)
    POWERBI_EMBED_URL = os.environ.get("POWERBI_EMBED_URL", "")

    # tempo real (s)
    OFFLINE_AFTER_S = 15     # sem mensagem do ESP32 por esse tempo: sala offline
    COMMAND_TIMEOUT_S = 10   # comando sem confirmacao do ESP32: expirado
    STORE_INTERVAL_S = 60    # intervalo minimo para gravar leitura sem mudanca