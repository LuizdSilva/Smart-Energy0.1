import json
from datetime import timedelta

import pytest

from app import regras_tempo_real as regras

T0 = regras.texto_para_data("2026-01-01T12:00:00Z")


def estado(**campos):
    padrao = dict(predio="A", sala="A01", movimento=False, iluminacao=True, ar_condicionado=True, alerta=False)
    return {**padrao, **campos}


def enviar(conn, segundos, **campos):
    """Simula o ESP32 publicando um estado 'segundos' depois do início do teste."""
    return regras.processar_estado(conn, estado(**campos), agora=T0 + timedelta(seconds=segundos))


def tudo_desligado(**campos):
    return dict(iluminacao=False, ar_condicionado=False, **campos)


def status_do_comando(conn, comando_id):
    return conn.execute("SELECT status FROM comandos WHERE id=?", (comando_id,)).fetchone()[0]


def total(conn, tabela):
    return conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]


# ---------------------------------------------------------------- mensagem do ESP32
def test_estado_valido_fica_em_maiusculas():
    msg = {"predio": "a", "sala": "a01", "movimento": False, "iluminacao": True,
           "ar_condicionado": True, "alerta": False}
    e = regras.validar_estado(msg, topico_sala="sala-a01")
    assert (e["predio"], e["sala"]) == ("A", "A01")


@pytest.mark.parametrize("msg", [
    [],
    "texto",
    {"predio": "A"},
    {"predio": "A", "sala": "A01", "movimento": "sim", "iluminacao": True, "ar_condicionado": True, "alerta": False},
    {"predio": "A;DROP", "sala": "A01", "movimento": 0, "iluminacao": 1, "ar_condicionado": 1, "alerta": 0},
    {"predio": "A\n", "sala": "A01", "movimento": 0, "iluminacao": 1, "ar_condicionado": 1, "alerta": 0},
    {"predio": "B", "sala": "B01", "movimento": 0, "iluminacao": 1, "ar_condicionado": 1, "alerta": 0},  # bloco inexistente
    {"predio": "L", "sala": "C22", "movimento": 0, "iluminacao": 1, "ar_condicionado": 1, "alerta": 0},  # sala de outro bloco
])
def test_estado_invalido(msg):
    with pytest.raises(regras.EstadoInvalido):
        regras.validar_estado(msg)


def test_sala_diferente_do_topico():
    with pytest.raises(regras.EstadoInvalido):
        regras.validar_estado(estado(sala="A02"), topico_sala="sala-a01")


# ---------------------------------------------------------------- ocorrências
def test_alerta_abre_uma_ocorrencia_so(conn):
    enviar(conn, 0)
    assert enviar(conn, 3, alerta=True)["abriu"] is not None
    for segundos in (6, 9, 12):  # o alerta continua ligado, mas não abre outra
        assert enviar(conn, segundos, alerta=True)["abriu"] is None
    assert total(conn, "ocorrencias") == 1


def test_esp32_desliga_tudo_junto_com_o_alerta(conn):
    enviar(conn, 0)
    resultado = enviar(conn, 3, **tudo_desligado(alerta=True))
    assert resultado["fechou"][1] == "desligado_automaticamente_esp32"
    linha = conn.execute("SELECT status, equipamentos_ligados FROM ocorrencias").fetchone()
    assert linha["status"] == "resolvida" and json.loads(linha["equipamentos_ligados"]) == []


def test_comando_remoto_fecha_ocorrencia_aberta(conn):
    enviar(conn, 0)
    enviar(conn, 3, alerta=True)
    sala = regras.listar_salas(conn, 15, 10, agora=T0 + timedelta(seconds=4))[0]
    assert sala["acao_necessaria"] is True

    comando = regras.registrar_comando(conn, "A", "A01", "todos", "desligar", agora=T0 + timedelta(seconds=5))
    resultado = enviar(conn, 7, **tudo_desligado(alerta=True))
    assert resultado["confirmados"] == [comando]
    assert resultado["fechou"][1] == "comando_remoto"


def test_alerta_encerrado_pelo_esp32_fecha_ocorrencia(conn):
    enviar(conn, 0)
    enviar(conn, 3, alerta=True)
    resultado = enviar(conn, 6, alerta=False, movimento=True)
    assert resultado["fechou"][1] == "alerta_encerrado_no_esp32"


