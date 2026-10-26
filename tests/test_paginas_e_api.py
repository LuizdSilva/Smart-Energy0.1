import json

from app import regras_tempo_real
from app.cliente_mqtt import ClienteMqtt

BASE = "smartenergy/facens/demo-48271"
GOOD = {"predio": "A", "sala": "A01", "movimento": False, "iluminacao": True, "ar_condicionado": True, "alerta": False}


def svc(app):
    return ClienteMqtt(app.config)


def test_login_required(client):
    assert client.get("/").status_code == 302
    assert client.get("/api/salas").status_code == 401


def test_login_wrong_password(client):
    client.get("/login")
    with client.session_transaction() as s:
        tok = s["csrf"]
    r = client.post("/login", data={"usuario": "u", "senha": "x", "csrf_token": tok})
    assert r.status_code == 200 and b"inv\xc3\xa1lidos" in r.data


def test_post_without_csrf_rejected(auth):
    assert auth.post("/api/salas/A/A01/comando", json={}).status_code == 403
    assert auth.post("/logout").status_code == 400


def test_all_pages_render(auth):
    for url in ("/", "/tempo-real", "/consumo", "/geracao-solar", "/anomalias", "/dados",
                "/anomalias?status=aberta", "/consumo?ano=1999", "/salas"):
        r = auth.get(url)
        assert r.status_code == 200, url
    consumo = auth.get("/consumo").data.decode()
    assert "1.135.655" in consumo and "94.638" in consumo           # total + média mensal
    solar = auth.get("/geracao-solar").data.decode()
    assert "160.684" in solar and "14,15%" in solar and "1.135.655" in solar
    assert 'id="kpi-online"' in auth.get("/tempo-real").data.decode()
    assert "Ocorrências abertas" in auth.get("/anomalias").data.decode()


def test_home_is_powerbi_only(app, auth):
    html = auth.get("/").data.decode()
    assert "Bem-vindo ao SMART ENERGY" in html
    for banned in ("1.135.655", "160.684", "14,15%", "66,7", "Prédios e salas", "Ocorrências abertas", "Consumo Total"):
        assert banned not in html, banned
    assert "pbi-placeholder" in html and "<iframe" not in html
    app.config["POWERBI_EMBED_URL"] = "https://app.powerbi.com/reportEmbed?reportId=abc"
    assert "<iframe" in auth.get("/").data.decode()


def test_powerbi_embed_only_on_home(app, auth):
    app.config["POWERBI_EMBED_URL"] = "https://app.powerbi.com/reportEmbed?reportId=abc"
    assert "reportEmbed" in auth.get("/").data.decode()
    for url in ("/consumo", "/tempo-real", "/dados"):
        assert "reportEmbed" not in auth.get(url).data.decode()


def test_mqtt_message_flow_and_rejections(app, conn):
    s = svc(app)
    assert s.tratar_mensagem(f"{BASE}/sala-a01/estado", json.dumps(GOOD).encode())
    assert conn.execute("SELECT COUNT(*) FROM salas").fetchone()[0] == 1
    assert not s.tratar_mensagem(f"{BASE}/sala-a01/estado", b"nao e json")
    assert not s.tratar_mensagem(f"{BASE}/sala-a02/estado", json.dumps(GOOD).encode())   # sala != tópico
    assert not s.tratar_mensagem("outro/topico/estado", json.dumps(GOOD).encode())
    assert not s.tratar_mensagem(f"{BASE}/sala-a01/comando", json.dumps(GOOD).encode())
    assert not s.tratar_mensagem(f"{BASE}/sala-a01/estado", b"x" * 3000)
    assert s.mensagens_ok == 1 and s.mensagens_descartadas == 5


def test_command_api_flow(app, auth, conn):
    regras_tempo_real.processar_estado(conn, regras_tempo_real.validar_estado(GOOD))
    r = auth.post("/api/salas/A/A01/comando", json={"equipamento": "todos", "acao": "desligar"},
                  headers={"X-CSRF-Token": auth.csrf})
    assert r.status_code == 202
    assert app.extensions["mqtt"].published == [("A01", "todos", "desligar")]
    snap = auth.get("/api/salas").get_json()["salas"][0]
    assert len(snap["comandos_pendentes"]) == 1 and snap["iluminacao"] is True   # tela NÃO assume mudança
    regras_tempo_real.processar_estado(conn, regras_tempo_real.validar_estado({**GOOD, "iluminacao": False, "ar_condicionado": False}))
    snap = auth.get("/api/salas").get_json()["salas"][0]
    assert snap["comandos_pendentes"] == [] and snap["iluminacao"] is False


def test_command_api_errors(app, auth, conn):
    h = {"X-CSRF-Token": auth.csrf}
    assert auth.post("/api/salas/L/L99/comando", json={"equipamento": "todos", "acao": "desligar"},
                     headers=h).status_code == 400    # sala não cadastrada
    regras_tempo_real.processar_estado(conn, regras_tempo_real.validar_estado(GOOD))
    assert auth.post("/api/salas/A/A01/comando", json={"equipamento": "x", "acao": "ligar"},
                     headers=h).status_code == 400
    app.extensions["mqtt"].ok = False
    r = auth.post("/api/salas/A/A01/comando", json={"equipamento": "iluminacao", "acao": "ligar"}, headers=h)
    assert r.status_code == 503
    assert conn.execute("SELECT status FROM comandos").fetchone()[0] == "falhou"


