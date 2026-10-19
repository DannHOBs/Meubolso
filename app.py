import os
import secrets
import sqlite3
import tempfile
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import backup
import banco

try:
    import build_info  # criado pelo GitHub na hora de gerar o APK
    VERSAO_BUILD = f"{build_info.COMMIT} · {build_info.DATA}"
except ImportError:
    VERSAO_BUILD = "versão local"

app = Flask(__name__, template_folder=os.getenv("MEUBOLSO_TEMPLATES", "templates"))
app.secret_key = os.getenv("MEUBOLSO_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
)

banco.criar_banco()

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

TIPOS = ("receita", "despesa")

SENHA_MINIMA = 4
INATIVIDADE_SEGUNDOS = 15 * 60
TENTATIVAS_MAXIMAS = 3
BLOQUEIO_SEGUNDOS = 60

_falhas_login = {"quantas": 0, "bloqueado_ate": 0.0}


# ==========================================
# FORMATAÇÃO
# ==========================================

def formatar_moeda(centavos):
    negativo = centavos < 0
    reais, resto = divmod(abs(int(centavos)), 100)
    texto = f"{reais:,}".replace(",", ".")
    return f"{'-' if negativo else ''}R$ {texto},{resto:02d}"


def formatar_data(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return iso or ""


def centavos_para_campo(centavos):
    """Valor de 12345 -> '123,45' (para preencher o formulário de edição)."""
    reais, resto = divmod(int(centavos), 100)
    return f"{reais},{resto:02d}"


def ler_valor(texto):
    """Converte '1.234,56' ou '12.50' em centavos. Devolve None se for inválido."""
    texto = (texto or "").strip().replace("R$", "").replace(" ", "")

    if not texto:
        return None

    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")

    try:
        valor = Decimal(texto)
    except InvalidOperation:
        return None

    if not valor.is_finite():
        return None

    centavos = int((valor * 100).to_integral_value())

    if centavos <= 0 or centavos > 100_000_000_00:
        return None

    return centavos


def ler_data(texto):
    try:
        return datetime.strptime((texto or "").strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


app.jinja_env.filters["moeda"] = formatar_moeda
app.jinja_env.filters["data_br"] = formatar_data


@app.context_processor
def variaveis_globais():
    return {"versao_build": VERSAO_BUILD}


# ==========================================
# PERÍODO (mês navegável)
# ==========================================

def periodo():
    hoje = date.today()

    try:
        mes = int(request.args.get("mes", hoje.month))
        ano = int(request.args.get("ano", hoje.year))
    except ValueError:
        return hoje.month, hoje.year

    if not (1 <= mes <= 12 and 2000 <= ano <= 2100):
        return hoje.month, hoje.year

    return mes, ano


def mes_vizinho(mes, ano, delta):
    indice = mes - 1 + delta
    return indice % 12 + 1, ano + indice // 12


def limites_do_mes(mes, ano):
    proximo_mes, proximo_ano = mes_vizinho(mes, ano, 1)
    return f"{ano:04d}-{mes:02d}-01", f"{proximo_ano:04d}-{proximo_mes:02d}-01"


def dados_do_periodo(mes, ano):
    anterior = mes_vizinho(mes, ano, -1)
    seguinte = mes_vizinho(mes, ano, 1)

    return {
        "mes": mes,
        "ano": ano,
        "nome_mes": MESES[mes - 1],
        "ant": {"mes": anterior[0], "ano": anterior[1]},
        "prox": {"mes": seguinte[0], "ano": seguinte[1]},
    }


# ==========================================
# PROTEÇÃO: primeiro acesso, login e inatividade
# ==========================================

@app.before_request
def proteger_sistema():
    endpoint = request.endpoint

    if endpoint is None or endpoint == "static":
        return None

    if not banco.usuario_existe():
        if endpoint != "primeiro_acesso":
            return redirect(url_for("primeiro_acesso"))
        return None

    if endpoint in ("login", "primeiro_acesso"):
        return None

    if not session.get("logado"):
        return redirect(url_for("login"))

    agora = time.time()

    if agora - session.get("ultimo_acesso", agora) > INATIVIDADE_SEGUNDOS:
        session.clear()
        flash("Você ficou um tempo sem usar o app. Entre novamente.", "aviso")
        return redirect(url_for("login"))

    session["ultimo_acesso"] = agora
    return None


def entrar(usuario_nome):
    session.clear()
    session["logado"] = True
    session["nome"] = usuario_nome
    session["ultimo_acesso"] = time.time()


@app.route("/primeiro-acesso", methods=["GET", "POST"])
def primeiro_acesso():
    if banco.usuario_existe():
        return redirect(url_for("login"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()[:40]
        senha = request.form.get("senha", "")
        confirmar = request.form.get("confirmar", "")

        if not nome:
            flash("Diga como podemos te chamar.", "erro")
        elif len(senha) < SENHA_MINIMA:
            flash(f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.", "erro")
        elif senha != confirmar:
            flash("As duas senhas não são iguais.", "erro")
        else:
            banco.criar_usuario(nome, generate_password_hash(senha))
            entrar(nome)
            return redirect(url_for("inicio"))

    return render_template(
        "primeiro_acesso.html",
        sem_menu=True,
        titulo_pagina="Bem-vindo ao Meu Bolso",
        subtitulo_pagina="Crie sua senha para proteger seus dados",
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        agora = time.time()

        if agora < _falhas_login["bloqueado_ate"]:
            espera = int(_falhas_login["bloqueado_ate"] - agora) + 1
            flash(f"Muitas tentativas. Aguarde {espera} segundos.", "erro")
        else:
            usuario = banco.obter_usuario()

            if usuario and check_password_hash(usuario["senha"], request.form.get("senha", "")):
                _falhas_login.update(quantas=0, bloqueado_ate=0.0)
                entrar(usuario["nome"])
                return redirect(url_for("inicio"))

            _falhas_login["quantas"] += 1

            if _falhas_login["quantas"] >= TENTATIVAS_MAXIMAS:
                _falhas_login.update(quantas=0, bloqueado_ate=agora + BLOQUEIO_SEGUNDOS)
                flash(f"Senha incorreta. Aguarde {BLOQUEIO_SEGUNDOS} segundos para tentar de novo.", "erro")
            else:
                flash("Senha incorreta.", "erro")

    usuario = banco.obter_usuario()

    return render_template(
        "login.html",
        sem_menu=True,
        titulo_pagina="Meu Bolso",
        subtitulo_pagina=f"Olá, {usuario['nome']}! Digite sua senha." if usuario else "",
    )


@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))


@app.route("/senha", methods=["GET", "POST"])
def alterar_senha():
    if request.method == "POST":
        atual = request.form.get("atual", "")
        nova = request.form.get("nova", "")
        confirmar = request.form.get("confirmar", "")
        usuario = banco.obter_usuario()

        if not check_password_hash(usuario["senha"], atual):
            flash("A senha atual está incorreta.", "erro")
        elif len(nova) < SENHA_MINIMA:
            flash(f"A nova senha precisa ter pelo menos {SENHA_MINIMA} caracteres.", "erro")
        elif nova != confirmar:
            flash("As duas senhas novas não são iguais.", "erro")
        else:
            banco.alterar_senha(generate_password_hash(nova))
            flash("Senha alterada com sucesso.", "sucesso")
            return redirect(url_for("mais"))

    return render_template(
        "senha.html",
        aba="mais",
        titulo_pagina="Alterar senha",
        voltar_url=url_for("mais"),
        voltar_texto="Mais",
    )


# ==========================================
# INÍCIO (resumo do mês)
# ==========================================

@app.route("/")
def inicio():
    mes, ano = periodo()
    data_inicio, data_fim = limites_do_mes(mes, ano)

    entradas, saidas = banco.totais_do_periodo(data_inicio, data_fim)
    gastos = banco.gastos_por_categoria(data_inicio, data_fim)

    categorias = [
        {
            "nome": gasto["nome"],
            "icone": gasto["icone"],
            "total": gasto["total"],
            "percentual": round(gasto["total"] * 100 / saidas) if saidas else 0,
        }
        for gasto in gastos
    ]

    return render_template(
        "inicio.html",
        aba="inicio",
        endpoint_mes="inicio",
        nome=session.get("nome", ""),
        entradas=entradas,
        saidas=saidas,
        saldo_mes=entradas - saidas,
        saldo_anterior=banco.saldo_ate(data_inicio),
        categorias=categorias,
        recentes=banco.listar_lancamentos(data_inicio, data_fim, limite=5),
        **dados_do_periodo(mes, ano),
    )


# ==========================================
# LANÇAMENTOS
# ==========================================

@app.route("/lancamentos")
def lancamentos():
    mes, ano = periodo()
    data_inicio, data_fim = limites_do_mes(mes, ano)

    tipo = request.args.get("tipo")
    if tipo not in TIPOS:
        tipo = None

    return render_template(
        "lancamentos.html",
        aba="lancamentos",
        endpoint_mes="lancamentos",
        tipo_filtro=tipo,
        itens=banco.listar_lancamentos(data_inicio, data_fim, tipo=tipo),
        **dados_do_periodo(mes, ano),
    )


def ler_formulario_lancamento(tipo):
    """Lê e confere o formulário. Devolve (dados, mensagem_de_erro)."""
    valor = ler_valor(request.form.get("valor"))
    data = ler_data(request.form.get("data"))
    descricao = request.form.get("descricao", "").strip()[:80]

    try:
        categoria_id = int(request.form.get("categoria_id", ""))
    except ValueError:
        categoria_id = None

    categoria = banco.buscar_categoria(categoria_id) if categoria_id else None

    if valor is None:
        return None, "Informe um valor maior que zero. Exemplo: 25,90"

    if data is None:
        return None, "Informe uma data válida."

    if categoria is None or categoria["tipo"] != tipo:
        return None, "Escolha uma categoria."

    return {
        "valor": valor,
        "data": data,
        "descricao": descricao or categoria["nome"],
        "categoria_id": categoria["id"],
    }, None


def mes_do_lancamento(data_iso):
    return {"mes": int(data_iso[5:7]), "ano": int(data_iso[:4])}


@app.route("/novo/<tipo>", methods=["GET", "POST"])
def novo(tipo):
    if tipo not in TIPOS:
        abort(404)

    if request.method == "POST":
        dados, erro = ler_formulario_lancamento(tipo)

        if erro:
            flash(erro, "erro")
        else:
            banco.adicionar_lancamento(
                tipo, dados["valor"], dados["descricao"], dados["categoria_id"], dados["data"]
            )
            flash("Receita registrada." if tipo == "receita" else "Despesa registrada.", "sucesso")
            return redirect(url_for("lancamentos", **mes_do_lancamento(dados["data"])))

    return render_template(
        "lancamento_form.html",
        aba="lancamentos",
        tipo=tipo,
        editando=False,
        categorias=banco.listar_categorias(tipo),
        form=request.form if request.method == "POST" else {"data": date.today().isoformat()},
        titulo_pagina="Nova receita" if tipo == "receita" else "Nova despesa",
        voltar_url=url_for("lancamentos"),
        voltar_texto="Lançamentos",
    )


@app.route("/editar/<int:id_lancamento>", methods=["GET", "POST"])
def editar(id_lancamento):
    item = banco.buscar_lancamento(id_lancamento)

    if item is None:
        abort(404)

    tipo = item["tipo"]

    if request.method == "POST":
        dados, erro = ler_formulario_lancamento(tipo)

        if erro:
            flash(erro, "erro")
        else:
            banco.editar_lancamento(
                id_lancamento, dados["valor"], dados["descricao"], dados["categoria_id"], dados["data"]
            )
            flash("Lançamento atualizado.", "sucesso")
            return redirect(url_for("lancamentos", **mes_do_lancamento(dados["data"])))

        form = request.form
    else:
        form = {
            "valor": centavos_para_campo(item["valor_centavos"]),
            "descricao": item["descricao"],
            "categoria_id": str(item["categoria_id"]),
            "data": item["data"],
        }

    # Na edição, a categoria atual aparece mesmo que tenha sido ocultada depois.
    categorias = [
        c for c in banco.listar_categorias(tipo, so_ativas=False)
        if c["ativa"] or c["id"] == item["categoria_id"]
    ]

    return render_template(
        "lancamento_form.html",
        aba="lancamentos",
        tipo=tipo,
        editando=True,
        id_lancamento=id_lancamento,
        categorias=categorias,
        form=form,
        titulo_pagina="Editar lançamento",
        voltar_url=url_for("lancamentos", **mes_do_lancamento(item["data"])),
        voltar_texto="Lançamentos",
    )


@app.route("/excluir/<int:id_lancamento>", methods=["POST"])
def excluir(id_lancamento):
    item = banco.buscar_lancamento(id_lancamento)

    if item is None:
        abort(404)

    banco.excluir_lancamento(id_lancamento)
    flash("Lançamento excluído.", "sucesso")
    return redirect(url_for("lancamentos", **mes_do_lancamento(item["data"])))


# ==========================================
# CATEGORIAS
# ==========================================

@app.route("/categorias")
def categorias():
    return render_template(
        "categorias.html",
        aba="mais",
        receitas=banco.listar_categorias("receita", so_ativas=False),
        despesas=banco.listar_categorias("despesa", so_ativas=False),
        titulo_pagina="Categorias",
        subtitulo_pagina="Do seu jeito: renomeie, crie ou oculte",
        voltar_url=url_for("mais"),
        voltar_texto="Mais",
    )


def ler_categoria_do_formulario():
    nome = request.form.get("nome", "").strip()[:40]
    icone = request.form.get("icone", "").strip()[:4]
    return nome, icone


@app.route("/categorias/nova", methods=["POST"])
def categoria_nova():
    tipo = request.form.get("tipo")
    nome, icone = ler_categoria_do_formulario()

    if tipo not in TIPOS:
        abort(400)

    if not nome:
        flash("Digite o nome da categoria.", "erro")
    else:
        try:
            banco.adicionar_categoria(tipo, nome, icone or banco.ICONE_PADRAO[tipo])
            flash("Categoria criada.", "sucesso")
        except sqlite3.IntegrityError:
            flash("Você já tem uma categoria com esse nome.", "erro")

    return redirect(url_for("categorias"))


@app.route("/categorias/<int:id_categoria>/editar", methods=["POST"])
def categoria_editar(id_categoria):
    categoria = banco.buscar_categoria(id_categoria)

    if categoria is None:
        abort(404)

    nome, icone = ler_categoria_do_formulario()

    if not nome:
        flash("O nome da categoria não pode ficar vazio.", "erro")
    else:
        try:
            banco.editar_categoria(id_categoria, nome, icone or banco.ICONE_PADRAO[categoria["tipo"]])
            flash("Categoria atualizada.", "sucesso")
        except sqlite3.IntegrityError:
            flash("Você já tem uma categoria com esse nome.", "erro")

    return redirect(url_for("categorias"))


@app.route("/categorias/<int:id_categoria>/alternar", methods=["POST"])
def categoria_alternar(id_categoria):
    if banco.buscar_categoria(id_categoria) is None:
        abort(404)

    banco.alternar_categoria(id_categoria)
    return redirect(url_for("categorias"))


# ==========================================
# MAIS
# ==========================================

@app.route("/mais")
def mais():
    return render_template(
        "mais.html",
        aba="mais",
        nome=session.get("nome", ""),
        titulo_pagina="Mais",
        subtitulo_pagina="Ajustes e segurança",
    )


# ==========================================
# BACKUP
# ==========================================

@app.route("/backup")
def tela_backup():
    return render_template(
        "backup.html",
        aba="mais",
        backups=backup.listar_backups(),
        titulo_pagina="Backup",
        subtitulo_pagina="Guarde uma cópia dos seus dados",
        voltar_url=url_for("mais"),
        voltar_texto="Mais",
    )


@app.route("/backup/criar", methods=["POST"])
def criar_backup_web():
    try:
        backup.criar_backup()
        flash("Backup criado. Toque em Baixar para guardar fora do celular.", "sucesso")
    except Exception as erro:
        flash(f"Não foi possível criar o backup: {erro}", "erro")

    return redirect(url_for("tela_backup"))


@app.route("/backup/baixar/<nome>")
def baixar_backup(nome):
    if not backup.nome_valido(nome):
        abort(404)

    return send_from_directory(
        os.path.abspath(backup.PASTA_BACKUPS),
        nome,
        as_attachment=True,
        mimetype="application/octet-stream",
    )


@app.route("/backup/restaurar", methods=["POST"])
def restaurar_backup_web():
    arquivo = request.files.get("arquivo")

    if arquivo is None or not arquivo.filename:
        flash("Escolha o arquivo de backup (.db).", "erro")
        return redirect(url_for("tela_backup"))

    descritor, caminho_temporario = tempfile.mkstemp(suffix=".db")
    os.close(descritor)

    try:
        arquivo.save(caminho_temporario)
        ok, resultado = backup.restaurar_de_arquivo(caminho_temporario)
    finally:
        os.remove(caminho_temporario)

    if not ok:
        flash(resultado, "erro")
        return redirect(url_for("tela_backup"))

    # Os dados mudaram por inteiro (inclusive a senha): entra de novo.
    session.clear()
    flash("Backup restaurado. Entre com a senha que você usava naquela cópia.", "sucesso")
    return redirect(url_for("login"))


# ==========================================
# ERROS
# ==========================================

@app.errorhandler(404)
def pagina_nao_encontrada(_erro):
    return render_template(
        "erro.html",
        aba="inicio",
        titulo_pagina="Página não encontrada",
        mensagem="Não encontramos o que você procurava.",
    ), 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