def test_novo_alerta_depois_de_resolvido_abre_outra_ocorrencia(conn):
    enviar(conn, 0)
    enviar(conn, 3, **tudo_desligado(alerta=True))
    enviar(conn, 6, **tudo_desligado(movimento=True))
    assert enviar(conn, 30, **tudo_desligado(alerta=True))["abriu"] is not None
    assert total(conn, "ocorrencias") == 2


def test_alerta_ja_tratado_nao_abre_de_novo(conn):
    """O estado anterior vem do banco, então reiniciar o sistema não duplica a ocorrência."""
    enviar(conn, 0)
    enviar(conn, 3, alerta=True)
    enviar(conn, 6, **tudo_desligado(alerta=True))
    assert enviar(conn, 9, **tudo_desligado(alerta=True))["abriu"] is None


def test_aviso_recente_aparece_mesmo_com_tudo_desligado(conn):
    enviar(conn, 0)
    enviar(conn, 3, **tudo_desligado(alerta=True))
    agora = regras.listar_salas(conn, 15, 10, agora=T0 + timedelta(seconds=10))[0]
    assert agora["ocorrencia_recente"]["status"] == "resolvida" and agora["acao_necessaria"] is False
    depois = regras.listar_salas(conn, 15, 10, agora=T0 + timedelta(seconds=600))[0]
    assert depois["ocorrencia_recente"] is None


# ---------------------------------------------------------------- comandos
def test_comando_so_confirma_quando_o_estado_muda(conn):
    enviar(conn, 0)
    comando = regras.registrar_comando(conn, "A", "A01", "iluminacao", "desligar", agora=T0 + timedelta(seconds=1))
    enviar(conn, 3)  # o ESP32 ainda informa a luz ligada
    assert status_do_comando(conn, comando) == "enviado"
    enviar(conn, 5, iluminacao=False)
    assert status_do_comando(conn, comando) == "confirmado"


def test_comando_expira_sem_resposta(conn):
    enviar(conn, 0)
    comando = regras.registrar_comando(conn, "A", "A01", "iluminacao", "desligar", agora=T0 + timedelta(seconds=1))
    enviar(conn, 30)
    assert status_do_comando(conn, comando) == "expirado"


@pytest.mark.parametrize("dados", [
    ("A", "A01", "ventilador", "ligar"),
    ("A", "A01", "iluminacao", "reiniciar"),
    ("A", "A01", "todos", "ligar"),
    ("A", "ZZZ", "iluminacao", "ligar"),
])
def test_comando_invalido(conn, dados):
    with pytest.raises(regras.ComandoInvalido):
        regras.registrar_comando(conn, *dados)


# ---------------------------------------------------------------- histórico e conexão
def test_leitura_gravada_so_quando_muda_ou_a_cada_minuto(conn):
    enviar(conn, 0)
    assert total(conn, "leituras") == 1
    enviar(conn, 3)
    enviar(conn, 6)
    assert total(conn, "leituras") == 1  # nada mudou
    enviar(conn, 9, movimento=True)
    assert total(conn, "leituras") == 2  # mudou
    enviar(conn, 80, movimento=True)
    assert total(conn, "leituras") == 3  # passou 1 minuto


def test_sala_fica_offline_sem_mensagens(conn):
    enviar(conn, 0)
    assert regras.listar_salas(conn, 15, 10, agora=T0 + timedelta(seconds=5))[0]["online"] is True
    assert regras.listar_salas(conn, 15, 10, agora=T0 + timedelta(seconds=40))[0]["online"] is False


# ---------------------------------------------------------------- cadastro
def test_cadastro_de_sala(conn):
    assert regras.cadastrar_sala(conn, " l ", "l15", "  Sala 15 ") == ("L", "L15")
    assert regras.cadastrar_sala(conn, "C", "C22") == ("C", "C22")
    # repetida, bloco inexistente, sala de outro bloco, sem número, formato errado
    for predio, sala in [("L", "L15"), ("B", "B01"), ("L", "C22"), ("L", "L"), ("L", "L-15"), ("L", "L1234")]:
        with pytest.raises(regras.CadastroInvalido):
            regras.cadastrar_sala(conn, predio, sala)


def test_estado_de_sala_nao_cadastrada_e_recusado(conn):
    with pytest.raises(regras.SalaNaoCadastrada):
        enviar(conn, 0, sala="A99")
