"""Teste automático da versão 5: valores com ponto de milhar, backup automático, bloqueio que não zera,
pagar com valor e data, saldo inicial, esconder valores, modo escuro e lembrete de backup.

Uso (na raiz do projeto):  python tests/teste_versao5.py
Usa um banco temporário; não mexe nos seus dados.
"""

import os
import pathlib
import sys
import tempfile
import time
from datetime import date, timedelta

pasta = tempfile.mkdtemp()
os.environ["MEUBOLSO_DB"] = os.path.join(pasta, "teste.db")
os.environ["MEUBOLSO_BACKUPS"] = os.path.join(pasta, "backups")
os.environ["MEUBOLSO_SECRET_KEY"] = "chave-de-teste"
os.environ.pop("MEUBOLSO_TOKEN_RAPIDO", None)

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


def automaticos():
    return [b["nome"] for b in backup.listar_backups() if b["nome"].startswith(backup.PREFIXO_AUTO)]


FORTE = "Gato&Lua2026"
hoje = date.today()
data_hoje = hoje.isoformat()
mes_ano = {"mes": hoje.month, "ano": hoje.year}

print("Valores digitados")
for digitado, esperado in [
    ("1.500", 150000), ("2.000.000", 200000000), ("1.500,00", 150000), ("12.50", 1250),
    ("1.5", 150), ("25,90", 2590), ("R$ 1.234,56", 123456), ("10", 1000), ("0", None), ("1.50.0", None),
]:
    conferir(f"'{digitado}' vira {esperado} centavos", meubolso.ler_valor(digitado) == esperado)

print("Primeiro acesso e lembrete de backup")
cliente = meubolso.app.test_client()
cliente.post("/primeiro-acesso", data={"nome": "Dani", "senha": FORTE, "confirmar": FORTE})
conferir("primeiro acesso não cria backup automático (ainda não há dados)", not automaticos())

r = cliente.get("/")
conferir("sem lançamentos, o Início não cobra backup", "Fazer backup agora" not in texto(r))
conferir("as telas não ficam guardadas em cache", r.headers.get("Cache-Control") == "no-store")

cat_mercado = next(c for c in banco.listar_categorias("despesa") if c["nome"] == "Mercado")
cat_salario = next(c for c in banco.listar_categorias("receita") if c["nome"] == "Salário")
cliente.post("/novo/receita", data={"valor": "2.000", "categoria_id": cat_salario["id"], "data": data_hoje})
conferir("receita digitada como '2.000' fica com R$ 2.000,00",
         banco.totais_do_periodo(*meubolso.limites_do_mes(hoje.month, hoje.year))[0] == 200000)

r = cliente.get("/")
conferir("com lançamentos e sem backup, o Início lembra de fazer backup",
         "Você ainda não tem nenhum backup" in texto(r) and "Fazer backup agora" in texto(r))

print("Backup automático")
cliente.get("/sair")
cliente.post("/login", data={"senha": FORTE})
conferir("entrar com senha forte cria o backup automático", len(automaticos()) == 1)
conferir("o lembrete some depois do backup", "Fazer backup agora" not in texto(cliente.get("/")))

cliente.get("/sair")
cliente.post("/login", data={"senha": FORTE})
conferir("entrar de novo na mesma semana não cria outro", len(automaticos()) == 1)

primeiro = automaticos()[0]
with open(os.path.join(backup.PASTA_BACKUPS, primeiro), "rb") as arquivo:
    conferir("o automático é protegido por senha", arquivo.read().startswith(meubolso.seguranca.MAGIA))

# Simula as semanas seguintes (os nomes têm data e hora; espera 1 s entre eles para não repetir).
for semana in range(1, 6):
    time.sleep(1.05)
    backup.backup_automatico(FORTE, hoje + timedelta(days=7 * semana))

conferir(f"guarda só os {backup.AUTOMATICOS_GUARDADOS} automáticos mais recentes",
         len(automaticos()) == backup.AUTOMATICOS_GUARDADOS and primeiro not in automaticos())

marcado = automaticos()[0]
marcador = os.path.join(backup.PASTA_BACKUPS, marcado + backup.SUFIXO_EXPORTADO)
open(marcador, "w").close()
backup.excluir_backup(marcado)
conferir("excluir um backup também apaga o marcador de 'já copiado para Downloads'", not os.path.exists(marcador))
conferir("o marcador não aparece na lista de backups", not any(b["nome"].endswith(backup.SUFIXO_EXPORTADO) for b in backup.listar_backups()))

r = cliente.post("/backup/automatico", data={"acao": "desligar"}, follow_redirects=True)
conferir("dá para desligar o backup automático", "Backup automático desligado" in texto(r) and "Ligar backup automático" in texto(r))
antes = len(automaticos())
conferir("desligado, não cria backup", backup.backup_automatico(FORTE, hoje + timedelta(days=365)) is None and len(automaticos()) == antes)
cliente.post("/backup/automatico", data={"acao": "ligar"})
conferir("dá para ligar de novo", banco.obter_config("backup_automatico") == "1")

print("Bloqueio de senha que não zera ao fechar o app")
cliente.get("/sair")
for _ in range(meubolso.TENTATIVAS_MAXIMAS):
    cliente.post("/login", data={"senha": "errada"})
recarregado = meubolso._carregar_falhas_login()
conferir("o bloqueio fica guardado no banco (vale depois de reabrir o app)", recarregado["bloqueado_ate"] > time.time())
meubolso._falhas_login.update(bloqueado_ate=time.time() + 10 * 3600)
r = cliente.post("/login", data={"senha": "errada"})
conferir("se o relógio voltou, o bloqueio não passa de 60 segundos",
         meubolso._falhas_login["bloqueado_ate"] <= time.time() + meubolso.BLOQUEIO_SEGUNDOS)
