"""Inicializador do Meu Bolso no Android.

Chamado pelo MainActivity (Java) através do Chaquopy. Define onde ficam
os dados do app, importa o sistema Flask (app.py) e deixa o servidor
rodando em segundo plano, somente neste aparelho (127.0.0.1).
"""

import os
import secrets
import threading

_servidor = None


def _chave_secreta(pasta_dados):
    # A chave de sessão é criada uma vez e guardada na pasta de dados do app.
    arquivo = os.path.join(pasta_dados, "chave_secreta.txt")

    if os.path.exists(arquivo):
        with open(arquivo, "r", encoding="utf-8") as f:
            chave = f.read().strip()
        if chave:
            return chave

    chave = secrets.token_hex(32)

    with open(arquivo, "w", encoding="utf-8") as f:
        f.write(chave)

    try:
        os.chmod(arquivo, 0o600)
    except OSError:
        pass

    return chave


def iniciar(pasta_dados, pasta_templates, porta):
    global _servidor

    if _servidor is not None:
        return

    os.makedirs(pasta_dados, exist_ok=True)

    # Tudo que o sistema grava com caminho relativo
    # (arquivos auxiliares) vai para a pasta de dados do app.
    os.chdir(pasta_dados)

    os.environ["MEUBOLSO_DB"] = os.path.join(pasta_dados, "meubolso.db")
    os.environ["MEUBOLSO_BACKUPS"] = os.path.join(pasta_dados, "backups")
    os.environ["MEUBOLSO_TEMPLATES"] = pasta_templates
    os.environ["MEUBOLSO_SECRET_KEY"] = _chave_secreta(pasta_dados)

    # Importar o app já cria o banco, se ainda não existir.
    import app as meubolso

    from werkzeug.serving import make_server

    servidor = make_server(
        "127.0.0.1",
        int(porta),
        meubolso.app,
        threaded=True,
    )

    threading.Thread(
        target=servidor.serve_forever,
        name="servidor-meubolso",
        daemon=True,
    ).start()

    _servidor = servidor
