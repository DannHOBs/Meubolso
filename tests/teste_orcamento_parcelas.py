"""Teste automático do orçamento por categoria e das compras parceladas (versão 3).

Uso (na raiz do projeto):  python tests/teste_orcamento_parcelas.py
Usa um banco temporário; não mexe nos seus dados.
"""

import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from datetime import date

pasta = tempfile.mkdtemp()
os.environ["MEUBOLSO_DB"] = os.path.join(pasta, "teste.db")
os.environ["MEUBOLSO_BACKUPS"] = os.path.join(pasta, "backups")
os.environ["MEUBOLSO_SECRET_KEY"] = "chave-de-teste"

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["MEUBOLSO_TEMPLATES"] = str(RAIZ / "templates")

import app as meubolso  # noqa: E402
import backup  # noqa: E402
import banco  # noqa: E402

falhas = []


def conferir(descricao, condicao):
    print(("  ok   " if condicao else "  FALHOU  ") + descricao)
    if not condicao:
        falhas.append(descricao)


def texto(resposta):
    return resposta.get_data(as_text=True)


def contas_no_banco(**filtros):
    conexao = banco.conectar()
    consulta = "SELECT * FROM contas WHERE 1 = 1"
    valores = []
    for coluna, valor in filtros.items():
        consulta += f" AND {coluna} = ?"
        valores.append(valor)
    linhas = conexao.execute(consulta + " ORDER BY parcela_numero, vencimento, id", valores).fetchall()
    conexao.close()
    return linhas


cliente = meubolso.app.test_client()
cliente.post("/primeiro-acesso", data={"nome": "Dani", "senha": "1234", "confirmar": "1234"})

hoje = date.today()
mes_ano = {"mes": hoje.month, "ano": hoje.year}
data_hoje = hoje.isoformat()
cat_mercado = next(c for c in banco.listar_categorias("despesa") if c["nome"] == "Mercado")
cat_lazer = next(c for c in banco.listar_categorias("despesa") if c["nome"] == "Lazer")
cat_moradia = next(c for c in banco.listar_categorias("despesa") if c["nome"] == "Moradia")
cat_salario = next(c for c in banco.listar_categorias("receita") if c["nome"] == "Salário")

print("Contas recorrentes: tela e aviso")
cliente.post("/contas/nova", data={"descricao": "Aluguel", "valor": "850", "categoria_id": cat_moradia["id"], "vencimento": data_hoje, "repete": "1"})
html = texto(cliente.get("/recorrentes"))
conferir("tela de recorrentes tem o botão 'Nova conta recorrente'", "Nova conta recorrente" in html)
conferir("o aviso não tem mais link no meio do texto", 'href="/contas/nova">' not in html.split("alerta")[-1] and "<strong>nova conta</strong>" not in html)
html = texto(cliente.get("/contas/nova", query_string={"repete": "1"}))
conferir("o botão abre a conta já com 'Repetir todo mês' marcado", "checked" in html and "Nova conta recorrente" in html)
html = texto(cliente.get("/contas/nova"))
conferir("conta comum abre sem a opção marcada", "checked" not in html)
conferir("aviso usa bloco (corrige o layout quebrado)", ".alerta {\n            display: block;" in html)

