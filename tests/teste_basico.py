"""Teste automático do Meu Bolso: percorre o uso normal do app e confere os resultados.

Uso (na raiz do projeto):  python tests/teste_basico.py
Usa um banco temporário; não mexe nos seus dados.
"""

import io
import os
import pathlib
import sys
import tempfile

pasta = tempfile.mkdtemp()
os.environ["MEUBOLSO_DB"] = os.path.join(pasta, "teste.db")
os.environ["MEUBOLSO_BACKUPS"] = os.path.join(pasta, "backups")
os.environ["MEUBOLSO_SECRET_KEY"] = "chave-de-teste"

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["MEUBOLSO_TEMPLATES"] = str(RAIZ / "templates")

import app as meubolso  # noqa: E402
import banco  # noqa: E402

falhas = []


def conferir(descricao, condicao):
    print(("  ok   " if condicao else "  FALHOU  ") + descricao)
    if not condicao:
        falhas.append(descricao)


def texto(resposta):
    return resposta.get_data(as_text=True)


cliente = meubolso.app.test_client()

print("Primeiro acesso e login")
r = cliente.get("/")
conferir("sem usuário, '/' manda para o primeiro acesso", r.status_code == 302 and "primeiro-acesso" in r.location)

r = cliente.post("/primeiro-acesso", data={"nome": "Dani", "senha": "123", "confirmar": "123"})
conferir("senha curta é recusada", "pelo menos 4" in texto(r))

r = cliente.post("/primeiro-acesso", data={"nome": "Dani", "senha": "1234", "confirmar": "1234"}, follow_redirects=True)
conferir("cria usuário e entra no início", r.status_code == 200 and "Olá, Dani" in texto(r))

cliente.get("/sair")
r = cliente.get("/lancamentos")
conferir("sem login, as telas pedem senha", r.status_code == 302 and "/login" in r.location)

r = cliente.post("/login", data={"senha": "errada"})
conferir("senha errada é recusada", "Senha incorreta" in texto(r))
cliente.post("/login", data={"senha": "errada"})
r = cliente.post("/login", data={"senha": "errada"})
conferir("3 erros bloqueiam por um tempo", "Aguarde" in texto(r))
r = cliente.post("/login", data={"senha": "1234"})
conferir("durante o bloqueio nem a senha certa entra", r.status_code == 200 and "Aguarde" in texto(r))
meubolso._falhas_login.update(quantas=0, bloqueado_ate=0.0)
r = cliente.post("/login", data={"senha": "1234"})
conferir("depois do bloqueio, a senha certa entra", r.status_code == 302)

print("Lançamentos")
hoje = meubolso.date.today()
mes_ano = {"mes": hoje.month, "ano": hoje.year}
data_hoje = hoje.isoformat()

cat_salario = next(c for c in banco.listar_categorias("receita") if c["nome"] == "Salário")
cat_mercado = next(c for c in banco.listar_categorias("despesa") if c["nome"] == "Mercado")

r = cliente.post("/novo/receita", data={"valor": "3.500,00", "descricao": "", "categoria_id": cat_salario["id"], "data": data_hoje}, follow_redirects=True)
conferir("registra receita de 3.500,00", "Receita registrada" in texto(r) and "+R$ 3.500,00" in texto(r))
conferir("descrição vazia vira o nome da categoria", "Salário" in texto(r))

cliente.post("/novo/despesa", data={"valor": "250,90", "descricao": "Feira", "categoria_id": cat_mercado["id"], "data": data_hoje})
cliente.post("/novo/despesa", data={"valor": "10", "descricao": "Pão", "categoria_id": cat_mercado["id"], "data": data_hoje})

r = cliente.post("/novo/despesa", data={"valor": "abc", "categoria_id": cat_mercado["id"], "data": data_hoje})
conferir("valor inválido é recusado", "valor maior que zero" in texto(r))
r = cliente.post("/novo/despesa", data={"valor": "5", "categoria_id": cat_salario["id"], "data": data_hoje})
conferir("categoria de outro tipo é recusada", "Escolha uma categoria" in texto(r))
r = cliente.post("/novo/despesa", data={"valor": "5", "categoria_id": cat_mercado["id"], "data": "31/02/2026"})
conferir("data inválida é recusada", "data válida" in texto(r))

r = cliente.get("/", query_string=mes_ano)
html = texto(r)
conferir("saldo do mês = 3.500,00 - 260,90 = 3.239,10", "R$ 3.239,10" in html)
conferir("entradas e saídas aparecem", "R$ 3.500,00" in html and "R$ 260,90" in html)
conferir("gastos por categoria mostram Mercado", "Mercado" in html)

lancamentos = banco.listar_lancamentos("2000-01-01", "2100-01-01")
feira = next(l for l in lancamentos if l["descricao"] == "Feira")

