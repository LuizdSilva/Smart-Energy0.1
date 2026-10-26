import csv
import hmac
import io
import json
import math
import sqlite3

from flask import (Blueprint, Response, current_app, flash, jsonify, redirect, render_template, request,
                   session, url_for)

from . import analise_dados_reais, banco_dados, regras_tempo_real

bp = Blueprint("main", __name__)


def _conn() -> sqlite3.Connection:
    return banco_dados.connect(current_app.config["DB_PATH"])


def _cfg(key):
    return current_app.config[key]


def _ano(conn) -> int | None:
    anos = analise_dados_reais.anos_disponiveis(conn)
    try:
        ano = int(request.args.get("ano", ""))
    except ValueError:
        ano = None
    return ano if ano in anos else (anos[0] if anos else None)


def _page(conn, template, ativo, titulo, subtitulo, **ctx):
    anos = analise_dados_reais.anos_disponiveis(conn)
    return render_template(template, ativo=ativo, titulo=titulo, subtitulo=subtitulo,
                           anos=anos, ano_sel=_ano(conn), **ctx)


# ---------------------------------------------------------------- auth
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u, p = request.form.get("usuario", ""), request.form.get("senha", "")
        ok = hmac.compare_digest(u.encode(), _cfg("APP_USER").encode()) and \
            hmac.compare_digest(p.encode(), _cfg("APP_PASSWORD").encode())
        if ok:
            session.clear()
            session["user"] = u
            return redirect(url_for("main.visao_geral"))
        flash("Usuário ou senha inválidos.", "danger")
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))


# ---------------------------------------------------------------- páginas
@bp.get("/")
def visao_geral():
    """Página inicial: exclusivamente o Power BI (Sérgio). Os indicadores ficam nas outras páginas do menu."""
    conn = _conn()
    try:
        return _page(conn, "pagina_inicial_powerbi.html", "visao_geral", "Bem-vindo ao SMART ENERGY", "")
    finally:
        conn.close()


@bp.get("/tempo-real")
def tempo_real():
    conn = _conn()
    try:
        return _page(conn, "tempo_real.html", "tempo_real", "Tempo Real — Salas",
                     "Estado das salas via MQTT (dados SIMULADOS pelo ESP32/Wokwi)")
    finally:
        conn.close()


@bp.get("/salas")
def cadastro_salas():
    """Cadastro de salas: só salas cadastradas aparecem em Tempo Real e aceitam mensagens do ESP32."""
    conn = _conn()
    try:
        salas = regras_tempo_real.listar_salas(conn, _cfg("OFFLINE_AFTER_S"), _cfg("COMMAND_TIMEOUT_S"))
        return _page(conn, "cadastro_salas.html", "salas", "Cadastrar salas",
                     "Salas que o sistema monitora (cada sala corresponde a um ESP32)",
                     salas=salas, blocos=regras_tempo_real.BLOCOS, topico_base=_cfg("MQTT_BASE_TOPIC"), slug=regras_tempo_real.nome_no_topico)
    finally:
        conn.close()


@bp.post("/salas/cadastrar")
def salas_cadastrar():
    conn = _conn()
    try:
        try:
            predio, sala = regras_tempo_real.cadastrar_sala(conn, request.form.get("predio"), request.form.get("sala"),
                                                            request.form.get("descricao"))
            flash(f"Sala {sala} (prédio {predio}) cadastrada. Aguardando a primeira mensagem do ESP32.", "success")
        except regras_tempo_real.CadastroInvalido as exc:
            flash(str(exc), "danger")
    finally:
        conn.close()
    return redirect(url_for("main.cadastro_salas"))


@bp.post("/salas/remover")
def salas_remover():
    conn = _conn()
    try:
        try:
            regras_tempo_real.remover_sala(conn, request.form.get("predio"), request.form.get("sala"))
            flash("Sala removida. O histórico (ocorrências e leituras) foi mantido.", "success")
        except regras_tempo_real.CadastroInvalido as exc:
            flash(str(exc), "danger")
    finally:
        conn.close()
    return redirect(url_for("main.cadastro_salas"))


