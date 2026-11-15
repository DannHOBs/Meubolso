"""Teste automático das contas a pagar e recorrências (versão 2).

Uso (na raiz do projeto):  python tests/teste_contas.py
Usa um banco temporário; não mexe nos seus dados.
"""

import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

pasta = tempfile.mkdtemp()
os.environ["OFFBANK_DB"] = os.path.join(pasta, "teste.db")
os.environ["OFFBANK_BACKUPS"] = os.path.join(pasta, "backups")
os.environ["OFFBANK_SECRET_KEY"] = "chave-de-teste"

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["OFFBANK_TEMPLATES"] = str(RAIZ / "templates")

import app as offbank  # noqa: E402
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
    linhas = conexao.execute(consulta + " ORDER BY vencimento", valores).fetchall()
    conexao.close()
    return linhas


def total_lancamentos():
    return len(banco.listar_lancamentos("2000-01-01", "2100-01-01"))


cliente = offbank.app.test_client()
cliente.post("/primeiro-acesso", data={"nome": "Dani", "senha": "Gato&Lua2026", "confirmar": "Gato&Lua2026"})

hoje = date.today()
cat_moradia = next(c for c in banco.listar_categorias("despesa") if c["nome"] == "Moradia")
cat_salario = next(c for c in banco.listar_categorias("receita") if c["nome"] == "Salário")


def nova_conta(descricao, valor, vencimento, repete=False):
    dados = {
        "descricao": descricao, "valor": valor,
        "categoria_id": cat_moradia["id"], "vencimento": vencimento.isoformat(),
    }
    if repete:
        dados["repete"] = "1"
    return cliente.post("/contas/nova", data=dados, follow_redirects=True)


def mes_seguinte(data, quantos=1):
    mes, ano = offbank.mes_vizinho(data.month, data.year, quantos)
    return mes, ano


print("Datas")
conferir("dia 31 em fevereiro vira 28", banco.data_do_mes(2026, 2, 31) == "2026-02-28")
conferir("dia 31 em fevereiro de ano bissexto vira 29", banco.data_do_mes(2028, 2, 31) == "2028-02-29")
conferir("dia 31 em abril vira 30", banco.data_do_mes(2026, 4, 31) == "2026-04-30")
conferir("dia 15 continua 15", banco.data_do_mes(2026, 7, 15) == "2026-07-15")

print("Contas a pagar")
r = cliente.post("/contas/nova", data={"descricao": "", "valor": "10", "categoria_id": cat_moradia["id"], "vencimento": hoje.isoformat()})
conferir("conta sem descrição é recusada", "Diga o que é a conta" in texto(r))
r = cliente.post("/contas/nova", data={"descricao": "X", "valor": "10", "categoria_id": cat_salario["id"], "vencimento": hoje.isoformat()})
conferir("categoria de receita é recusada", "Escolha uma categoria" in texto(r))
r = cliente.post("/contas/nova", data={"descricao": "X", "valor": "10", "categoria_id": cat_moradia["id"], "vencimento": "32/13/2026"})
conferir("vencimento inválido é recusado", "vencimento válida" in texto(r))

r = nova_conta("Luz", "180,50", hoje - timedelta(days=2))
conferir("cria conta atrasada e mostra aviso de atraso", "Conta criada" in texto(r) and "Atrasada há 2 dias" in texto(r))
conferir("seção de atrasadas aparece", "Atrasadas" in texto(r))

nova_conta("Internet", "99,90", hoje + timedelta(days=3))
r = cliente.get("/")
html = texto(r)
conferir("início mostra o alerta de conta atrasada", "1 conta atrasada" in html and "R$ 180,50" in html)
conferir("início mostra conta que vence em 3 dias", "Internet" in html and "Vence em 3 dias" in html)

luz = contas_no_banco(descricao="Luz")[0]
lancamentos_antes = total_lancamentos()

