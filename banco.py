"""Banco de dados do Meu Bolso (SQLite).

Todos os valores em dinheiro são guardados em CENTAVOS (número inteiro),
para evitar erros de arredondamento. As datas são guardadas como AAAA-MM-DD.
"""

import os
import sqlite3

NOME_BANCO = os.getenv("MEUBOLSO_DB", "meubolso.db")

CATEGORIAS_PADRAO = [
    ("receita", "Salário", "💼"),
    ("receita", "Renda extra", "💸"),
    ("receita", "Outras receitas", "➕"),
    ("despesa", "Mercado", "🛒"),
    ("despesa", "Moradia", "🏠"),
    ("despesa", "Contas (água, luz, internet)", "💡"),
    ("despesa", "Transporte", "🚌"),
    ("despesa", "Alimentação fora", "🍔"),
    ("despesa", "Saúde", "💊"),
    ("despesa", "Educação", "📚"),
    ("despesa", "Lazer", "🎉"),
    ("despesa", "Outras despesas", "📦"),
]

ICONE_PADRAO = {"receita": "💰", "despesa": "🏷️"}


def conectar():
    conexao = sqlite3.connect(NOME_BANCO)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


# ==========================================
# CRIAR / ATUALIZAR O BANCO
# ==========================================

def criar_banco():
    conexao = conectar()

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuario (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            nome TEXT NOT NULL,
            senha TEXT NOT NULL
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL CHECK (tipo IN ('receita', 'despesa')),
            nome TEXT NOT NULL,
            icone TEXT NOT NULL DEFAULT '',
            ativa INTEGER NOT NULL DEFAULT 1,
            UNIQUE (tipo, nome)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS lancamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL CHECK (tipo IN ('receita', 'despesa')),
            valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
            descricao TEXT NOT NULL,
            categoria_id INTEGER NOT NULL,
            data TEXT NOT NULL,
            FOREIGN KEY (categoria_id) REFERENCES categorias (id)
        )
    """)

    conexao.execute(
        "CREATE INDEX IF NOT EXISTS idx_lancamentos_data ON lancamentos (data)"
    )

    # Categorias iniciais: só na primeira vez (banco novo, sem nenhuma categoria).
    total = conexao.execute("SELECT COUNT(*) FROM categorias").fetchone()[0]

    if total == 0:
        conexao.executemany(
            "INSERT INTO categorias (tipo, nome, icone) VALUES (?, ?, ?)",
            CATEGORIAS_PADRAO,
        )

    conexao.commit()
    conexao.close()


# ==========================================
# USUÁRIO (um só por aparelho)
# ==========================================

def usuario_existe():
    conexao = conectar()
    existe = conexao.execute("SELECT 1 FROM usuario WHERE id = 1").fetchone()
    conexao.close()
    return existe is not None


def criar_usuario(nome, senha_hash):
    conexao = conectar()
    conexao.execute(
        "INSERT INTO usuario (id, nome, senha) VALUES (1, ?, ?)",
        (nome, senha_hash),
    )
    conexao.commit()
    conexao.close()


def obter_usuario():
    conexao = conectar()
    usuario = conexao.execute("SELECT nome, senha FROM usuario WHERE id = 1").fetchone()
    conexao.close()
    return usuario


def alterar_senha(senha_hash):
    conexao = conectar()
    conexao.execute("UPDATE usuario SET senha = ? WHERE id = 1", (senha_hash,))
    conexao.commit()
    conexao.close()


# ==========================================
# CATEGORIAS
# ==========================================

def listar_categorias(tipo=None, so_ativas=True):
    consulta = "SELECT id, tipo, nome, icone, ativa FROM categorias WHERE 1 = 1"
    parametros = []

    if tipo:
        consulta += " AND tipo = ?"
        parametros.append(tipo)

    if so_ativas:
        consulta += " AND ativa = 1"

    consulta += " ORDER BY ativa DESC, nome COLLATE NOCASE"

    conexao = conectar()
    categorias = conexao.execute(consulta, parametros).fetchall()
    conexao.close()
    return categorias


def buscar_categoria(id_categoria):
    conexao = conectar()
    categoria = conexao.execute(
        "SELECT id, tipo, nome, icone, ativa FROM categorias WHERE id = ?",
        (id_categoria,),
    ).fetchone()
    conexao.close()
    return categoria


def adicionar_categoria(tipo, nome, icone):
    """Levanta sqlite3.IntegrityError se já existir uma igual."""
    conexao = conectar()
    try:
        conexao.execute(
            "INSERT INTO categorias (tipo, nome, icone) VALUES (?, ?, ?)",
            (tipo, nome, icone),
        )
        conexao.commit()
    finally:
        conexao.close()


def editar_categoria(id_categoria, nome, icone):
    """Levanta sqlite3.IntegrityError se já existir outra com o mesmo nome."""
    conexao = conectar()
    try:
        conexao.execute(
            "UPDATE categorias SET nome = ?, icone = ? WHERE id = ?",
            (nome, icone, id_categoria),
        )
        conexao.commit()
    finally:
        conexao.close()


def alternar_categoria(id_categoria):
    conexao = conectar()
    conexao.execute(
        "UPDATE categorias SET ativa = 1 - ativa WHERE id = ?",
        (id_categoria,),
    )
    conexao.commit()
    conexao.close()


# ==========================================
# LANÇAMENTOS
# ==========================================

_COLUNAS_LANCAMENTO = """
    l.id, l.tipo, l.valor_centavos, l.descricao, l.data, l.categoria_id,
    c.nome AS categoria_nome, c.icone AS categoria_icone