@bp.get("/consumo")
def consumo():
    conn = _conn()
    try:
        ano = _ano(conn)
        cons = analise_dados_reais.consumo_ano(conn, ano) if ano else None
        return _page(conn, "consumo.html", "consumo", "Consumo",
                     "Consumo elétrico mensal da FACENS (dados reais)", cons=cons)
    finally:
        conn.close()


@bp.get("/geracao-solar")
def solar():
    conn = _conn()
    try:
        ano = _ano(conn)
        cons = analise_dados_reais.consumo_ano(conn, ano) if ano else None
        ger = analise_dados_reais.geracao_ano(conn, ano, cons["total"] if cons else None) if ano else None
        return _page(conn, "geracao_solar.html", "solar", "Geração Solar",
                     "Geração renovável da FACENS (dados reais)", cons=cons, ger=ger)
    finally:
        conn.close()


@bp.get("/anomalias")
def anomalias():
    conn = _conn()
    try:
        status = request.args.get("status", "todas")
        consulta = "SELECT * FROM ocorrencias"
        parametros = ()
        if status in ("aberta", "resolvida"):
            consulta += " WHERE status=?"
            parametros = (status,)
        ocorrencias = conn.execute(consulta + " ORDER BY id DESC LIMIT 200", parametros).fetchall()
        abertas = conn.execute("SELECT COUNT(*) FROM ocorrencias WHERE status='aberta'").fetchone()[0]
        return _page(conn, "anomalias.html", "anomalias", "Anomalias",
                     "Ocorrências de possível desperdício registradas pelo sistema",
                     ocorrencias=ocorrencias, status=status, abertas=abertas)
    finally:
        conn.close()


def _csv_response(name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(header)
    w.writerows(rows)
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


@bp.get("/exportar/<tipo>.csv")
def exportar(tipo):
    conn = _conn()
    try:
        if tipo == "consumo":
            rows = conn.execute("SELECT ano, mes, kwh FROM consumo_mensal ORDER BY ano, mes").fetchall()
            return _csv_response("consumo_mensal.csv", ["ano", "mes", "kwh"], [tuple(r) for r in rows])
        if tipo == "ocorrencias":
            rows = conn.execute("SELECT id, predio, sala, tipo, ts_inicio, ts_fim, status, resolucao, "
                                "equipamentos_ligados FROM ocorrencias ORDER BY id").fetchall()
            return _csv_response("ocorrencias.csv", ["id", "predio", "sala", "tipo", "ts_inicio_utc", "ts_fim_utc",
                                                      "status", "resolucao", "equipamentos_ligados"],
                                 [tuple(r) for r in rows])
        return jsonify(erro="tipo inválido"), 404
    finally:
        conn.close()


@bp.get("/dados")
def dados():
    conn = _conn()
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("consumo_mensal", "geracao_anual", "salas", "leituras", "ocorrencias", "comandos")}
        mqtt = current_app.extensions["mqtt"]
        return _page(conn, "gerenciamento_dados.html", "dados", "Gerenciamento de Dados",
                     "Origem dos dados, status do MQTT e importação",
                     counts=counts, mqtt=mqtt.status() if mqtt else None,
                     mqtt_enabled=_cfg("MQTT_ENABLED"))
    finally:
        conn.close()