r = cliente.post(f"/contas/{luz['id']}/pagar", follow_redirects=True)
conferir("pagar registra o pagamento", "Pagamento de R$ 180,50 registrado" in texto(r))
conferir("pagar cria um lançamento de despesa", total_lancamentos() == lancamentos_antes + 1)
luz = contas_no_banco(descricao="Luz")[0]
lancamento = banco.buscar_lancamento(luz["lancamento_id"])
conferir("lançamento tem valor, tipo e data certos",
         lancamento["valor_centavos"] == 18050 and lancamento["tipo"] == "despesa" and lancamento["data"] == hoje.isoformat())
conferir("conta fica marcada como paga", luz["paga"] == 1)

r = cliente.get("/")
conferir("início não mostra mais atraso", "conta atrasada" not in texto(r))

r = cliente.post(f"/contas/{luz['id']}/pagar", follow_redirects=True)
conferir("pagar duas vezes não duplica", "já estava paga" in texto(r) and total_lancamentos() == lancamentos_antes + 1)

r = cliente.post(f"/contas/{luz['id']}/editar", data={"descricao": "Luz 2", "valor": "1", "categoria_id": cat_moradia["id"], "vencimento": hoje.isoformat()}, follow_redirects=True)
conferir("conta paga não pode ser editada", "não pode ser editada" in texto(r) and contas_no_banco(descricao="Luz"))
r = cliente.post(f"/contas/{luz['id']}/excluir", follow_redirects=True)
conferir("conta paga não pode ser excluída", "não pode ser excluída" in texto(r) and contas_no_banco(descricao="Luz"))

r = cliente.post(f"/contas/{luz['id']}/desfazer", follow_redirects=True)
conferir("desfazer pagamento reabre a conta", "Pagamento desfeito" in texto(r) and contas_no_banco(descricao="Luz")[0]["paga"] == 0)
conferir("desfazer remove o lançamento", total_lancamentos() == lancamentos_antes)

cliente.post(f"/contas/{luz['id']}/pagar")
lancamento_id = contas_no_banco(descricao="Luz")[0]["lancamento_id"]
r = cliente.post(f"/excluir/{lancamento_id}", follow_redirects=True)
conferir("excluir o lançamento pelo menu reabre a conta", "voltou para" in texto(r) and contas_no_banco(descricao="Luz")[0]["paga"] == 0)

internet = contas_no_banco(descricao="Internet")[0]
r = cliente.post(f"/contas/{internet['id']}/editar", data={"descricao": "Internet fibra", "valor": "120,00", "categoria_id": cat_moradia["id"], "vencimento": internet["vencimento"]}, follow_redirects=True)
conferir("edita conta em aberto", "Conta atualizada" in texto(r) and contas_no_banco(descricao="Internet fibra")[0]["valor_centavos"] == 12000)
r = cliente.get(f"/contas/{internet['id']}/editar")
conferir("tela de edição mostra o valor com vírgula", 'value="120,00"' in texto(r))
r = cliente.post(f"/contas/{internet['id']}/excluir", follow_redirects=True)
conferir("exclui conta em aberto", "Conta excluída" in texto(r) and not contas_no_banco(descricao="Internet fibra"))

print("Contas recorrentes")
nova_conta("Aluguel", "850,00", hoje, repete=True)
recorrencias = banco.listar_recorrencias()
conferir("criou a recorrência com o dia do vencimento", len(recorrencias) == 1 and recorrencias[0]["dia"] == hoje.day)
aluguel_id = recorrencias[0]["id"]

mes2, ano2 = mes_seguinte(hoje)
r = cliente.get("/contas", query_string={"mes": mes2, "ano": ano2})
conferir("conta do mês seguinte é gerada sozinha", "Aluguel" in texto(r) and "🔁" in texto(r))
conferir("são 2 contas da recorrência", len(contas_no_banco(recorrencia_id=aluguel_id)) == 2)

