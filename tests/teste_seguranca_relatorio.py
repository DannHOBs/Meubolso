"""Teste automático da segurança (senha forte, desbloqueio rápido, backup) e do relatório em PDF (versão 4).

Uso (na raiz do projeto):  python tests/teste_seguranca_relatorio.py
Usa um banco temporário; não mexe nos seus dados.
"""

import io
import os
import pathlib
import sys
import tempfile
from datetime import date, timedelta

pasta = tempfile.mkdtemp()
os.environ["OFFBANK_DB"] = os.path.join(pasta, "teste.db")
os.environ["OFFBANK_BACKUPS"] = os.path.join(pasta, "backups")
os.environ["OFFBANK_SECRET_KEY"] = "chave-de-teste"
os.environ.pop("OFFBANK_TOKEN_RAPIDO", None)

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["OFFBANK_TEMPLATES"] = str(RAIZ / "templates")

import app as offbank  # noqa: E402
import banco  # noqa: E402
import relatorio  # noqa: E402
import seguranca  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

try:
    import pypdf
except ImportError:
    pypdf = None

falhas = []


def conferir(descricao, condicao):
    print(("  ok   " if condicao else "  FALHOU  ") + descricao)
    if not condicao:
        falhas.append(descricao)


def texto(resposta):
    return resposta.get_data(as_text=True)


def texto_do_pdf(bytes_pdf):
    leitor = pypdf.PdfReader(io.BytesIO(bytes_pdf))
    return "\n".join(pagina.extract_text() for pagina in leitor.pages), len(leitor.pages)


FORTE = "Gato&Lua2026"
NOVA = "Outra&Senha77"

print("Conta criada em versão antiga (senha curta)")
banco.criar_usuario("Dani", generate_password_hash("1234"))
cliente = offbank.app.test_client()

r = cliente.post("/login", data={"senha": "1234"}, follow_redirects=True)
conferir("a senha curta antiga ainda entra", r.status_code == 200 and "Olá, Dani" in texto(r))
conferir("o início avisa que a senha é curta", "Sua senha é curta" in texto(r))

r = cliente.post("/backup/criar", data={"senha": "1234"}, follow_redirects=True)
conferir("com senha curta, o backup é recusado", "curta ou fácil demais" in texto(r) and not offbank.backup.listar_backups())
r = cliente.post("/backup/restaurar", data={"arquivo": (io.BytesIO(b"x"), "x.mbk"), "senha_backup": "", "senha_atual": "1234"},
                 content_type="multipart/form-data", follow_redirects=True)
conferir("com senha curta, restaurar também pede troca", "curta ou fácil demais" in texto(r))

r = cliente.post("/senha", data={"atual": "1234", "nova": FORTE, "confirmar": FORTE}, follow_redirects=True)
conferir("trocar para senha forte funciona", "Senha alterada" in texto(r))
conferir("o aviso de senha curta some", "Sua senha é curta" not in texto(cliente.get("/")))
cliente.post("/backup/criar", data={"senha": FORTE})
conferir("com senha forte, o backup é criado", len(offbank.backup.listar_backups()) == 1)

print("Backup antigo sem proteção na lista")
antigo = os.path.join(os.environ["OFFBANK_BACKUPS"], "backup_2026-01-01_10-00-00.db")
offbank.backup._copiar_banco_para(antigo)
html = texto(cliente.get("/backup"))
conferir("a tela avisa sobre backups sem proteção", "SEM proteção" in html and "sem proteção" in html)
nome_antigo = "backup_2026-01-01_10-00-00.db"
r = cliente.get(f"/backup/baixar/{nome_antigo}")
conferir("backup antigo não pode ser baixado", r.status_code == 404)
r = cliente.post(f"/backup/excluir/{nome_antigo}", follow_redirects=True)
conferir("backup antigo pode ser excluído", "Backup excluído" in texto(r) and not os.path.exists(antigo))
r = cliente.post("/backup/excluir/arquivo-que-nao-existe.db")
conferir("excluir nome inválido dá erro", r.status_code == 404)

print("Desbloqueio rápido (fora do Android)")
html = texto(cliente.get("/desbloqueio-rapido"))
conferir("fora do Android a tela explica que não existe", "só existe no aplicativo para Android" in html)
conferir("e o menu Mais não mostra a opção", "Desbloqueio rápido" not in texto(cliente.get("/mais")))
cliente.get("/sair")
r = cliente.get("/entrada-rapida?t=qualquer")
conferir("sem token configurado, a entrada rápida não funciona", r.status_code == 302 and "/login" in r.location)
r = cliente.get("/")
conferir("e continua precisando de senha", r.status_code == 302 and "/login" in r.location)