"""


def listar_lancamentos(data_inicio, data_fim, tipo=None, limite=None):
    """Lançamentos de [data_inicio, data_fim), do mais recente para o mais antigo."""
    consulta = f"""
        SELECT {_COLUNAS_LANCAMENTO}
        FROM lancamentos l
        JOIN categorias c ON c.id = l.categoria_id
        WHERE l.data >= ? AND l.data < ?
    """
    parametros = [data_inicio, data_fim]

    if tipo:
        consulta += " AND l.tipo = ?"
        parametros.append(tipo)

    consulta += " ORDER BY l.data DESC, l.id DESC"

    if limite:
        consulta += " LIMIT ?"
        parametros.append(limite)

    conexao = conectar()
    lancamentos = conexao.execute(consulta, parametros).fetchall()
    conexao.close()
    return lancamentos


def buscar_lancamento(id_lancamento):
    conexao = conectar()
    lancamento = conexao.execute(
        f"""
        SELECT {_COLUNAS_LANCAMENTO}
        FROM lancamentos l
        JOIN categorias c ON c.id = l.categoria_id
        WHERE l.id = ?
        """,
        (id_lancamento,),
    ).fetchone()
    conexao.close()
    return lancamento


def adicionar_lancamento(tipo, valor_centavos, descricao, categoria_id, data):
    conexao = conectar()
    conexao.execute(
        """
        INSERT INTO lancamentos (tipo, valor_centavos, descricao, categoria_id, data)
        VALUES (?, ?, ?, ?, ?)
        """,
        (tipo, valor_centavos, descricao, categoria_id, data),
    )
    conexao.commit()
    conexao.close()


def editar_lancamento(id_lancamento, valor_centavos, descricao, categoria_id, data):
    conexao = conectar()
    conexao.execute(
        """
        UPDATE lancamentos
        SET valor_centavos = ?, descricao = ?, categoria_id = ?, data = ?
        WHERE id = ?
        """,
        (valor_centavos, descricao, categoria_id, data, id_lancamento),
    )
    conexao.commit()
    conexao.close()


def excluir_lancamento(id_lancamento):
    conexao = conectar()
    conexao.execute("DELETE FROM lancamentos WHERE id = ?", (id_lancamento,))
    conexao.commit()
    conexao.close()


# ==========================================
# RESUMOS
# ==========================================

def totais_do_periodo(data_inicio, data_fim):
    """Retorna (entradas, saidas) em centavos para [data_inicio, data_fim)."""
    conexao = conectar()
    linhas = conexao.execute(
        """
        SELECT tipo, COALESCE(SUM(valor_centavos), 0) AS total
        FROM lancamentos
        WHERE data >= ? AND data < ?
        GROUP BY tipo
        """,
        (data_inicio, data_fim),
    ).fetchall()
    conexao.close()

    totais = {linha["tipo"]: linha["total"] for linha in linhas}
    return totais.get("receita", 0), totais.get("despesa", 0)


def saldo_ate(data_limite):
    """Saldo acumulado de tudo que aconteceu ANTES de data_limite (em centavos)."""
    conexao = conectar()
    linha = conexao.execute(
        """
        SELECT COALESCE(SUM(
            CASE WHEN tipo = 'receita' THEN valor_centavos ELSE -valor_centavos END
        ), 0) AS saldo
        FROM lancamentos
        WHERE data < ?
        """,
        (data_limite,),
    ).fetchone()
    conexao.close()
    return linha["saldo"]


def gastos_por_categoria(data_inicio, data_fim):
    conexao = conectar()
    gastos = conexao.execute(
        """
        SELECT c.nome, c.icone, SUM(l.valor_centavos) AS total
        FROM lancamentos l
        JOIN categorias c ON c.id = l.categoria_id
        WHERE l.tipo = 'despesa' AND l.data >= ? AND l.data < ?
        GROUP BY c.id
        ORDER BY total DESC
        """,
        (data_inicio, data_fim),
    ).fetchall()
    conexao.close()
    return gastos