def test_csv_import_valid_and_invalid(auth, conn):
    h = {"csrf_token": auth.csrf}
    import io
    ok = io.BytesIO("ano;mes;kwh\n2025;1;1000\n2025;2;1.234,5\n".encode())
    auth.post("/dados/importar", data={**h, "arquivo": (ok, "x.csv")}, content_type="multipart/form-data")
    assert conn.execute("SELECT kwh FROM consumo_mensal WHERE ano=2025 AND mes=2").fetchone()[0] == 1234.5
    bad = io.BytesIO("ano;mes;kwh\n2025;13;5\n".encode())
    auth.post("/dados/importar", data={**h, "arquivo": (bad, "y.csv")}, content_type="multipart/form-data")
    assert conn.execute("SELECT COUNT(*) FROM consumo_mensal WHERE ano=2025").fetchone()[0] == 2


def test_clear_simulated_keeps_real(auth, conn):
    regras_tempo_real.processar_estado(conn, regras_tempo_real.validar_estado({**GOOD, "alerta": True}))
    auth.post("/dados/limpar-simulados", data={"csrf_token": auth.csrf})
    assert conn.execute("SELECT COUNT(*) FROM salas").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM consumo_mensal").fetchone()[0] == 12


def test_exports(auth):
    for t in ("consumo", "ocorrencias"):
        r = auth.get(f"/exportar/{t}.csv")
        assert r.status_code == 200 and r.mimetype == "text/csv"
    assert auth.get("/exportar/x.csv").status_code == 404


def test_csrf_header_non_ascii_does_not_crash(auth):
    r = auth.post("/api/salas/A/A01/comando", json={}, headers={"X-CSRF-Token": "çã"})
    assert r.status_code == 403


def test_csv_rejects_infinity(auth, conn):
    import io
    bad = io.BytesIO("ano;mes;kwh\n2025;1;inf\n".encode())
    auth.post("/dados/importar", data={"csrf_token": auth.csrf, "arquivo": (bad, "z.csv")},
              content_type="multipart/form-data")
    assert conn.execute("SELECT COUNT(*) FROM consumo_mensal WHERE ano=2025").fetchone()[0] == 0


def test_room_registration_flow(auth, conn):
    h = {"csrf_token": auth.csrf}
    # A01 já vem cadastrada na primeira criação do banco
    assert "A01" in auth.get("/salas").data.decode()
    auth.post("/salas/cadastrar", data={**h, "predio": "l", "sala": "l15", "descricao": "Lab"})
    assert conn.execute("SELECT predio, sala FROM salas_cadastradas WHERE sala='L15'").fetchone()[0] == "L"
    snap = auth.get("/api/salas").get_json()["salas"]
    nova = [x for x in snap if x["sala"] == "L15"][0]
    assert nova["aguardando_primeira_mensagem"] and not nova["online"] and nova["descricao"] == "Lab"
    # código duplicado em outro prédio e entradas inválidas são recusados
    auth.post("/salas/cadastrar", data={**h, "predio": "C", "sala": "L15"})
    auth.post("/salas/cadastrar", data={**h, "predio": "A;--", "sala": "A1"})
    auth.post("/salas/cadastrar", data={**h, "predio": "B", "sala": "B01"})
    assert conn.execute("SELECT COUNT(*) FROM salas_cadastradas").fetchone()[0] == 2
    # remover mantém histórico e apaga o estado
    regras_tempo_real.processar_estado(conn, regras_tempo_real.validar_estado({**GOOD, "sala": "L15", "predio": "L", "alerta": True}))
    auth.post("/salas/remover", data={**h, "predio": "L", "sala": "L15"})
    assert conn.execute("SELECT COUNT(*) FROM salas_cadastradas WHERE sala='L15'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM salas WHERE sala='L15'").fetchone()[0] == 0
    assert conn.execute("SELECT status, resolucao FROM ocorrencias WHERE sala='L15'").fetchone()["resolucao"] == "sala_removida"


def test_removed_default_room_not_recreated_on_restart(app, auth, conn):
    from app import create_app
    auth.post("/salas/remover", data={"csrf_token": auth.csrf, "predio": "A", "sala": "A01"})
    create_app({"TESTING": True, "DB_PATH": app.config["DB_PATH"], "MQTT_ENABLED": False,
                "APP_USER": "u", "APP_PASSWORD": "p", "SECRET_KEY": "k"})
    assert conn.execute("SELECT COUNT(*) FROM salas_cadastradas").fetchone()[0] == 0


def test_mqtt_state_from_unregistered_room_is_discarded(app, conn):
    s = svc(app)
    bad = {**GOOD, "sala": "L99", "predio": "L"}
    assert not s.tratar_mensagem(f"{BASE}/sala-l99/estado", json.dumps(bad).encode())
    assert conn.execute("SELECT COUNT(*) FROM salas").fetchone()[0] == 0
    assert s.mensagens_descartadas == 1
