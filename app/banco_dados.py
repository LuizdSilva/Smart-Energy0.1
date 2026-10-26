import json
import sqlite3
from pathlib import Path

from .regras_tempo_real import iso, utcnow

TABELAS = """
CREATE TABLE IF NOT EXISTS consumo_mensal (
    ano INTEGER NOT NULL,
    mes INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    kwh REAL NOT NULL CHECK (kwh >= 0),
    PRIMARY KEY (ano, mes)
);

CREATE TABLE IF NOT EXISTS totais_anuais (
    ano INTEGER PRIMARY KEY,
    consumo_kwh_oficial REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS geracao_anual (
    ano INTEGER NOT NULL,
    fonte TEXT NOT NULL,
    kwh REAL NOT NULL,
    tipo TEXT,
    PRIMARY KEY (ano, fonte)
);

CREATE TABLE IF NOT EXISTS salas_cadastradas (
    predio TEXT NOT NULL,
    sala TEXT NOT NULL,
    descricao TEXT,
    criada_em TEXT NOT NULL,
    PRIMARY KEY (predio, sala)
);

CREATE TABLE IF NOT EXISTS salas (
    predio TEXT NOT NULL,
    sala TEXT NOT NULL,
    movimento INTEGER NOT NULL,
    iluminacao INTEGER NOT NULL,
    ar_condicionado INTEGER NOT NULL,
    alerta INTEGER NOT NULL,
    ultimo_visto TEXT NOT NULL,
    PRIMARY KEY (predio, sala)
);

CREATE TABLE IF NOT EXISTS leituras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    predio TEXT NOT NULL,
    sala TEXT NOT NULL,
    movimento INTEGER NOT NULL,
    iluminacao INTEGER NOT NULL,
    ar_condicionado INTEGER NOT NULL,
    alerta INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leituras_sala ON leituras (predio, sala, id);

CREATE TABLE IF NOT EXISTS ocorrencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    predio TEXT NOT NULL,
    sala TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'desperdicio',
    ts_inicio TEXT NOT NULL,
    ts_fim TEXT,
    status TEXT NOT NULL CHECK (status IN ('aberta', 'resolvida')),
    resolucao TEXT,
    equipamentos_ligados TEXT NOT NULL  -- lista em JSON
);

CREATE TABLE IF NOT EXISTS comandos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    predio TEXT NOT NULL,
    sala TEXT NOT NULL,
    equipamento TEXT NOT NULL,
    acao TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('enviado', 'confirmado', 'expirado', 'falhou')),
    ts_confirmacao TEXT
);
"""

SALAS_INICIAIS = [("A", "A01", "Sala de demonstracao")]


def connect(caminho: str) -> sqlite3.Connection:
    """Abre uma conexao nova. O Flask e a thread do MQTT nao podem dividir a mesma."""
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db(caminho: str, arquivo_dados: str) -> None:
    conn = connect(caminho)
    try:
        with conn:
            conn.executescript(TABELAS)
        cadastrar_salas_iniciais(conn)
        carregar_dados_reais(conn, arquivo_dados)
    finally:
        conn.close()

def cadastrar_salas_iniciais(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA user_version").fetchone()[0] != 0:
        return
    with conn:
        for predio, sala, descricao in SALAS_INICIAIS:
            conn.execute(
                "INSERT OR IGNORE INTO salas_cadastradas (predio, sala, descricao, criada_em) VALUES (?, ?, ?, ?)",
                (predio, sala, descricao, iso(utcnow())),)
        conn.execute("PRAGMA user_version = 1")

def carregar_dados_reais(conn: sqlite3.Connection, arquivo: str) -> None:
    dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
    with conn:
        for ano, meses in dados["consumo_mensal_kwh"].items():
            for mes, kwh in meses.items():
                conn.execute(
                    "INSERT OR IGNORE INTO consumo_mensal (ano, mes, kwh) VALUES (?, ?, ?)",
                    (int(ano), int(mes), kwh),)
        for ano, total in dados["total_oficial_kwh"].items():
            conn.execute(
                "INSERT OR IGNORE INTO totais_anuais (ano, consumo_kwh_oficial) VALUES (?, ?)",
                (int(ano), total),)
        for g in dados["geracao_anual_kwh"]:
            conn.execute(
                "INSERT OR IGNORE INTO geracao_anual (ano, fonte, kwh, tipo) VALUES (?, ?, ?, ?)",
                (g["ano"], g["fonte"], g["kwh"], g["tipo"]),)