r = cliente.get(f"/editar/{feira['id']}")
conferir("tela de edição mostra o valor com vírgula", 'value="250,90"' in texto(r))
r = cliente.post(f"/editar/{feira['id']}", data={"valor": "300,00", "descricao": "Feira grande", "categoria_id": cat_mercado["id"], "data": data_hoje}, follow_redirects=True)
conferir("edita o lançamento", "Lançamento atualizado" in texto(r) and "-R$ 300,00" in texto(r))

r = cliente.get("/lancamentos", query_string={**mes_ano, "tipo": "receita"})
conferir("filtro por receitas esconde despesas", "Feira grande" not in texto(r) and "Salário" in texto(r))

mes_passado = meubolso.mes_vizinho(hoje.month, hoje.year, -1)
r = cliente.get("/", query_string={"mes": mes_passado[0], "ano": mes_passado[1]})
conferir("mês passado vazio, saldo acumulado vazio", "Nenhuma despesa neste mês" in texto(r))
r = cliente.get("/", query_string={"mes": 13, "ano": 2026})
conferir("mês inválido não quebra a tela", r.status_code == 200)

r = cliente.post(f"/excluir/{feira['id']}", follow_redirects=True)
conferir("exclui o lançamento", "Lançamento excluído" in texto(r) and "Feira grande" not in texto(r))

print("Categorias")
r = cliente.post("/categorias/nova", data={"tipo": "despesa", "nome": "Pets", "icone": "🐶"}, follow_redirects=True)
conferir("cria categoria nova", "Categoria criada" in texto(r) and "Pets" in texto(r))
r = cliente.post("/categorias/nova", data={"tipo": "despesa", "nome": "Pets"}, follow_redirects=True)
conferir("não permite categoria repetida", "já tem uma categoria" in texto(r))
pets = next(c for c in banco.listar_categorias("despesa") if c["nome"] == "Pets")
cliente.post(f"/categorias/{pets['id']}/editar", data={"nome": "Animais", "icone": "🐾"})
conferir("renomeia categoria", banco.buscar_categoria(pets["id"])["nome"] == "Animais")
cliente.post(f"/categorias/{cat_mercado['id']}/alternar")
r = cliente.get("/novo/despesa")
conferir("categoria oculta some da escolha", "Mercado" not in texto(r))
r = cliente.get("/lancamentos", query_string=mes_ano)
conferir("mas continua nos lançamentos antigos", "Pão" in texto(r))
cliente.post(f"/categorias/{cat_mercado['id']}/alternar")

print("Senha")
r = cliente.post("/senha", data={"atual": "x", "nova": "abcd", "confirmar": "abcd"})
conferir("troca de senha exige a atual", "senha atual está incorreta" in texto(r))
r = cliente.post("/senha", data={"atual": "1234", "nova": "abcd", "confirmar": "abcd"}, follow_redirects=True)
conferir("troca a senha", "Senha alterada" in texto(r))

print("Backup")
cliente.post("/backup/criar")
backups = meubolso.backup.listar_backups()
conferir("cria backup", len(backups) == 1)
r = cliente.get(f"/backup/baixar/{backups[0]['nome']}")
conferir("baixa o backup", r.status_code == 200 and r.data[:15] == b"SQLite format 3")
r = cliente.get("/backup/baixar/../../etc/passwd")
conferir("nome de arquivo malicioso é barrado", r.status_code == 404)
with open(os.path.join(os.environ["MEUBOLSO_BACKUPS"], backups[0]["nome"]), "rb") as arquivo_backup:
    conteudo_backup = arquivo_backup.read()

cliente.post("/novo/despesa", data={"valor": "99", "descricao": "Depois do backup", "categoria_id": cat_mercado["id"], "data": data_hoje})

r = cliente.post("/backup/restaurar", data={"arquivo": (io.BytesIO(b"isto nao e um banco"), "lixo.db")}, content_type="multipart/form-data", follow_redirects=True)
conferir("arquivo inválido é recusado", "backup" in texto(r).lower() and "Depois do backup" not in texto(r) and len(banco.listar_lancamentos("2000-01-01", "2100-01-01")) == 3)

r = cliente.post("/backup/restaurar", data={"arquivo": (io.BytesIO(conteudo_backup), "meu.db")}, content_type="multipart/form-data", follow_redirects=True)
conferir("restaura backup válido e pede login", "Backup restaurado" in texto(r))
conferir("lançamento feito depois do backup sumiu", all(l["descricao"] != "Depois do backup" for l in banco.listar_lancamentos("2000-01-01", "2100-01-01")))
conferir("restauração guardou cópia de segurança", any("antes-de-restaurar" in b["nome"] for b in meubolso.backup.listar_backups()))

print()
if falhas:
    print(f"{len(falhas)} verificação(ões) falharam.")
    sys.exit(1)

print("Tudo certo: todas as verificações passaram.")