print("Desbloqueio rápido (dentro do Android)")
TOKEN = "a" * 64
os.environ["OFFBANK_TOKEN_RAPIDO"] = TOKEN
cliente.post("/login", data={"senha": FORTE})
conferir("o menu Mais mostra a opção (Desligado)", "Desbloqueio rápido" in texto(cliente.get("/mais")) and "Desligado" in texto(cliente.get("/mais")))

r = cliente.post("/desbloqueio-rapido", data={"acao": "ativar", "senha": "errada"}, follow_redirects=True)
conferir("ligar exige a senha certa", "Senha incorreta" in texto(r) and not offbank.rapido_ativo())
r = cliente.post("/desbloqueio-rapido", data={"acao": "ativar", "senha": FORTE}, follow_redirects=True)
conferir("liga o desbloqueio rápido", "Desbloqueio rápido ligado" in texto(r) and offbank.rapido_ativo())

cliente.get("/sair")
conferir("a tela de login oferece o botão do bloqueio do celular", "Entrar com o bloqueio do celular" in texto(cliente.get("/login")))

r = cliente.get("/entrada-rapida?t=errado")
conferir("token errado não entra", r.status_code == 302 and "/login" in r.location and cliente.get("/").status_code == 302)
r = cliente.get("/entrada-rapida?t=" + "ç" * 5)
conferir("token com acentos não quebra", r.status_code == 302 and "/login" in r.location)
r = cliente.get("/entrada-rapida")
conferir("sem token não entra", r.status_code == 302 and "/login" in r.location)
r = cliente.get("/entrada-rapida?t=" + TOKEN, follow_redirects=True)
conferir("token certo entra", r.status_code == 200 and "Olá, Dani" in texto(r))

cliente.post("/desbloqueio-rapido", data={"acao": "desativar"})
cliente.get("/sair")
r = cliente.get("/entrada-rapida?t=" + TOKEN)
conferir("desligado, nem o token certo entra", r.status_code == 302 and "/login" in r.location and cliente.get("/").status_code == 302)
conferir("e o botão some da tela de login", "Entrar com o bloqueio do celular" not in texto(cliente.get("/login")))
os.environ.pop("OFFBANK_TOKEN_RAPIDO")
cliente.post("/login", data={"senha": FORTE})

print("Relatório em PDF")
hoje = date.today()
mes_ano = {"mes": hoje.month, "ano": hoje.year}
cat = {c["nome"]: c["id"] for c in banco.listar_categorias()}
cat_mercado = next(c for c in banco.listar_categorias("despesa") if c["nome"] == "Mercado")
cat_salario = next(c for c in banco.listar_categorias("receita") if c["nome"] == "Salário")
cat_lazer = next(c for c in banco.listar_categorias("despesa") if c["nome"] == "Lazer")
cat_moradia = next(c for c in banco.listar_categorias("despesa") if c["nome"] == "Moradia")
data_hoje = hoje.isoformat()

