"""Cópias de segurança (backup) do Meu Bolso.

Usa a própria API de backup do SQLite, que é segura mesmo com o app aberto.
"""

import os
import re
import sqlite3
from datetime import datetime

import banco

PASTA_BACKUPS = os.getenv("MEUBOLSO_BACKUPS", "backups")

PADRAO_NOME = re.compile(r"^backup_[\w\-]+\.db$")

TABELAS_OBRIGATORIAS = {"usuario", "categorias", "lancamentos"}


def _pasta():
    os.makedirs(PASTA_BACKUPS, exist_ok=True)
    return PASTA_BACKUPS


def nome_valido(nome):
    return bool(PADRAO_NOME.match(nome or ""))


def criar_backup(prefixo="backup"):
    """Cria uma cópia do banco e devolve o nome do arquivo."""
    agora = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nome = f"{prefixo}_{agora}.db"
    caminho = os.path.join(_pasta(), nome)

    origem = sqlite3.connect(banco.NOME_BANCO)
    destino = sqlite3.connect(caminho)

    try:
        origem.backup(destino)
    finally:
        destino.close()
        origem.close()

    return nome


def listar_backups():
    pasta = _pasta()
    itens = []

    for nome in sorted(os.listdir(pasta), reverse=True):
        caminho = os.path.join(pasta, nome)

        if not (os.path.isfile(caminho) and nome_valido(nome)):
            continue

        itens.append({
            "nome": nome,
            "tamanho_kb": round(os.path.getsize(caminho) / 1024, 1),
            "data": datetime.fromtimestamp(os.path.getmtime(caminho)).strftime("%d/%m/%Y %H:%M"),
        })

    return itens


def validar_arquivo(caminho):
    """Confere se o arquivo é mesmo um banco do Meu Bolso. Devolve (ok, mensagem)."""
    try:
        conexao = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
    except sqlite3.Error:
        return False, "Esse arquivo não é um backup válido."

    try:
        tabelas = {
            linha[0]
            for linha in conexao.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

        if not TABELAS_OBRIGATORIAS.issubset(tabelas):
            return False, "Esse arquivo não é um backup do Meu Bolso."

        resultado = conexao.execute("PRAGMA integrity_check").fetchone()[0]

        if resultado != "ok":
            return False, "O arquivo de backup está danificado."

    except sqlite3.Error:
        return False, "Esse arquivo não é um backup válido."
    finally:
        conexao.close()

    return True, ""


def restaurar_de_arquivo(caminho):
    """Substitui os dados atuais pelos do arquivo. Antes, guarda uma cópia dos dados atuais."""
    ok, mensagem = validar_arquivo(caminho)

    if not ok:
        return False, mensagem

    nome_seguranca = criar_backup("backup_antes-de-restaurar")

    origem = sqlite3.connect(caminho)
    destino = sqlite3.connect(banco.NOME_BANCO)

    try:
        origem.backup(destino)
    finally:
        destino.close()
        origem.close()

    # Garante que um backup antigo ganhe tabelas/colunas criadas em versões novas.
    banco.criar_banco()

    return True, nome_seguranca
