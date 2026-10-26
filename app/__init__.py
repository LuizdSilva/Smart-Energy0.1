import hmac
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, session, redirect, url_for, abort

from . import banco_dados
from .configuracoes import Config

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")

RESOLUCOES = {
    "desligado_automaticamente_esp32": "Desligado automaticamente pelo ESP32",
    "comando_remoto": "Desligado por comando do sistema",
    "desligado_localmente": "Desligado no local (botão)",
    "alerta_encerrado_no_esp32": "Alerta encerrado (presença / nova contagem)",
    "sala_removida": "Sala removida do cadastro",
}

# Itens da barra lateral: (chave, texto, ícone, rota, é_sub_item). Para criar uma página nova, acrescente uma linha aqui.
NAV = [
    ("visao_geral", "Visão Geral", "bi-house-door-fill", "main.visao_geral", False),
    ("tempo_real", "Tempo Real (Salas)", "bi-broadcast", "main.tempo_real", False),
    ("salas", "Cadastrar salas", "bi-plus-circle", "main.cadastro_salas", True),   # sub-item de Tempo Real
    ("consumo", "Consumo", "bi-lightning-charge", "main.consumo", False),
    ("solar", "Geração Solar", "bi-brightness-high", "main.solar", False),
    ("anomalias", "Anomalias", "bi-exclamation-triangle", "main.anomalias", False),
    ("dados", "Gerenciamento de Dados", "bi-database", "main.dados", False),
]


def br_number(value, decimals=0):
    if value is None:
        return "—"
    s = f"{value:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def local_dt(value):
    if not value:
        return "—"
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
    return dt.strftime("%d/%m/%Y %H:%M:%S")


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    banco_dados.init_db(app.config["DB_PATH"], app.config["SEED_FILE"])

    app.jinja_env.filters["br"] = br_number
    app.jinja_env.filters["local_dt"] = local_dt
    app.jinja_env.filters["fromjson"] = lambda v: __import__("json").loads(v or "[]")
    app.jinja_env.filters["resolucao"] = lambda k: RESOLUCOES.get(k, k or "—")

    # MQTT
    app.extensions["mqtt"] = None
    if app.config["MQTT_ENABLED"] and not app.config["TESTING"]:
        from .cliente_mqtt import ClienteMqtt
        cliente = ClienteMqtt(app.config)
        cliente.iniciar()
        app.extensions["mqtt"] = cliente

    # ---- segurança: CSRF + login --------------------------------------
    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    @app.before_request
    def _guard():
        if request.endpoint == "static":
            return None
        public = request.endpoint in ("main.login",)
        if request.method == "POST":
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            if not hmac.compare_digest(sent.encode("utf-8"), session.get("csrf", "\0").encode("utf-8")):
                if request.path.startswith("/api/"):
                    return jsonify(erro="token CSRF inválido"), 403
                abort(400, "Token CSRF inválido. Recarregue a página.")
        if not public and "user" not in session:
            if request.path.startswith("/api/"):
                return jsonify(erro="não autenticado"), 401
            return redirect(url_for("main.login"))
        return None

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.context_processor
    def _ctx():
        return {"NAV": NAV, "csrf_token": csrf_token, "user_name": app.config["APP_USER_NAME"],
                "powerbi_url": app.config["POWERBI_EMBED_URL"]}

    from .rotas import bp
    app.register_blueprint(bp)
    return app