r = cliente.get("/relatorio/pdf", query_string=mes_ano)
conferir("mês vazio gera PDF válido", r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF"))
if pypdf:
    conteudo, paginas = texto_do_pdf(r.data)
    conferir("mês vazio diz que não há lançamentos", "Nenhum lançamento neste mês" in conteudo and "Nenhuma despesa neste mês" in conteudo)

cliente.post("/novo/receita", data={"valor": "5.000,00", "descricao": "Salário de outubro", "categoria_id": cat_salario["id"], "data": data_hoje})
cliente.post("/novo/despesa", data={"valor": "420,50", "descricao": "Feira & <b>padaria</b>", "categoria_id": cat_mercado["id"], "data": data_hoje})
cliente.post("/novo/despesa", data={"valor": "80,00", "descricao": "Cinema", "categoria_id": cat_lazer["id"], "data": data_hoje})
cliente.post(f"/orcamento/{cat_mercado['id']}/definir", data={"limite": "400,00"})
cliente.post("/contas/nova", data={"descricao": "Aluguel", "valor": "1.200,00", "categoria_id": cat_moradia["id"], "vencimento": data_hoje, "parcelas": "1"})
cliente.post("/contas/nova", data={"descricao": "Sofá", "valor": "300,00", "categoria_id": cat_moradia["id"], "vencimento": data_hoje, "parcelas": "2", "valor_tipo": "parcela"})

html = texto(cliente.get("/relatorio", query_string=mes_ano))
conferir("tela do relatório mostra o saldo e o botão de baixar", "R$ 4.499,50" in html and "Baixar PDF de" in html and "5.000,00" in html)
conferir("tela do lançamentos tem o atalho do PDF", "📄 PDF" in texto(cliente.get("/lancamentos", query_string=mes_ano)))
conferir("o menu Mais tem o Relatório em PDF", "Relatório em PDF" in texto(cliente.get("/mais")))

r = cliente.get("/relatorio/pdf", query_string=mes_ano)
conferir("PDF do mês é válido e vem como download", r.status_code == 200 and r.data.startswith(b"%PDF")
         and f"OffBank_{hoje.year}-{hoje.month:02d}.pdf" in r.headers.get("Content-Disposition", ""))

with open(os.path.join(pasta, "exemplo_relatorio.pdf"), "wb") as arquivo:
    arquivo.write(r.data)

if pypdf:
    conteudo, paginas = texto_do_pdf(r.data)
    conferir("PDF tem o título e o nome", "Relatório mensal" in conteudo and "Dani" in conteudo)
    conferir("PDF mostra entradas, saídas e saldo", "R$ 5.000,00" in conteudo and "R$ 500,50" in conteudo and "R$ 4.499,50" in conteudo)
    conferir("PDF lista as categorias com porcentagem", "Mercado" in conteudo and "84,0%" in conteudo and "Lazer" in conteudo)
    conferir("PDF mostra o orçamento estourado", "Orçamento do mês" in conteudo and "Estourado" in conteudo and "R$ 400,00" in conteudo)
    conferir("PDF lista as contas (com parcelas)", "Aluguel" in conteudo and "Sofá (1/2)" in conteudo and "Vence hoje" in conteudo)
    conferir("PDF lista os lançamentos, inclusive com caracteres especiais", "Salário de outubro" in conteudo and "Feira & <b>padaria</b>" in conteudo)
    conferir("PDF tem rodapé com página", "Página 1" in conteudo and "OffBank" in conteudo)

mes_passado = offbank.mes_vizinho(hoje.month, hoje.year, -1)
r = cliente.get("/relatorio/pdf", query_string={"mes": mes_passado[0], "ano": mes_passado[1]})
conferir("outro mês gera outro PDF", r.status_code == 200 and f"{mes_passado[1]}-{mes_passado[0]:02d}" in r.headers.get("Content-Disposition", ""))
r = cliente.get("/relatorio/pdf", query_string={"mes": "13", "ano": "abc"})
conferir("mês inválido volta ao mês atual", r.status_code == 200 and r.data.startswith(b"%PDF"))

for i in range(150):
    cliente.post("/novo/despesa", data={"valor": f"{i + 1},00", "descricao": f"Compra número {i + 1} com uma descrição bem comprida para quebrar de linha na tabela do relatório",
                                         "categoria_id": cat_mercado["id"], "data": (hoje - timedelta(days=0)).isoformat()})
r = cliente.get("/relatorio/pdf", query_string=mes_ano)
conferir("mês com muitos lançamentos gera vários páginas", r.status_code == 200 and r.data.startswith(b"%PDF"))
if pypdf:
    conteudo, paginas = texto_do_pdf(r.data)
    conferir("o PDF tem mais de uma página e todas têm rodapé", paginas >= 3 and f"Página {paginas}" in conteudo)
    conferir("o último lançamento aparece", "Compra número 150" in conteudo)

pdf_direto = relatorio.gerar_pdf({
    "nome": "Teste", "mes": 2, "ano": 2026, "gerado_em": __import__("datetime").datetime(2026, 2, 1, 9, 30),
    "entradas": 0, "saidas": 0, "saldo_anterior": -12345,
    "despesas_por_categoria": [], "receitas_por_categoria": [], "orcamento": [], "contas": [], "lancamentos": [],
})
conferir("gerar_pdf funciona sozinho, com saldo acumulado negativo", pdf_direto.startswith(b"%PDF"))
if pypdf:
    conteudo, _ = texto_do_pdf(pdf_direto)
    conferir("saldo negativo é mostrado com sinal", "-R$ 123,45" in conteudo and "Fevereiro de 2026" in conteudo)

print()
if falhas:
    print(f"{len(falhas)} verificação(ões) falharam.")
    sys.exit(1)

print("Tudo certo: todas as verificações passaram.")