@bp.post("/dados/importar")
def dados_importar():
    f = request.files.get("arquivo")
    if not f or not f.filename.lower().endswith(".csv"):
        flash("Envie um arquivo .csv com as colunas ano;mes;kwh.", "danger")
        return redirect(url_for("main.dados"))
    try:
        text = f.read().decode("utf-8-sig")
        dialect = ";" if text.splitlines()[0].count(";") >= text.splitlines()[0].count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=dialect)
        if not reader.fieldnames or {"ano", "mes", "kwh"} - {c.strip().lower() for c in reader.fieldnames}:
            raise ValueError("cabeçalho deve conter: ano, mes, kwh")
        rows = []
        for i, r in enumerate(reader, start=2):
            r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
            ano, mes = int(r["ano"]), int(r["mes"])
            kwh = float(r["kwh"].replace(".", "").replace(",", ".")) if "," in r["kwh"] else float(r["kwh"])
            if not (2000 <= ano <= 2100 and 1 <= mes <= 12 and math.isfinite(kwh) and kwh >= 0):
                raise ValueError(f"linha {i}: valores fora do intervalo")
            rows.append((ano, mes, kwh))
        if not rows or len(rows) > 1000:
            raise ValueError("arquivo vazio ou com mais de 1000 linhas")
    except (ValueError, UnicodeDecodeError, IndexError, KeyError) as exc:
        flash(f"Importação cancelada, nada foi alterado: {exc}", "danger")
        return redirect(url_for("main.dados"))
    conn = _conn()
    try:
        with conn:
            conn.executemany("INSERT OR REPLACE INTO consumo_mensal (ano, mes, kwh) VALUES (?,?,?)", rows)
        flash(f"{len(rows)} registros importados/atualizados.", "success")
    finally:
        conn.close()
    return redirect(url_for("main.dados"))


@bp.post("/dados/limpar-simulados")
def dados_limpar():
    conn = _conn()
    try:
        with conn:
            for t in ("comandos", "ocorrencias", "leituras", "salas"):
                conn.execute(f"DELETE FROM {t}")
        flash("Dados simulados (salas, leituras, ocorrências e comandos) apagados. Dados reais preservados.", "success")
    finally:
        conn.close()
    return redirect(url_for("main.dados"))


# ---------------------------------------------------------------- API (usada pelo JavaScript)
@bp.get("/api/salas")
def api_salas():
    conn = _conn()
    try:
        salas = regras_tempo_real.listar_salas(conn, _cfg("OFFLINE_AFTER_S"), _cfg("COMMAND_TIMEOUT_S"))
        return jsonify(salas=salas)
    finally:
        conn.close()


@bp.post("/api/salas/<predio>/<sala>/comando")
def api_comando(predio, sala):
    corpo = request.get_json(silent=True) or {}
    equipamento, acao = corpo.get("equipamento"), corpo.get("acao")
    cliente_mqtt = current_app.extensions["mqtt"]

    conn = _conn()
    try:
        try:
            comando_id = regras_tempo_real.registrar_comando(conn, predio, sala, equipamento, acao)
        except regras_tempo_real.ComandoInvalido as erro:
            return jsonify(erro=str(erro)), 400

        if cliente_mqtt is None or not cliente_mqtt.publicar_comando(sala.upper(), equipamento, acao):
            regras_tempo_real.marcar_comando_falhou(conn, comando_id)
            return jsonify(erro="Broker MQTT indisponível, o comando não foi enviado.", id=comando_id), 503

        # 202 = comando enviado. Só vira "confirmado" quando o ESP32 publicar o novo estado.
        return jsonify(id=comando_id, status="enviado"), 202
    finally:
        conn.close()


@bp.get("/api/ocorrencias")
def api_ocorrencias():
    conn = _conn()
    try:
        linhas = conn.execute("SELECT * FROM ocorrencias ORDER BY id DESC LIMIT 100").fetchall()
        ocorrencias = []
        for linha in linhas:
            ocorrencia = dict(linha)
            ocorrencia["equipamentos_ligados"] = json.loads(linha["equipamentos_ligados"])
            ocorrencias.append(ocorrencia)
        return jsonify(ocorrencias=ocorrencias)
    finally:
        conn.close()


@bp.get("/api/status")
def api_status():
    cliente_mqtt = current_app.extensions["mqtt"]
    if cliente_mqtt is None:
        return jsonify(mqtt={"conectado": False, "desativado": True})
    return jsonify(mqtt=cliente_mqtt.status())
