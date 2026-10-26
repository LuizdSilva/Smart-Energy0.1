"""Calculos sobre os dados reais da Facens (consumo mensal e geração de energia)."""
import sqlite3

MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
MESES_LONGOS = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def anos_disponiveis(conn: sqlite3.Connection) -> list[int]:
    linhas = conn.execute("SELECT DISTINCT ano FROM consumo_mensal ORDER BY ano DESC")
    return [linha[0] for linha in linhas]


def consumo_ano(conn: sqlite3.Connection, ano: int) -> dict | None:
    linhas = conn.execute("SELECT mes, kwh FROM consumo_mensal WHERE ano=? ORDER BY mes", (ano,)).fetchall()
    if not linhas:
        return None

    soma = sum(linha["kwh"] for linha in linhas)
    oficial = conn.execute("SELECT consumo_kwh_oficial FROM totais_anuais WHERE ano=?", (ano,)).fetchone()
    total = oficial[0] if oficial else soma
    media = total / len(linhas)

    meses = [
        {
            "nome": MESES[linha["mes"] - 1],
            "nome_longo": MESES_LONGOS[linha["mes"] - 1],
            "kwh": linha["kwh"],
            "desvio_pct": (linha["kwh"] - media) / media * 100,
        }
        for linha in linhas
    ]
    return {
        "ano": ano,
        "total": total,
        "soma_meses": soma,
        "diferenca_total_vs_soma": soma - total,  # 1 kWh em 2024, por arredondamento
        "media_mensal": media,
        "meses": meses,
        "maior": max(meses, key=lambda m: m["kwh"]),
        "menor": min(meses, key=lambda m: m["kwh"]),
    }


def geracao_ano(conn: sqlite3.Connection, ano: int, consumo_total: float | None) -> dict:
    fontes = {linha["fonte"]: dict(linha) for linha in conn.execute("SELECT * FROM geracao_anual WHERE ano=?", (ano,))}
    solar = fontes["solar"]["kwh"] if "solar" in fontes else None

    resultado = {"ano": ano, "fontes": fontes, "solar_kwh": solar, "solar_pct": None, "renovavel_pct_total": None}
    if consumo_total:
        if solar is not None:
            resultado["solar_pct"] = solar / consumo_total * 100
        if fontes:
            resultado["renovavel_pct_total"] = sum(f["kwh"] for f in fontes.values()) / consumo_total * 100
    return resultado