print("Compras parceladas: cálculos")
conferir("R$ 100,00 em 3 vezes = 33,34 + 33,33 + 33,33", banco.dividir_valor(10000, 3) == [3334, 3333, 3333])
conferir("a soma das parcelas é sempre o total", sum(banco.dividir_valor(99999, 7)) == 99999)
conferir("R$ 100,00 em 4 vezes = 25,00 cada", banco.dividir_valor(10000, 4) == [2500] * 4)
conferir("vencimentos mensais respeitam meses curtos",
         banco.gerar_vencimentos("2026-01-31", 4) == ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"])
conferir("vencimentos viram o ano", banco.gerar_vencimentos("2026-11-10", 3) == ["2026-11-10", "2026-12-10", "2027-01-10"])

print("Compras parceladas: tela")


def parcelar(descricao, valor, parcelas, tipo="parcela", **extra):
    dados = {
        "descricao": descricao, "valor": valor, "categoria_id": cat_lazer["id"],
        "vencimento": data_hoje, "parcelas": parcelas, "valor_tipo": tipo,
    }
    dados.update(extra)
    return cliente.post("/contas/nova", data=dados, follow_redirects=True)


r = parcelar("Celular", "1.200,00", "3", tipo="total")
parcelas = contas_no_banco(descricao="Celular")
conferir("cria 3 parcelas ligadas entre si", len(parcelas) == 3 and len({p["parcela_grupo"] for p in parcelas}) == 1)
conferir("total dividido em 3 de R$ 400,00", [p["valor_centavos"] for p in parcelas] == [40000, 40000, 40000])
conferir("numeração 1/3, 2/3 e 3/3", [(p["parcela_numero"], p["parcela_total"]) for p in parcelas] == [(1, 3), (2, 3), (3, 3)])
conferir("aviso de sucesso fala das 3 contas", "Compra parcelada: 3 contas criadas" in texto(r))
conferir("a lista mostra o nome com (1/3)", "Celular (1/3)" in texto(r))

parcelar("Sofá", "250,00", "2", tipo="parcela")
conferir("valor 'de cada parcela' mantém o valor", [p["valor_centavos"] for p in contas_no_banco(descricao="Sofá")] == [25000, 25000])

r = parcelar("Erro", "10,00", "61")
conferir("mais de 60 parcelas é recusado", "entre 1 e 60" in texto(r) and not contas_no_banco(descricao="Erro"))
r = parcelar("Erro", "10,00", "0")
conferir("zero parcelas é recusado", "entre 1 e 60" in texto(r))
r = parcelar("Erro", "10,00", "3", repete="1")
conferir("parcelar e repetir ao mesmo tempo é recusado", "só uma opção" in texto(r) and not contas_no_banco(descricao="Erro"))
r = parcelar("Erro", "0,02", "3", tipo="total")
conferir("total menor que o número de parcelas é recusado", "pequeno demais" in texto(r))

primeira = parcelas[0]
lancamentos_antes = len(banco.listar_lancamentos("2000-01-01", "2100-01-01"))
cliente.post(f"/contas/{primeira['id']}/pagar")
lancamento = banco.listar_lancamentos("2000-01-01", "2100-01-01")[0]
conferir("pagar uma parcela cria o lançamento com o nome (1/3)",
         len(banco.listar_lancamentos("2000-01-01", "2100-01-01")) == lancamentos_antes + 1
         and lancamento["descricao"] == "Celular (1/3)" and lancamento["valor_centavos"] == 40000)

html = texto(cliente.get(f"/contas/{parcelas[1]['id']}/editar"))
conferir("editar parcela avisa que é a 2 de 3 e oferece excluir todas", "parcela 2 de 3" in html and "Excluir todas as parcelas em aberto" in html)
r = cliente.post(f"/contas/{parcelas[1]['id']}/excluir-parcelas", follow_redirects=True)
restantes = contas_no_banco(descricao="Celular")
conferir("excluir parcelas apaga só as em aberto (a paga fica)", "2 parcela(s) em aberto excluída(s)" in texto(r) and len(restantes) == 1 and restantes[0]["paga"] == 1)
conferir("as parcelas do Sofá não foram afetadas", len(contas_no_banco(descricao="Sofá")) == 2)
r = cliente.post(f"/contas/{contas_no_banco(descricao='Aluguel')[0]['id']}/excluir-parcelas", follow_redirects=True)
conferir("conta que não é parcelada não é afetada", "não é uma compra parcelada" in texto(r) and contas_no_banco(descricao="Aluguel"))

print("Orçamento")
html = texto(cliente.get("/orcamento", query_string=mes_ano))
conferir("tela do orçamento abre e lista as categorias", "Mercado" in html and "Sem limite definido" in html and "Defina um limite" in html)
html = texto(cliente.get("/", query_string=mes_ano))
conferir("sem limites, o início não mostra o cartão", "Orçamento do mês" not in html)

r = cliente.post(f"/orcamento/{cat_mercado['id']}/definir", data={"limite": "500,00", "mes": hoje.month, "ano": hoje.year}, follow_redirects=True)
conferir("define limite de R$ 500,00 para Mercado", "Limite mensal de Mercado: R$ 500,00" in texto(r))
conferir("o campo vem preenchido com 500,00", 'value="500,00"' in texto(r))

html = texto(cliente.get("/", query_string=mes_ano))
conferir("sem gasto ainda, tudo dentro dos limites", "Orçamento do mês" in html and "Tudo dentro dos limites" in html)

cliente.post("/novo/despesa", data={"valor": "410,00", "descricao": "Feira", "categoria_id": cat_mercado["id"], "data": data_hoje})
html = texto(cliente.get("/orcamento", query_string=mes_ano))
conferir("410 de 500 = 82%: zona de atenção", "82% usado" in html and "Restam R$ 90,00" in html)
html = texto(cliente.get("/", query_string=mes_ano))
conferir("o início destaca a categoria em atenção", "Mercado" in html and "82% usado" in html)

cliente.post("/novo/despesa", data={"valor": "150,00", "descricao": "Mais feira", "categoria_id": cat_mercado["id"], "data": data_hoje})
html = texto(cliente.get("/orcamento", query_string=mes_ano))
conferir("560 de 500: passou R$ 60,00", "Passou R$ 60,00" in html and "112%" in html)
conferir("barra não passa de 100%", "width: 100%" in html and "width: 112%" not in html)

cliente.post("/contas/nova", data={"descricao": "Padaria", "valor": "40,00", "categoria_id": cat_mercado["id"], "vencimento": data_hoje, "parcelas": "1"})
html = texto(cliente.get("/orcamento", query_string=mes_ano))
conferir("contas em aberto aparecem como previsto", "com contas em aberto: R$ 600,00" in html)

r = cliente.post(f"/orcamento/{cat_mercado['id']}/definir", data={"limite": "abc"}, follow_redirects=True)
conferir("limite inválido é recusado sem apagar o antigo", "Informe um valor" in texto(r) and 'value="500,00"' in texto(r))
r = cliente.post(f"/orcamento/{cat_salario['id']}/definir", data={"limite": "100"})
conferir("categoria de receita não aceita limite", r.status_code == 404)

cliente.post(f"/orcamento/{cat_mercado['id']}/definir", data={"limite": "", "acao": "remover"})
conferir("remover tira o limite", not any(o["limite"] for o in meubolso.orcamento_do_periodo(*meubolso.limites_do_mes(hoje.month, hoje.year))))
html = texto(cliente.get("/", query_string=mes_ano))
conferir("sem limites, o cartão some de novo", "Orçamento do mês" not in html)

cliente.post(f"/orcamento/{cat_mercado['id']}/definir", data={"limite": "500"})
mes_passado = meubolso.mes_vizinho(hoje.month, hoje.year, -1)
html = texto(cliente.get("/orcamento", query_string={"mes": mes_passado[0], "ano": mes_passado[1]}))
conferir("o limite vale em todos os meses (mês passado, sem gasto)", "Restam R$ 500,00" in html)

print("Bancos das versões anteriores")
nome_backup = backup.criar_backup()
caminho = os.path.join(os.environ["MEUBOLSO_BACKUPS"], nome_backup)
antigo = os.path.join(pasta, "v2.db")
shutil.copy(caminho, antigo)

try:
    conexao = sqlite3.connect(antigo)
    conexao.execute("DROP TABLE orcamentos")
    for coluna in ("parcela_grupo", "parcela_numero", "parcela_total"):
        conexao.execute(f"ALTER TABLE contas DROP COLUMN {coluna}")
    conexao.commit()
    conexao.close()
    preparou = True
except sqlite3.OperationalError:
    preparou = False
    print("  (pulado: este SQLite não consegue simular um banco antigo)")

if preparou:
    ok, _ = backup.restaurar_de_arquivo(antigo)
    conexao = banco.conectar()
    tabelas = {l[0] for l in conexao.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    colunas = {l["name"] for l in conexao.execute("PRAGMA table_info(contas)")}
    conexao.close()
    conferir("backup da versão 2 é aceito", ok)
    conferir("tabela de orçamento e colunas de parcela são criadas ao restaurar",
             "orcamentos" in tabelas and {"parcela_grupo", "parcela_numero", "parcela_total"} <= colunas)

    cliente.post("/login", data={"senha": "1234"})
    r = cliente.get("/contas", query_string=mes_ano)
    conferir("as telas funcionam depois de restaurar um backup antigo", r.status_code == 200)

print()
if falhas:
    print(f"{len(falhas)} verificação(ões) falharam.")
    sys.exit(1)

print("Tudo certo: todas as verificações passaram.")
