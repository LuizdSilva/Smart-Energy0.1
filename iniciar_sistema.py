"""Liga o sistema: python iniciar_sistema.py  e abre http://127.0.0.1:5000"""
import logging
import os
from pathlib import Path


def carregar_env(caminho):
    """Lê o arquivo .env (linhas NOME=valor) e coloca os valores nas variáveis de ambiente."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        nome, valor = linha.split("=", 1)
        os.environ.setdefault(nome.strip(), valor.strip().strip('"').strip("'"))


# O .env precisa ser lido ANTES de importar o app, porque configuracoes.py usa essas variáveis.
carregar_env(Path(__file__).parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    # use_reloader=False evita abrir duas conexões MQTT ao mesmo tempo
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
