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
conferir("senha curta é recusada", "pelo menos 8" in texto(r))

r = cliente.post("/primeiro-acesso", data={"nome": "Dani", "senha": "Gato&Lua2026", "confirmar": "Gato&Lua2026"}, follow_redirects=True)
conferir("cria usuário e entra no início", r.status_code == 200 and "Olá, Dani" in texto(r))

cliente.get("/sair")
r = cliente.get("/lancamentos")
conferir("sem login, as telas pedem senha", r.status_code == 302 and "/login" in r.location)

r = cliente.post("/login", data={"senha": "errada"})
conferir("senha errada é recusada", "Senha incorreta" in texto(r))
cliente.post("/login", data={"senha": "errada"})
r = cliente.post("/login", data={"senha": "errada"})
conferir("3 erros bloqueiam por um tempo", "Aguarde" in texto(r))
r = cliente.post("/login", data={"senha": "Gato&Lua2026"})
conferir("durante o bloqueio nem a senha certa entra", r.status_code == 200 and "Aguarde" in texto(r))
meubolso._falhas_login.update(quantas=0, bloqueado_ate=0.0)
r = cliente.post("/login", data={"senha": "Gato&Lua2026"})
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
NOVA = "Outra&Senha77"
r = cliente.post("/senha", data={"atual": "x", "nova": NOVA, "confirmar": NOVA})
conferir("troca de senha exige a atual", "senha atual está incorreta" in texto(r))
r = cliente.post("/senha", data={"atual": "Gato&Lua2026", "nova": "abcd", "confirmar": "abcd"})
conferir("nova senha curta é recusada", "pelo menos 8" in texto(r))
r = cliente.post("/senha", data={"atual": "Gato&Lua2026", "nova": "12345678", "confirmar": "12345678"})
conferir("nova senha óbvia é recusada", "fácil de adivinhar" in texto(r))
r = cliente.post("/senha", data={"atual": "Gato&Lua2026", "nova": NOVA, "confirmar": NOVA}, follow_redirects=True)
conferir("troca a senha", "Senha alterada" in texto(r))

print("Backup protegido")
r = cliente.post("/backup/criar", data={"senha": "errada"}, follow_redirects=True)
conferir("backup exige a senha certa", "Senha incorreta" in texto(r) and not meubolso.backup.listar_backups())

cliente.post("/backup/criar", data={"senha": NOVA})
backups = meubolso.backup.listar_backups()
conferir("cria backup protegido (.mbk)", len(backups) == 1 and backups[0]["protegido"] and backups[0]["nome"].endswith(".mbk"))

r = cliente.get(f"/backup/baixar/{backups[0]['nome']}")
conferir("baixa o backup", r.status_code == 200 and r.data.startswith(meubolso.seguranca.MAGIA))
conferir("o arquivo baixado não mostra nada legível",
         b"SQLite format 3" not in r.data and "Salário".encode() not in r.data and b"Pao" not in r.data and "Pão".encode() not in r.data)
r = cliente.get("/backup/baixar/../../etc/passwd")
conferir("nome de arquivo malicioso é barrado", r.status_code == 404)

with open(os.path.join(os.environ["MEUBOLSO_BACKUPS"], backups[0]["nome"]), "rb") as arquivo_backup:
    conteudo_backup = arquivo_backup.read()

cliente.post("/novo/despesa", data={"valor": "99", "descricao": "Depois do backup", "categoria_id": cat_mercado["id"], "data": data_hoje})


def restaurar(conteudo, nome_arquivo="meu.mbk", senha_backup=NOVA, senha_atual=NOVA):
    return cliente.post(
        "/backup/restaurar",
        data={"arquivo": (io.BytesIO(conteudo), nome_arquivo), "senha_backup": senha_backup, "senha_atual": senha_atual},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def tem_lancamento_depois():
    return any(l["descricao"] == "Depois do backup" for l in banco.listar_lancamentos("2000-01-01", "2100-01-01"))


r = restaurar(b"isto nao e um banco", "lixo.db")
conferir("arquivo inválido é recusado", "não é um backup" in texto(r) and tem_lancamento_depois())
r = restaurar(conteudo_backup, senha_backup="senha-errada-1")
conferir("senha do backup errada é recusada", "incorreta" in texto(r) and tem_lancamento_depois())
r = restaurar(conteudo_backup, senha_atual="outra-errada-1")
conferir("senha atual errada é recusada", "senha atual está incorreta" in texto(r) and tem_lancamento_depois())
adulterado = bytearray(conteudo_backup)
adulterado[len(adulterado) // 2] ^= 1
r = restaurar(bytes(adulterado))
conferir("backup adulterado é recusado", "incorreta" in texto(r) and tem_lancamento_depois())

r = restaurar(conteudo_backup)
conferir("restaura backup válido e pede login", "Backup restaurado" in texto(r))
conferir("lançamento feito depois do backup sumiu", not tem_lancamento_depois())
copias = [b for b in meubolso.backup.listar_backups() if "antes-de-restaurar" in b["nome"]]
conferir("restauração guardou cópia de segurança protegida", len(copias) == 1 and copias[0]["protegido"])
r = cliente.post("/login", data={"senha": NOVA})
conferir("depois de restaurar, entra com a senha da cópia", r.status_code == 302)

r = cliente.post(f"/backup/excluir/{copias[0]['nome']}", follow_redirects=True)
conferir("exclui backup do aparelho", "Backup excluído" in texto(r) and not any("antes-de" in b["nome"] for b in meubolso.backup.listar_backups()))

print("Backup antigo (.db sem proteção)")
antigo_plano = os.path.join(pasta, "antigo.db")
meubolso.backup._copiar_banco_para(antigo_plano)
with open(antigo_plano, "rb") as arquivo_antigo:
    conteudo_antigo = arquivo_antigo.read()
cliente.post("/novo/despesa", data={"valor": "99", "descricao": "Depois do backup", "categoria_id": cat_mercado["id"], "data": data_hoje})
r = restaurar(conteudo_antigo, "antigo.db", senha_backup="")
conferir("ainda restaura um .db antigo (sem senha do backup)", "Backup restaurado" in texto(r) and not tem_lancamento_depois())

print()
if falhas:
    print(f"{len(falhas)} verificação(ões) falharam.")
    sys.exit(1)

print("Tudo certo: todas as verificações passaram.")
