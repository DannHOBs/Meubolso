"""Cópias de segurança (backup) do Meu Bolso.

Os backups novos são arquivos .mbk, protegidos por senha (veja seguranca.py).
Ainda é possível restaurar arquivos .db antigos (sem proteção), mas o app não cria mais esse tipo.
"""

import os
import re
import sqlite3
import tempfile
from datetime import datetime

import banco
import seguranca

PASTA_BACKUPS = os.getenv("MEUBOLSO_BACKUPS", "backups")

PADRAO_NOME = re.compile(r"^backup_[\w\-]+\.(mbk|db)$")

TABELAS_OBRIGATORIAS = {"usuario", "categorias", "lancamentos"}

ASSINATURA_SQLITE = b"SQLite format 3\x00"


def _pasta():
    os.makedirs(PASTA_BACKUPS, exist_ok=True)
    return PASTA_BACKUPS


def nome_valido(nome):
    return bool(PADRAO_NOME.match(nome or ""))


def _copiar_banco_para(caminho_destino):
    """Cópia consistente do banco (usa a API de backup do SQLite, segura com o app aberto)."""
    origem = sqlite3.connect(banco.NOME_BANCO)
    destino = sqlite3.connect(caminho_destino)

    try:
        origem.backup(destino)
    finally:
        destino.close()
        origem.close()


def criar_backup(senha, prefixo="backup"):
    """Cria um backup protegido pela senha e devolve o nome do arquivo."""
    agora = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nome = f"{prefixo}_{agora}.mbk"
    caminho = os.path.join(_pasta(), nome)

    descritor, temporario = tempfile.mkstemp(suffix=".db")
    os.close(descritor)

    try:
        _copiar_banco_para(temporario)

        with open(temporario, "rb") as arquivo:
            dados = arquivo.read()
    finally:
        os.remove(temporario)

    with open(caminho + ".parcial", "wb") as arquivo:
        arquivo.write(seguranca.criptografar(dados, senha))

    os.replace(caminho + ".parcial", caminho)
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
            "protegido": nome.endswith(".mbk"),
            "tamanho_kb": round(os.path.getsize(caminho) / 1024, 1),
            "data": datetime.fromtimestamp(os.path.getmtime(caminho)).strftime("%d/%m/%Y %H:%M"),
        })

    return itens


def excluir_backup(nome):
    """Exclui um backup guardado no aparelho. Devolve True se existia."""
    if not nome_valido(nome):
        return False

    caminho = os.path.join(_pasta(), nome)

    if not os.path.isfile(caminho):
        return False

    os.remove(caminho)
    return True


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


def restaurar_de_arquivo(caminho_banco, senha_da_copia_de_seguranca):
    """Substitui os dados atuais pelos do banco indicado (arquivo SQLite já aberto/descriptografado).

    Antes, guarda uma cópia protegida dos dados atuais. Devolve (ok, mensagem_ou_nome_da_copia).
    """
    ok, mensagem = validar_arquivo(caminho_banco)

    if not ok:
        return False, mensagem

    nome_seguranca = criar_backup(senha_da_copia_de_seguranca, "backup_antes-de-restaurar")

    origem = sqlite3.connect(caminho_banco)
    destino = sqlite3.connect(banco.NOME_BANCO)

    try:
        origem.backup(destino)
    finally:
        destino.close()
        origem.close()

    # Garante que um backup antigo ganhe tabelas/colunas criadas em versões novas.
    banco.criar_banco()

    return True, nome_seguranca


def restaurar_de_upload(caminho_upload, senha_do_backup, senha_atual):
    """Restaura a partir do arquivo enviado: .mbk (protegido) ou .db antigo.

    senha_do_backup: abre o arquivo .mbk.
    senha_atual: protege a cópia de segurança dos dados de hoje.
    """
    with open(caminho_upload, "rb") as arquivo:
        conteudo = arquivo.read()

    if seguranca.eh_arquivo_protegido(conteudo):
        if not senha_do_backup:
            return False, "Digite a senha do backup."

        try:
            dados = seguranca.descriptografar(conteudo, senha_do_backup)
        except seguranca.SenhaOuArquivoInvalido as erro:
            return False, str(erro)

        descritor, temporario = tempfile.mkstemp(suffix=".db")
        os.close(descritor)

        try:
            with open(temporario, "wb") as arquivo:
                arquivo.write(dados)

            return restaurar_de_arquivo(temporario, senha_atual)
        finally:
            os.remove(temporario)

    if conteudo.startswith(ASSINATURA_SQLITE):
        return restaurar_de_arquivo(caminho_upload, senha_atual)

    return False, "Esse arquivo não é um backup do Meu Bolso."
