"""Inicializador do OffBank no Android.

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


def _migrar_banco_antigo(pasta_dados):
    """Se o banco ainda tem o nome antigo (meubolso.db), passa para offbank.db sem perder dados."""
    antigo = os.path.join(pasta_dados, "meubolso.db")
    novo = os.path.join(pasta_dados, "offbank.db")
    if os.path.exists(antigo) and not os.path.exists(novo):
        for sufixo in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(antigo + sufixo):
                os.replace(antigo + sufixo, novo + sufixo)


def iniciar(pasta_dados, pasta_templates, porta, token_rapido=""):
    global _servidor

    if _servidor is not None:
        return

    os.makedirs(pasta_dados, exist_ok=True)

    # Tudo que o sistema grava com caminho relativo
    # (arquivos auxiliares) vai para a pasta de dados do app.
    os.chdir(pasta_dados)

    _migrar_banco_antigo(pasta_dados)
    os.environ["OFFBANK_DB"] = os.path.join(pasta_dados, "offbank.db")
    os.environ["OFFBANK_BACKUPS"] = os.path.join(pasta_dados, "backups")
    os.environ["OFFBANK_TEMPLATES"] = pasta_templates
    os.environ["OFFBANK_SECRET_KEY"] = _chave_secreta(pasta_dados)

    # Código secreto desta abertura do app: só o Android conhece. Libera a "entrada rápida"
    # (bloqueio do celular) sem que outros aplicativos consigam usá-la.
    os.environ["OFFBANK_TOKEN_RAPIDO"] = token_rapido

    # Importar o app já cria o banco, se ainda não existir.
    import app as offbank

    from werkzeug.serving import make_server

    servidor = make_server(
        "127.0.0.1",
        int(porta),
        offbank.app,
        threaded=True,
    )

    threading.Thread(
        target=servidor.serve_forever,
        name="servidor-offbank",
        daemon=True,
    ).start()

    _servidor = servidor
