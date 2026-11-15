"""Relatório mensal em PDF do OffBank.

Recebe os números já prontos (centavos e datas AAAA-MM-DD) e devolve os bytes do PDF.
Não conhece o banco de dados: quem monta os dados é o app.py.
"""

from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

AZUL = colors.HexColor("#4338ca")
AZUL_CLARO = colors.HexColor("#eef0ff")
CINZA_TEXTO = "#475569"
CINZA_CLARO = colors.HexColor("#f8fafc")
LINHA = colors.HexColor("#e2e8f0")
VERDE = "#15803d"
VERMELHO = "#b91c1c"
AMBAR = "#b45309"

ESTILO_TITULO = ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=AZUL)
ESTILO_SUBTITULO = ParagraphStyle("subtitulo", fontName="Helvetica", fontSize=10.5, leading=14,
                                  textColor=colors.HexColor(CINZA_TEXTO))
ESTILO_SECAO = ParagraphStyle("secao", fontName="Helvetica-Bold", fontSize=12.5, leading=16,
                              textColor=colors.HexColor("#0f172a"), spaceBefore=16, spaceAfter=6)
ESTILO_CELULA = ParagraphStyle("celula", fontName="Helvetica", fontSize=8.8, leading=11)
ESTILO_CELULA_DIR = ParagraphStyle("celula_dir", parent=ESTILO_CELULA, alignment=2)
ESTILO_CABECALHO = ParagraphStyle("cabecalho", parent=ESTILO_CELULA, fontName="Helvetica-Bold",
                                  textColor=colors.white)
ESTILO_CABECALHO_DIR = ParagraphStyle("cabecalho_dir", parent=ESTILO_CABECALHO, alignment=2)
ESTILO_VAZIO = ParagraphStyle("vazio", fontName="Helvetica-Oblique", fontSize=9.5, leading=13,
                              textColor=colors.HexColor("#64748b"))
ESTILO_RESUMO_ROTULO = ParagraphStyle("resumo_rotulo", fontName="Helvetica", fontSize=8.5, leading=11,
                                      textColor=colors.HexColor(CINZA_TEXTO), alignment=1)
ESTILO_RESUMO_VALOR = ParagraphStyle("resumo_valor", fontName="Helvetica-Bold", fontSize=12.5, leading=16,
                                     alignment=1)


# ==========================================
# FORMATAÇÃO
# ==========================================

def moeda(centavos):
    negativo = centavos < 0
    reais, resto = divmod(abs(int(centavos)), 100)
    texto = f"{reais:,}".replace(",", ".")
    return f"{'-' if negativo else ''}R$ {texto},{resto:02d}"


def data_br(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return iso or ""


def percentual(valor):
    return f"{valor:.1f}".replace(".", ",") + "%"


def _cor_do_valor(centavos):
    return VERDE if centavos >= 0 else VERMELHO


# ==========================================
# BLOCOS DO DOCUMENTO
# ==========================================

def _celula(conteudo, estilo):
    """Aceita texto simples ou (texto, cor_hex)."""
    if isinstance(conteudo, tuple):
        texto, cor = conteudo
        return Paragraph(f'<font color="{cor}">{escape(str(texto))}</font>', estilo)

    return Paragraph(escape(str(conteudo)), estilo)


def _tabela(cabecalho, linhas, larguras_cm, colunas_a_direita=()):
    dados = [[
        Paragraph(escape(titulo), ESTILO_CABECALHO_DIR if i in colunas_a_direita else ESTILO_CABECALHO)
        for i, titulo in enumerate(cabecalho)
    ]]

    for linha in linhas:
        dados.append([
            _celula(valor, ESTILO_CELULA_DIR if i in colunas_a_direita else ESTILO_CELULA)
            for i, valor in enumerate(linha)
        ])

    tabela = Table(dados, colWidths=[largura * cm for largura in larguras_cm], repeatRows=1)
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CINZA_CLARO]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINHA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tabela


def _resumo(entradas, saidas, saldo_mes, saldo_acumulado):
    valores = [entradas, -saidas, saldo_mes, saldo_acumulado]
    rotulos = ["Entradas", "Saídas", "Saldo do mês", "Saldo acumulado"]
    cores = [VERDE, VERMELHO, _cor_do_valor(saldo_mes), _cor_do_valor(saldo_acumulado)]

    linha_rotulos = [Paragraph(rotulo, ESTILO_RESUMO_ROTULO) for rotulo in rotulos]
    linha_valores = [
        Paragraph(f'<font color="{cor}">{escape(moeda(abs(valor) if i == 1 else valor))}</font>',
                  ESTILO_RESUMO_VALOR)
        for i, (valor, cor) in enumerate(zip(valores, cores))
    ]

    tabela = Table([linha_rotulos, linha_valores], colWidths=[4.25 * cm] * 4)
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AZUL_CLARO),
        ("LINEAFTER", (0, 0), (-2, -1), 0.8, colors.white),
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 1),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
    ]))
    return tabela