meubolso._falhas_login.update(quantas=0, bloqueado_ate=0.0)
r = cliente.post("/login", data={"senha": FORTE})
conferir("depois do bloqueio, a senha certa entra e zera o contador",
         r.status_code == 302 and meubolso._carregar_falhas_login() == {"quantas": 0, "bloqueado_ate": 0.0})

print("Pagar conta com valor e data")
cliente.post("/contas/nova", data={"descricao": "Luz", "valor": "150,00", "categoria_id": cat_mercado["id"], "vencimento": data_hoje})
luz = next(c for c in banco.listar_contas_do_periodo(*meubolso.limites_do_mes(hoje.month, hoje.year)) if c["descricao"] == "Luz")

r = cliente.get(f"/contas/{luz['id']}/pagar")
conferir("a tela de pagar mostra o valor da conta e a data de hoje",
         'value="150,00"' in texto(r) and f'value="{data_hoje}"' in texto(r) and "Confirmar pagamento" in texto(r))
r = cliente.post(f"/contas/{luz['id']}/pagar", data={"valor": "abc", "data": data_hoje})
conferir("valor inválido é recusado", "valor maior que zero" in texto(r) and not banco.buscar_conta(luz["id"])["paga"])
r = cliente.post(f"/contas/{luz['id']}/pagar", data={"valor": "150", "data": "2026-02-31"})
conferir("data inválida é recusada", "data de pagamento válida" in texto(r) and not banco.buscar_conta(luz["id"])["paga"])

ontem = (hoje - timedelta(days=1)).isoformat()
r = cliente.post(f"/contas/{luz['id']}/pagar", data={"valor": "163,27", "data": ontem}, follow_redirects=True)
paga = banco.buscar_conta(luz["id"])
conferir("paga com o valor e a data informados",
         "Pagamento de R$ 163,27 registrado" in texto(r) and paga["paga"] and paga["valor_exibido"] == 16327 and paga["data_pagamento"] == ontem)
r = cliente.get(f"/contas/{luz['id']}/pagar", follow_redirects=True)
conferir("conta já paga não abre a tela de pagar de novo", "já estava paga" in texto(r))

print("Saldo inicial")
inicio, fim = meubolso.limites_do_mes(hoje.month, hoje.year)
saldo_antes = banco.saldo_ate(fim)
r = cliente.post("/saldo-inicial", data={"valor": "1.000,00"}, follow_redirects=True)
conferir("define o saldo inicial", "Saldo inicial: R$ 1.000,00" in texto(r) and banco.saldo_ate(fim) == saldo_antes + 100000)
r = cliente.get("/", query_string=mes_ano)
conferir("o saldo acumulado do Início soma o saldo inicial", meubolso.formatar_moeda(saldo_antes + 100000) in texto(r))
cliente.post("/saldo-inicial", data={"valor": "250,50", "negativo": "1"})
conferir("aceita saldo inicial negativo", banco.obter_saldo_inicial() == -25050)
r = cliente.get("/saldo-inicial")
conferir("a tela mostra o saldo salvo", 'value="250,50"' in texto(r) and "checked" in texto(r))
r = cliente.post("/saldo-inicial", data={"valor": "xyz"})
conferir("valor inválido é recusado sem mudar o saldo", "valor válido" in texto(r) and banco.obter_saldo_inicial() == -25050)
cliente.post("/saldo-inicial", data={"valor": ""})
conferir("em branco volta para zero", banco.obter_saldo_inicial() == 0)

print("Esconder valores")
r = cliente.get("/")
conferir("o Início tem o botão de olho", "botao-olho" in texto(r) and "valores-ocultos" not in texto(r).split("<body", 1)[1].split(">", 1)[0])
r = cliente.post("/ocultar-valores", data={"ocultar": "1"}, headers={"X-Requested-With": "fetch"})
conferir("pelo JavaScript, responde sem recarregar a tela", r.status_code == 204)
conferir("com valores escondidos, a página já abre escondida", 'class=" valores-ocultos"' in texto(cliente.get("/")))
conferir("as outras telas também", "valores-ocultos" in texto(cliente.get("/contas")) and "valores-ocultos" in texto(cliente.get("/recorrentes")))
r = cliente.post("/ocultar-valores", data={"ocultar": "0", "voltar": "/contas?mes=1&ano=2026"})
conferir("sem JavaScript, volta para a mesma tela", r.status_code == 302 and r.location.endswith("/contas?mes=1&ano=2026"))
r = cliente.post("/ocultar-valores", data={"ocultar": "0", "voltar": "//site-de-fora.com"})
conferir("nunca volta para um site de fora", r.status_code == 302 and "site-de-fora" not in r.location)
conferir("mostrar de novo funciona", "valores-ocultos" not in texto(cliente.get("/")).split("<body", 1)[1].split(">", 1)[0])

print("Modo escuro")
html = texto(cliente.get("/mais"))
conferir("o CSS tem o modo escuro automático", "prefers-color-scheme: dark" in html and 'data-tema="auto"' in html)
conferir("Mais tem a escolha de aparência", "Aparência" in html and "🌙 Escuro" in html)
cliente.post("/tema", data={"tema": "escuro"})
conferir("dá para forçar o modo escuro", 'data-tema="escuro"' in texto(cliente.get("/")))
cliente.post("/tema", data={"tema": "roxo"})
conferir("tema inválido é ignorado", banco.obter_config("tema") == "escuro")
cliente.post("/tema", data={"tema": "auto"})

print()
if falhas:
    print(f"{len(falhas)} verificação(ões) falharam.")
    sys.exit(1)

print("Tudo certo: todas as verificações passaram.")
