"""Banco de dados do Meu Bolso (SQLite).

Todos os valores em dinheiro são guardados em CENTAVOS (número inteiro),
para evitar erros de arredondamento. As datas são guardadas como AAAA-MM-DD.
"""

import calendar
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

    # Versão 2: contas a pagar e recorrências (aluguel, internet, assinaturas...).
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS recorrencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT NOT NULL,
            valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
            categoria_id INTEGER NOT NULL,
            dia INTEGER NOT NULL CHECK (dia BETWEEN 1 AND 31),
            ativa INTEGER NOT NULL DEFAULT 1,
            ultimo_gerado TEXT NOT NULL,
            FOREIGN KEY (categoria_id) REFERENCES categorias (id)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS contas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT NOT NULL,
            valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
            categoria_id INTEGER NOT NULL,
            vencimento TEXT NOT NULL,
            paga INTEGER NOT NULL DEFAULT 0,
            lancamento_id INTEGER,
            recorrencia_id INTEGER,
            FOREIGN KEY (categoria_id) REFERENCES categorias (id),
            FOREIGN KEY (lancamento_id) REFERENCES lancamentos (id) ON DELETE SET NULL,
            FOREIGN KEY (recorrencia_id) REFERENCES recorrencias (id) ON DELETE SET NULL
        )
    """)

    conexao.execute(
        "CREATE INDEX IF NOT EXISTS idx_contas_vencimento ON contas (vencimento)"
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
    """Exclui o lançamento. Devolve True se ele era o pagamento de uma conta (que reabre)."""
    conexao = conectar()

    reaberta = conexao.execute(
        "UPDATE contas SET paga = 0, lancamento_id = NULL WHERE lancamento_id = ?",
        (id_lancamento,),
    ).rowcount > 0

    conexao.execute("DELETE FROM lancamentos WHERE id = ?", (id_lancamento,))
    conexao.commit()
    conexao.close()

    return reaberta


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


# ==========================================
# DATAS (apoio às contas e recorrências)
# ==========================================

def data_do_mes(ano, mes, dia):
    """AAAA-MM-DD; se o mês é curto (ex.: dia 31 em fevereiro), usa o último dia."""
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return f"{ano:04d}-{mes:02d}-{min(dia, ultimo_dia):02d}"


def proximo_mes(ano, mes):
    return (ano + 1, 1) if mes == 12 else (ano, mes + 1)


# ==========================================
# CONTAS A PAGAR
# ==========================================

_COLUNAS_CONTA = """
    c.id, c.descricao, c.valor_centavos, c.categoria_id, c.vencimento,
    c.paga, c.lancamento_id, c.recorrencia_id,
    cat.nome AS categoria_nome, cat.icone AS categoria_icone,
    l.data AS data_pagamento,
    COALESCE(l.valor_centavos, c.valor_centavos) AS valor_exibido
"""

_JUNCOES_CONTA = """
    FROM contas c
    JOIN categorias cat ON cat.id = c.categoria_id
    LEFT JOIN lancamentos l ON l.id = c.lancamento_id
"""


def listar_contas_do_periodo(data_inicio, data_fim):
    """Contas com vencimento em [data_inicio, data_fim): em aberto primeiro, depois as pagas."""
    conexao = conectar()
    contas = conexao.execute(
        f"""
        SELECT {_COLUNAS_CONTA} {_JUNCOES_CONTA}
        WHERE c.vencimento >= ? AND c.vencimento < ?
        ORDER BY c.paga, c.vencimento, c.id
        """,
        (data_inicio, data_fim),
    ).fetchall()
    conexao.close()
    return contas


def listar_contas_em_aberto_ate(data_limite, limite=None):
    """Contas ainda não pagas com vencimento até data_limite (inclui as atrasadas)."""
    consulta = f"""
        SELECT {_COLUNAS_CONTA} {_JUNCOES_CONTA}
        WHERE c.paga = 0 AND c.vencimento <= ?
        ORDER BY c.vencimento, c.id
    """
    parametros = [data_limite]

    if limite:
        consulta += " LIMIT ?"
        parametros.append(limite)

    conexao = conectar()
    contas = conexao.execute(consulta, parametros).fetchall()
    conexao.close()
    return contas


def resumo_atrasadas(data_hoje):
    """(quantidade, total em centavos) das contas em aberto vencidas antes de data_hoje."""
    conexao = conectar()
    linha = conexao.execute(
        """
        SELECT COUNT(*) AS quantas, COALESCE(SUM(valor_centavos), 0) AS total
        FROM contas
        WHERE paga = 0 AND vencimento < ?
        """,
        (data_hoje,),
    ).fetchone()
    conexao.close()
    return linha["quantas"], linha["total"]


def totais_das_contas(data_inicio, data_fim):
    """(a_pagar, ja_pago) em centavos para as contas com vencimento no período."""
    conexao = conectar()
    linha = conexao.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN c.paga = 0 THEN c.valor_centavos END), 0) AS a_pagar,
            COALESCE(SUM(CASE WHEN c.paga = 1
                THEN COALESCE(l.valor_centavos, c.valor_centavos) END), 0) AS ja_pago
        FROM contas c
        LEFT JOIN lancamentos l ON l.id = c.lancamento_id
        WHERE c.vencimento >= ? AND c.vencimento < ?
        """,
        (data_inicio, data_fim),
    ).fetchone()
    conexao.close()
    return linha["a_pagar"], linha["ja_pago"]