def _secao(historia, titulo, tabela_ou_none, texto_vazio):
    historia.append(Paragraph(escape(titulo), ESTILO_SECAO))

    if tabela_ou_none is None:
        historia.append(Paragraph(escape(texto_vazio), ESTILO_VAZIO))
    else:
        historia.append(tabela_ou_none)


def _por_categoria(itens, total):
    if not itens:
        return None

    linhas = [
        (item["nome"], item["total"], percentual(item["total"] * 100 / total if total else 0))
        for item in itens
    ]
    linhas = [(nome, moeda(valor), pct) for nome, valor, pct in linhas]
    return _tabela(["Categoria", "Valor", "% do total"], linhas, [9.0, 4.0, 4.0], colunas_a_direita=(1, 2))


def _orcamento(itens):
    if not itens:
        return None

    linhas = []

    for item in itens:
        if item["gasto"] > item["limite"]:
            situacao = ("Estourado", VERMELHO)
        elif item["percentual"] >= 80:
            situacao = ("Atenção", AMBAR)
        else:
            situacao = ("Dentro do limite", VERDE)

        linhas.append((
            item["nome"], moeda(item["gasto"]), moeda(item["limite"]),
            f"{item['percentual']}%", situacao,
        ))

    return _tabela(["Categoria", "Gasto", "Limite", "Uso", "Situação"], linhas,
                   [5.2, 3.0, 3.0, 2.0, 3.8], colunas_a_direita=(1, 2, 3))


def _contas(itens):
    if not itens:
        return None

    linhas = []

    for item in itens:
        cor = {"paga": VERDE, "atrasada": VERMELHO, "hoje": AMBAR}.get(item["situacao"], CINZA_TEXTO)
        linhas.append((
            data_br(item["vencimento"]), item["descricao"], moeda(item["valor"]), (item["rotulo"], cor),
        ))

    return _tabela(["Vencimento", "Conta", "Valor", "Situação"], linhas,
                   [2.6, 6.6, 3.0, 4.8], colunas_a_direita=(2,))


def _lancamentos(itens):
    if not itens:
        return None

    linhas = []

    for item in itens:
        entrada = item["tipo"] == "receita"
        cor = VERDE if entrada else VERMELHO
        sinal = "+" if entrada else "-"
        linhas.append((
            data_br(item["data"]), item["descricao"], item["categoria"],
            "Receita" if entrada else "Despesa",
            (f"{sinal}{moeda(item['valor'])}", cor),
        ))

    return _tabela(["Data", "Descrição", "Categoria", "Tipo", "Valor"], linhas,
                   [2.4, 5.6, 4.2, 2.0, 2.8], colunas_a_direita=(4,))


# ==========================================
# PDF
# ==========================================

def gerar_pdf(dados):
    """Gera o relatório do mês e devolve os bytes do PDF.

    Campos de 'dados': nome, mes, ano, gerado_em (datetime), entradas, saidas, saldo_anterior,
    despesas_por_categoria, receitas_por_categoria ([{nome, total}]), orcamento
    ([{nome, gasto, limite, percentual}]), contas ([{vencimento, descricao, valor, situacao, rotulo}])
    e lancamentos ([{data, descricao, categoria, tipo, valor}]).
    """
    mes_nome = MESES[dados["mes"] - 1]
    saldo_mes = dados["entradas"] - dados["saidas"]
    gerado = dados["gerado_em"].strftime("%d/%m/%Y às %H:%M")

    def rodape(canvas, documento):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(2 * cm, 1.1 * cm, f"OffBank · gerado em {gerado}")
        canvas.drawRightString(A4[0] - 2 * cm, 1.1 * cm, f"Página {documento.page}")
        canvas.restoreState()

    memoria = BytesIO()
    documento = SimpleDocTemplate(
        memoria, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.9 * cm,
        title=f"Relatório de {mes_nome} de {dados['ano']}", author="OffBank",
    )

    historia = [
        Paragraph("Relatório mensal", ESTILO_TITULO),
        Paragraph(escape(f"{mes_nome} de {dados['ano']} · {dados['nome']}"), ESTILO_SUBTITULO),
        Spacer(1, 12),
        _resumo(dados["entradas"], dados["saidas"], saldo_mes, dados["saldo_anterior"] + saldo_mes),
    ]

    _secao(historia, "Despesas por categoria",
           _por_categoria(dados["despesas_por_categoria"], dados["saidas"]),
           "Nenhuma despesa neste mês.")

    if dados["orcamento"]:
        _secao(historia, "Orçamento do mês", _orcamento(dados["orcamento"]), "")

    _secao(historia, "Receitas por categoria",
           _por_categoria(dados["receitas_por_categoria"], dados["entradas"]),
           "Nenhuma receita neste mês.")

    _secao(historia, "Contas do mês", _contas(dados["contas"]),
           "Nenhuma conta com vencimento neste mês.")

    _secao(historia, "Lançamentos do mês", _lancamentos(dados["lancamentos"]),
           "Nenhum lançamento neste mês.")

    documento.build(historia, onFirstPage=rodape, onLaterPages=rodape)
    return memoria.getvalue()