cliente.get("/contas", query_string={"mes": mes2, "ano": ano2})
cliente.get("/")
conferir("visitar de novo não duplica", len(contas_no_banco(recorrencia_id=aluguel_id)) == 2)

mes3, ano3 = mes_seguinte(hoje, 2)
cliente.get("/contas", query_string={"mes": mes3, "ano": ano3})
conferir("ver um mês mais adiante gera aquele mês", len(contas_no_banco(recorrencia_id=aluguel_id)) == 3)

geradas = contas_no_banco(recorrencia_id=aluguel_id)
excluida = geradas[1]
cliente.post(f"/contas/{excluida['id']}/excluir")
cliente.get("/contas", query_string={"mes": mes3, "ano": ano3})
cliente.get("/")
conferir("conta gerada que foi apagada não volta", len(contas_no_banco(recorrencia_id=aluguel_id)) == 2)

cliente.post(f"/recorrentes/{aluguel_id}/alternar")
mes4, ano4 = mes_seguinte(hoje, 3)
cliente.get("/contas", query_string={"mes": mes4, "ano": ano4})
conferir("recorrência pausada não gera novas contas", len(contas_no_banco(recorrencia_id=aluguel_id)) == 2)
cliente.post(f"/recorrentes/{aluguel_id}/alternar")
cliente.get("/contas", query_string={"mes": mes4, "ano": ano4})
conferir("retomada, volta a gerar", len(contas_no_banco(recorrencia_id=aluguel_id)) >= 3)

primeira = contas_no_banco(recorrencia_id=aluguel_id)[0]
cliente.post(f"/contas/{primeira['id']}/pagar")
r = cliente.post(f"/recorrentes/{aluguel_id}/editar", data={"descricao": "Aluguel novo", "valor": "900,00", "categoria_id": cat_moradia["id"], "dia": "10"}, follow_redirects=True)
conferir("edita a recorrência", "Recorrência atualizada" in texto(r))
paga = contas_no_banco(id=primeira["id"])[0]
em_aberto = [c for c in contas_no_banco(recorrencia_id=aluguel_id) if not c["paga"]]
conferir("conta já paga não muda", paga["descricao"] == "Aluguel" and paga["valor_centavos"] == 85000)
conferir("contas em aberto ganham valor e dia novos",
         em_aberto and all(c["valor_centavos"] == 90000 and c["descricao"] == "Aluguel novo" and c["vencimento"].endswith("-10") for c in em_aberto))

r = cliente.post(f"/recorrentes/{aluguel_id}/editar", data={"descricao": "X", "valor": "1", "categoria_id": cat_moradia["id"], "dia": "40"})
conferir("dia inválido é recusado", "de 1 a 31" in texto(r))

r = cliente.post(f"/recorrentes/{aluguel_id}/excluir", follow_redirects=True)
restantes = contas_no_banco()
conferir("excluir recorrência remove contas em aberto e mantém as pagas",
         "Recorrência excluída" in texto(r) and not banco.listar_recorrencias()
         and any(c["id"] == primeira["id"] and c["recorrencia_id"] is None for c in restantes)
         and all(c["paga"] for c in restantes if c["descricao"].startswith("Aluguel")))

print("Backup da versão 1")
antigo = os.path.join(pasta, "v1.db")
backup._copiar_banco_para(antigo)
conexao = sqlite3.connect(antigo)
conexao.execute("DROP TABLE contas")
conexao.execute("DROP TABLE recorrencias")
conexao.commit()
conexao.close()

ok, mensagem = backup.restaurar_de_arquivo(antigo, "Gato&Lua2026")
conferir("backup feito antes da versão 2 é aceito", ok)
conexao = banco.conectar()
tabelas = {l[0] for l in conexao.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
conexao.close()
conferir("as tabelas novas são criadas após restaurar", {"contas", "recorrencias"} <= tabelas)

print()
if falhas:
    print(f"{len(falhas)} verificação(ões) falharam.")
    sys.exit(1)

print("Tudo certo: todas as verificações passaram.")