def buscar_conta(id_conta):
    conexao = conectar()
    conta = conexao.execute(
        f"SELECT {_COLUNAS_CONTA} {_JUNCOES_CONTA} WHERE c.id = ?",
        (id_conta,),
    ).fetchone()
    conexao.close()
    return conta


def adicionar_conta(descricao, valor_centavos, categoria_id, vencimento, recorrencia_id=None):
    conexao = conectar()
    cursor = conexao.execute(
        """
        INSERT INTO contas (descricao, valor_centavos, categoria_id, vencimento, recorrencia_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (descricao, valor_centavos, categoria_id, vencimento, recorrencia_id),
    )
    conexao.commit()
    id_conta = cursor.lastrowid
    conexao.close()
    return id_conta


def editar_conta(id_conta, descricao, valor_centavos, categoria_id, vencimento):
    conexao = conectar()
    conexao.execute(
        """
        UPDATE contas
        SET descricao = ?, valor_centavos = ?, categoria_id = ?, vencimento = ?
        WHERE id = ? AND paga = 0
        """,
        (descricao, valor_centavos, categoria_id, vencimento, id_conta),
    )
    conexao.commit()
    conexao.close()


def excluir_conta(id_conta):
    """Só exclui contas em aberto. Devolve True se excluiu."""
    conexao = conectar()
    apagou = conexao.execute(
        "DELETE FROM contas WHERE id = ? AND paga = 0", (id_conta,)
    ).rowcount > 0
    conexao.commit()
    conexao.close()
    return apagou


def pagar_conta(id_conta, data_pagamento):
    """Registra a despesa nos lançamentos e marca a conta como paga. Devolve True se pagou."""
    conexao = conectar()

    try:
        conta = conexao.execute(
            "SELECT descricao, valor_centavos, categoria_id FROM contas WHERE id = ? AND paga = 0",
            (id_conta,),
        ).fetchone()

        if conta is None:
            return False

        cursor = conexao.execute(
            """
            INSERT INTO lancamentos (tipo, valor_centavos, descricao, categoria_id, data)
            VALUES ('despesa', ?, ?, ?, ?)
            """,
            (conta["valor_centavos"], conta["descricao"], conta["categoria_id"], data_pagamento),
        )

        conexao.execute(
            "UPDATE contas SET paga = 1, lancamento_id = ? WHERE id = ?",
            (cursor.lastrowid, id_conta),
        )

        conexao.commit()
        return True
    finally:
        conexao.close()


def desfazer_pagamento(id_conta):
    """Apaga o lançamento do pagamento e reabre a conta. Devolve True se desfez."""
    conexao = conectar()

    try:
        conta = conexao.execute(
            "SELECT lancamento_id FROM contas WHERE id = ? AND paga = 1", (id_conta,)
        ).fetchone()

        if conta is None:
            return False

        conexao.execute(
            "UPDATE contas SET paga = 0, lancamento_id = NULL WHERE id = ?", (id_conta,)
        )

        if conta["lancamento_id"]:
            conexao.execute("DELETE FROM lancamentos WHERE id = ?", (conta["lancamento_id"],))

        conexao.commit()
        return True
    finally:
        conexao.close()


# ==========================================
# RECORRÊNCIAS (contas que se repetem todo mês)
# ==========================================

def listar_recorrencias():
    conexao = conectar()
    recorrencias = conexao.execute(
        """
        SELECT r.id, r.descricao, r.valor_centavos, r.categoria_id, r.dia, r.ativa,
               cat.nome AS categoria_nome, cat.icone AS categoria_icone
        FROM recorrencias r
        JOIN categorias cat ON cat.id = r.categoria_id
        ORDER BY r.ativa DESC, r.descricao COLLATE NOCASE
        """
    ).fetchall()
    conexao.close()
    return recorrencias


def buscar_recorrencia(id_recorrencia):
    conexao = conectar()
    recorrencia = conexao.execute(
        """
        SELECT r.id, r.descricao, r.valor_centavos, r.categoria_id, r.dia, r.ativa,
               cat.nome AS categoria_nome, cat.icone AS categoria_icone
        FROM recorrencias r
        JOIN categorias cat ON cat.id = r.categoria_id
        WHERE r.id = ?
        """,
        (id_recorrencia,),
    ).fetchone()
    conexao.close()
    return recorrencia


def adicionar_conta_recorrente(descricao, valor_centavos, categoria_id, vencimento):
    """Cria a conta (1º vencimento) e a recorrência que gera as próximas. Devolve o id da conta."""
    conexao = conectar()

    try:
        ano, mes, dia = int(vencimento[:4]), int(vencimento[5:7]), int(vencimento[8:10])

        cursor = conexao.execute(
            """
            INSERT INTO recorrencias (descricao, valor_centavos, categoria_id, dia, ultimo_gerado)
            VALUES (?, ?, ?, ?, ?)
            """,
            (descricao, valor_centavos, categoria_id, dia, f"{ano:04d}-{mes:02d}"),
        )

        cursor_conta = conexao.execute(
            """
            INSERT INTO contas (descricao, valor_centavos, categoria_id, vencimento, recorrencia_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (descricao, valor_centavos, categoria_id, vencimento, cursor.lastrowid),
        )

        conexao.commit()
        return cursor_conta.lastrowid
    finally:
        conexao.close()


def editar_recorrencia(id_recorrencia, descricao, valor_centavos, categoria_id, dia):
    """Atualiza a recorrência e as contas dela que ainda estão em aberto."""
    conexao = conectar()

    try:
        conexao.execute(
            """
            UPDATE recorrencias
            SET descricao = ?, valor_centavos = ?, categoria_id = ?, dia = ?
            WHERE id = ?
            """,
            (descricao, valor_centavos, categoria_id, dia, id_recorrencia),
        )

        em_aberto = conexao.execute(
            "SELECT id, vencimento FROM contas WHERE recorrencia_id = ? AND paga = 0",
            (id_recorrencia,),
        ).fetchall()

        for conta in em_aberto:
            ano, mes = int(conta["vencimento"][:4]), int(conta["vencimento"][5:7])

            conexao.execute(
                """
                UPDATE contas
                SET descricao = ?, valor_centavos = ?, categoria_id = ?, vencimento = ?
                WHERE id = ?
                """,
                (descricao, valor_centavos, categoria_id, data_do_mes(ano, mes, dia), conta["id"]),
            )

        conexao.commit()
    finally:
        conexao.close()


def alternar_recorrencia(id_recorrencia):
    conexao = conectar()
    conexao.execute(
        "UPDATE recorrencias SET ativa = 1 - ativa WHERE id = ?", (id_recorrencia,)
    )
    conexao.commit()
    conexao.close()


def excluir_recorrencia(id_recorrencia):
    """Remove a recorrência e as contas dela em aberto. As já pagas continuam no histórico."""
    conexao = conectar()
    conexao.execute(
        "DELETE FROM contas WHERE recorrencia_id = ? AND paga = 0", (id_recorrencia,)
    )
    conexao.execute("DELETE FROM recorrencias WHERE id = ?", (id_recorrencia,))
    conexao.commit()
    conexao.close()


def gerar_recorrentes(hoje, ate_ano, ate_mes):
    """Cria as contas dos próximos meses das recorrências ativas (sem repetir nem voltar ao passado).

    - Nunca cria contas de meses anteriores ao mês de 'hoje' (evita falsas "atrasadas").
    - Se você apagar uma conta gerada, ela não volta (cada mês é gerado uma única vez).
    """
    conexao = conectar()

    try:
        recorrencias = conexao.execute(
            "SELECT id, descricao, valor_centavos, categoria_id, dia, ultimo_gerado "
            "FROM recorrencias WHERE ativa = 1"
        ).fetchall()

        for r in recorrencias:
            ano, mes = proximo_mes(int(r["ultimo_gerado"][:4]), int(r["ultimo_gerado"][5:7]))

            if (ano, mes) < (hoje.year, hoje.month):
                ano, mes = hoje.year, hoje.month

            ultimo = r["ultimo_gerado"]

            while (ano, mes) <= (ate_ano, ate_mes):
                conexao.execute(
                    """
                    INSERT INTO contas (descricao, valor_centavos, categoria_id, vencimento, recorrencia_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (r["descricao"], r["valor_centavos"], r["categoria_id"],
                     data_do_mes(ano, mes, r["dia"]), r["id"]),
                )
                ultimo = f"{ano:04d}-{mes:02d}"
                ano, mes = proximo_mes(ano, mes)

            if ultimo != r["ultimo_gerado"]:
                conexao.execute(
                    "UPDATE recorrencias SET ultimo_gerado = ? WHERE id = ?", (ultimo, r["id"])
                )

        conexao.commit()
    finally:
        conexao.close()
