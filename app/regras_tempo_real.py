"""Regras do tempo real: estado das salas, ocorrencias, comandos e cadastro.Troca dados com o ESP32"""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

EQUIPAMENTOS = ("iluminacao", "ar_condicionado", "todos")
ACOES = ("ligar", "desligar")
CAMPOS_BOOLEANOS = ("movimento", "iluminacao", "ar_condicionado", "alerta")
ALERTA_RECENTE_S = 120  # por quanto tempo o aviso de desperdício continua aparecendo

# Blocos da Facens. A sala é o bloco + o número (ex.: L15, C22, E12).
BLOCOS = ("A", "C", "E", "G", "I", "K", "L")
SALA_VALIDA = re.compile(r"[A-Z][0-9]{1,3}")


def sala_pertence_ao_bloco(predio: str, sala: str) -> bool:
    return predio in BLOCOS and SALA_VALIDA.fullmatch(sala) is not None and sala[0] == predio


# ---------------------------------------------------------------- datas
def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def data_para_texto(data: datetime) -> str:
    return data.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def texto_para_data(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


# ---------------------------------------------------------------- erros
class EstadoInvalido(ValueError):
    pass


class SalaNaoCadastrada(ValueError):
    pass


class CadastroInvalido(ValueError):
    pass


class ComandoInvalido(ValueError):
    pass


# ---------------------------------------------------------------- mensagem do ESP32
def nome_no_topico(sala: str) -> str:
    """A01 vira 'sala-a01', o trecho do tópico MQTT."""
    return f"sala-{sala.lower()}"


def validar_estado(mensagem, topico_sala: str | None = None) -> dict:
    """Confere o JSON recebido do ESP32 e devolve o estado com prédio e sala em maiúsculas."""
    if not isinstance(mensagem, dict):
        raise EstadoInvalido("a mensagem não é um objeto JSON")

    predio, sala = mensagem.get("predio"), mensagem.get("sala")
    if not isinstance(predio, str) or predio.upper() not in BLOCOS:
        raise EstadoInvalido(f"campo 'predio' deve ser um destes blocos: {', '.join(BLOCOS)}")
    if not isinstance(sala, str) or not sala_pertence_ao_bloco(predio.upper(), sala.upper()):
        raise EstadoInvalido("campo 'sala' deve ser o bloco + número (ex.: L15)")

    estado = {"predio": predio.upper(), "sala": sala.upper()}
    for campo in CAMPOS_BOOLEANOS:
        valor = mensagem.get(campo)
        if type(valor) not in (bool, int) or valor not in (0, 1):
            raise EstadoInvalido(f"campo '{campo}' ausente ou não é verdadeiro/falso")
        estado[campo] = bool(valor)

    if topico_sala is not None and topico_sala != nome_no_topico(estado["sala"]):
        raise EstadoInvalido(f"a sala {estado['sala']} não corresponde ao tópico {topico_sala}")
    return estado


# ---------------------------------------------------------------- cadastro de salas
def sala_cadastrada(conn: sqlite3.Connection, predio: str, sala: str) -> bool:
    linha = conn.execute("SELECT 1 FROM salas_cadastradas WHERE predio=? AND sala=?", (predio, sala)).fetchone()
    return linha is not None


def cadastrar_sala(conn: sqlite3.Connection, predio: str, sala: str, descricao: str = "") -> tuple[str, str]:
    predio = (predio or "").strip().upper()
    sala = (sala or "").strip().upper()
    descricao = (descricao or "").strip()

    if predio not in BLOCOS:
        raise CadastroInvalido(f"Bloco inválido: use um destes: {', '.join(BLOCOS)}.")
    if not sala_pertence_ao_bloco(predio, sala):
        raise CadastroInvalido(f"Sala inválida: use o bloco + número (ex.: {predio}15).")
    if len(descricao) > 80:
        raise CadastroInvalido("Descrição muito longa (máximo de 80 caracteres).")

    # o código da sala forma o tópico MQTT, então não pode repetir em outro prédio
    outra = conn.execute("SELECT predio FROM salas_cadastradas WHERE sala=?", (sala,)).fetchone()
    if outra:
        raise CadastroInvalido(f"O código {sala} já está cadastrado no prédio {outra['predio']}.")

    with conn:
        conn.execute(
            "INSERT INTO salas_cadastradas (predio, sala, descricao, criada_em) VALUES (?, ?, ?, ?)",
            (predio, sala, descricao or None, data_para_texto(agora_utc())),
        )
    return predio, sala


def remover_sala(conn: sqlite3.Connection, predio: str, sala: str) -> None:
    """Apaga o cadastro e o último estado. O histórico de leituras e ocorrências fica."""
    predio, sala = (predio or "").upper(), (sala or "").upper()
    if not sala_cadastrada(conn, predio, sala):
        raise CadastroInvalido("Sala não encontrada.")
    agora = data_para_texto(agora_utc())
    with conn:
        conn.execute("DELETE FROM salas_cadastradas WHERE predio=? AND sala=?", (predio, sala))
        conn.execute("DELETE FROM salas WHERE predio=? AND sala=?", (predio, sala))
        conn.execute(
            "UPDATE comandos SET status='expirado' WHERE predio=? AND sala=? AND status='enviado'", (predio, sala)
        )
        conn.execute(
            "UPDATE ocorrencias SET status='resolvida', ts_fim=?, resolucao='sala_removida' "
            "WHERE predio=? AND sala=? AND status='aberta'",
            (agora, predio, sala),
        )


# ---------------------------------------------------------------- comandos
def registrar_comando(conn: sqlite3.Connection, predio: str, sala: str, equipamento: str, acao: str,
                      agora: datetime | None = None) -> int:
    """Grava o comando como 'enviado'. Quem publica no MQTT é o código que chamou."""
    predio, sala = (predio or "").upper(), (sala or "").upper()
    if equipamento not in EQUIPAMENTOS:
        raise ComandoInvalido("equipamento inválido")
    if acao not in ACOES:
        raise ComandoInvalido("ação inválida")
    if equipamento == "todos" and acao != "desligar":
        raise ComandoInvalido("'todos' só aceita 'desligar'")
    if not sala_cadastrada(conn, predio, sala):
        raise ComandoInvalido("sala não cadastrada")

    with conn:
        cursor = conn.execute(
            "INSERT INTO comandos (ts, predio, sala, equipamento, acao, status) VALUES (?, ?, ?, ?, ?, 'enviado')",
            (data_para_texto(agora or agora_utc()), predio, sala, equipamento, acao),
        )
    return cursor.lastrowid


def marcar_comando_falhou(conn: sqlite3.Connection, comando_id: int) -> None:
    with conn:
        conn.execute("UPDATE comandos SET status='falhou' WHERE id=?", (comando_id,))


def expirar_comandos(conn: sqlite3.Connection, timeout_s: int, agora: datetime | None = None) -> None:
    """Comando que o ESP32 não confirmou dentro do prazo passa para 'expirado'."""
    limite = data_para_texto((agora or agora_utc()) - timedelta(seconds=timeout_s))
    with conn:
        conn.execute("UPDATE comandos SET status='expirado' WHERE status='enviado' AND ts < ?", (limite,))


def _efeito_esperado(equipamento: str, acao: str) -> dict:
    ligado = acao == "ligar"
    if equipamento == "todos":
        return {"iluminacao": ligado, "ar_condicionado": ligado}
    return {equipamento: ligado}


# ---------------------------------------------------------------- estado recebido
def processar_estado(conn: sqlite3.Connection, estado: dict, agora: datetime | None = None,
                     intervalo_gravacao_s: int = 60, timeout_comando_s: int = 10) -> dict:
    """Aplica um estado vindo do ESP32.

    Devolve {"abriu": id da ocorrência aberta ou None,
             "fechou": (id, motivo) ou None,
             "confirmados": ids dos comandos confirmados}.
    """
    agora = agora or agora_utc()
    predio, sala = estado["predio"], estado["sala"]
    if not sala_cadastrada(conn, predio, sala):
        raise SalaNaoCadastrada(f"sala {predio}/{sala} não cadastrada")

    expirar_comandos(conn, timeout_comando_s, agora)
    resultado = {"abriu": None, "fechou": None, "confirmados": []}
    with conn:
        alerta_anterior = _salvar_estado(conn, estado, agora)
        _salvar_leitura(conn, estado, agora, intervalo_gravacao_s)
        resultado["confirmados"] = _confirmar_comandos(conn, estado, agora)
        _atualizar_ocorrencia(conn, estado, agora, alerta_anterior, resultado)
    return resultado


def _salvar_estado(conn, estado: dict, agora: datetime) -> bool:
    """Grava o estado atual da sala e devolve se o alerta já estava ligado antes."""
    anterior = conn.execute(
        "SELECT alerta FROM salas WHERE predio=? AND sala=?", (estado["predio"], estado["sala"])
    ).fetchone()
    conn.execute(
        """INSERT INTO salas (predio, sala, movimento, iluminacao, ar_condicionado, alerta, ultimo_visto)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (predio, sala) DO UPDATE SET
               movimento=excluded.movimento, iluminacao=excluded.iluminacao,
               ar_condicionado=excluded.ar_condicionado, alerta=excluded.alerta,
               ultimo_visto=excluded.ultimo_visto""",
        (estado["predio"], estado["sala"], estado["movimento"], estado["iluminacao"],
         estado["ar_condicionado"], estado["alerta"], data_para_texto(agora)),
    )
    return bool(anterior and anterior["alerta"])


def _salvar_leitura(conn, estado: dict, agora: datetime, intervalo_s: int) -> None:
    """Guarda no histórico só quando algo mudou ou quando passou o intervalo."""
    ultima = conn.execute(
        "SELECT * FROM leituras WHERE predio=? AND sala=? ORDER BY id DESC LIMIT 1",
        (estado["predio"], estado["sala"]),
    ).fetchone()
    if ultima is not None:
        mudou = any(bool(ultima[campo]) != estado[campo] for campo in CAMPOS_BOOLEANOS)
        passou = (agora - texto_para_data(ultima["ts"])).total_seconds() >= intervalo_s
        if not mudou and not passou:
            return
    conn.execute(
        """INSERT INTO leituras (ts, predio, sala, movimento, iluminacao, ar_condicionado, alerta)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (data_para_texto(agora), estado["predio"], estado["sala"], estado["movimento"],
         estado["iluminacao"], estado["ar_condicionado"], estado["alerta"]),
    )


def _confirmar_comandos(conn, estado: dict, agora: datetime) -> list[int]:
    """Um comando só é confirmado quando o estado enviado pelo ESP32 já mostra o resultado."""
    pendentes = conn.execute(
        "SELECT id, equipamento, acao FROM comandos WHERE predio=? AND sala=? AND status='enviado'",
        (estado["predio"], estado["sala"]),
    ).fetchall()
    confirmados = []
    for c in pendentes:
        esperado = _efeito_esperado(c["equipamento"], c["acao"])
        if all(estado[campo] == valor for campo, valor in esperado.items()):
            conn.execute(
                "UPDATE comandos SET status='confirmado', ts_confirmacao=? WHERE id=?",
                (data_para_texto(agora), c["id"]),
            )
            confirmados.append(c["id"])
    return confirmados


def _atualizar_ocorrencia(conn, estado: dict, agora: datetime, alerta_anterior: bool, resultado: dict) -> None:
    """Abre uma ocorrência quando o alerta liga e fecha quando os equipamentos desligam."""
    predio, sala, ts = estado["predio"], estado["sala"], data_para_texto(agora)
    aberta = conn.execute(
        "SELECT id, ts_inicio FROM ocorrencias WHERE predio=? AND sala=? AND status='aberta' ORDER BY id DESC LIMIT 1",
        (predio, sala),
    ).fetchone()

    recem_aberta = False
    if estado["alerta"] and not alerta_anterior and aberta is None:
        ligados = [e for e in ("iluminacao", "ar_condicionado") if estado[e]]
        cursor = conn.execute(
            "INSERT INTO ocorrencias (predio, sala, ts_inicio, status, equipamentos_ligados) "
            "VALUES (?, ?, ?, 'aberta', ?)",
            (predio, sala, ts, json.dumps(ligados)),
        )
        aberta = {"id": cursor.lastrowid, "ts_inicio": ts}
        recem_aberta = True
        resultado["abriu"] = cursor.lastrowid

    if aberta is None:
        return

    if not (estado["iluminacao"] or estado["ar_condicionado"]):
        por_comando = conn.execute(
            "SELECT 1 FROM comandos WHERE predio=? AND sala=? AND status='confirmado' AND ts_confirmacao >= ?",
            (predio, sala, aberta["ts_inicio"]),
        ).fetchone()
        if por_comando:
            motivo = "comando_remoto"
        elif recem_aberta:
            motivo = "desligado_automaticamente_esp32"
        else:
            motivo = "desligado_localmente"
    elif not estado["alerta"]:
        motivo = "alerta_encerrado_no_esp32"  # alguém voltou à sala ou a contagem recomeçou
    else:
        return

    conn.execute(
        "UPDATE ocorrencias SET status='resolvida', ts_fim=?, resolucao=? WHERE id=?",
        (ts, motivo, aberta["id"]),
    )
    resultado["fechou"] = (aberta["id"], motivo)


# ---------------------------------------------------------------- dados para a tela
def listar_salas(conn: sqlite3.Connection, offline_apos_s: int, timeout_comando_s: int,
                 agora: datetime | None = None) -> list[dict]:
    """Uma entrada por sala cadastrada, com estado, conexão, ocorrência e comandos pendentes."""
    agora = agora or agora_utc()
    expirar_comandos(conn, timeout_comando_s, agora)

    linhas = conn.execute(
        """SELECT c.predio, c.sala, c.descricao,
                  s.movimento, s.iluminacao, s.ar_condicionado, s.alerta, s.ultimo_visto
           FROM salas_cadastradas c
           LEFT JOIN salas s ON s.predio = c.predio AND s.sala = c.sala
           ORDER BY c.predio, c.sala"""
    ).fetchall()

    salas = []
    for l in linhas:
        predio, sala = l["predio"], l["sala"]
        visto = l["ultimo_visto"]  # None: cadastrada, mas o ESP32 ainda não enviou nada
        segundos = (agora - texto_para_data(visto)).total_seconds() if visto else None

        ocorrencia = conn.execute(
            "SELECT id, ts_inicio, status, resolucao FROM ocorrencias WHERE predio=? AND sala=? ORDER BY id DESC LIMIT 1",
            (predio, sala),
        ).fetchone()
        aberta = recente = None
        if ocorrencia:
            if ocorrencia["status"] == "aberta":
                aberta = dict(ocorrencia)
            if (agora - texto_para_data(ocorrencia["ts_inicio"])).total_seconds() <= ALERTA_RECENTE_S:
                recente = dict(ocorrencia)

        pendentes = conn.execute(
            "SELECT id, equipamento, acao, ts FROM comandos WHERE predio=? AND sala=? AND status='enviado' ORDER BY id",
            (predio, sala),
        ).fetchall()

        luz, ar = bool(l["iluminacao"]), bool(l["ar_condicionado"])
        salas.append({
            "predio": predio,
            "sala": sala,
            "descricao": l["descricao"],
            "movimento": bool(l["movimento"]),
            "iluminacao": luz,
            "ar_condicionado": ar,
            "alerta": bool(l["alerta"]),
            "ultimo_visto": visto,
            "segundos_desde_ultimo": int(segundos) if segundos is not None else None,
            "aguardando_primeira_mensagem": visto is None,
            "online": segundos is not None and segundos <= offline_apos_s,
            "ocorrencia_aberta": aberta,
            "ocorrencia_recente": recente,
            "acao_necessaria": aberta is not None and (luz or ar),
            "comandos_pendentes": [dict(p) for p in pendentes],
        })
    return salas
