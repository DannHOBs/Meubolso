import hmac
import os
import re
import secrets
import sqlite3
import tempfile
import time
from datetime import date, datetime, timedelta
from io import BytesIO
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import backup
import banco
import relatorio
import seguranca

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

INATIVIDADE_SEGUNDOS = 15 * 60
TENTATIVAS_MAXIMAS = 3
BLOQUEIO_SEGUNDOS = 60
DIAS_PARA_LEMBRAR_BACKUP = 30

TEMAS = ("auto", "claro", "escuro")


def _carregar_falhas_login():
    """As tentativas erradas ficam no banco: fechar e abrir o app não zera o bloqueio."""
    try:
        return {
            "quantas": int(banco.obter_config("login_falhas", "0")),
            "bloqueado_ate": float(banco.obter_config("login_bloqueado_ate", "0")),
        }
    except ValueError:
        return {"quantas": 0, "bloqueado_ate": 0.0}


def _guardar_falhas_login():
    banco.definir_config("login_falhas", _falhas_login["quantas"])
    banco.definir_config("login_bloqueado_ate", _falhas_login["bloqueado_ate"])


_falhas_login = _carregar_falhas_login()


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
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):
        # "1.500" ou "2.000.000": sem vírgula e com grupos de 3, o ponto separa os milhares.
        texto = texto.replace(".", "")

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


def rapido_disponivel():
    """O desbloqueio pelo bloqueio do celular só existe dentro do app Android."""
    return bool(os.getenv("MEUBOLSO_TOKEN_RAPIDO"))


def rapido_ativo():
    return rapido_disponivel() and banco.obter_config("desbloqueio_rapido") == "1"


@app.context_processor
def variaveis_globais():
    tema = banco.obter_config("tema", "auto")

    return {
        "versao_build": VERSAO_BUILD,
        "senha_fraca": banco.obter_config("senha_fraca") == "1",
        "rapido_disponivel": rapido_disponivel(),
        "rapido_ativo": rapido_ativo(),
        "ocultar_valores": banco.obter_config("ocultar_valores") == "1",
        "tema": tema if tema in TEMAS else "auto",
    }


@app.after_request
def sem_cache(resposta):
    # Nada fica guardado no WebView: depois de "Sair", o botão voltar não mostra telas antigas com valores.
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


def destino_local(endereco):
    """Só aceita voltar para uma tela do próprio app (nunca para um site de fora)."""
    if endereco and endereco.startswith("/") and not endereco.startswith(("//", "/\\")):
        return endereco
    return url_for("inicio")


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

    if endpoint in ("login", "primeiro_acesso", "entrada_rapida"):
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
        erro_senha = seguranca.validar_senha(senha)

        if not nome:
            flash("Diga como podemos te chamar.", "erro")
        elif erro_senha:
            flash(erro_senha, "erro")
        elif senha != confirmar:
            flash("As duas senhas não são iguais.", "erro")
        else:
            banco.criar_usuario(nome, generate_password_hash(senha))
            banco.definir_config("senha_fraca", "0")
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

        # Se o relógio do celular voltou no tempo, o bloqueio não dura mais que o normal.
        if _falhas_login["bloqueado_ate"] > agora + BLOQUEIO_SEGUNDOS:
            _falhas_login["bloqueado_ate"] = agora + BLOQUEIO_SEGUNDOS

        if agora < _falhas_login["bloqueado_ate"]:
            espera = int(_falhas_login["bloqueado_ate"] - agora) + 1
            flash(f"Muitas tentativas. Aguarde {espera} segundos.", "erro")
        else:
            usuario = banco.obter_usuario()

            senha_digitada = request.form.get("senha", "")

            if usuario and check_password_hash(usuario["senha"], senha_digitada):
                _falhas_login.update(quantas=0, bloqueado_ate=0.0)
                _guardar_falhas_login()
                senha_fraca = bool(seguranca.validar_senha(senha_digitada))
                banco.definir_config("senha_fraca", "1" if senha_fraca else "0")
                entrar(usuario["nome"])

                # O backup automático usa a senha digitada agora (é ela que protege o arquivo).
                if not senha_fraca:
                    try:
                        backup.backup_automatico(senha_digitada)
                    except Exception:
                        pass  # um problema no backup nunca impede a entrada

                return redirect(url_for("inicio"))

            _falhas_login["quantas"] += 1

            if _falhas_login["quantas"] >= TENTATIVAS_MAXIMAS:
                _falhas_login.update(quantas=0, bloqueado_ate=agora + BLOQUEIO_SEGUNDOS)
                flash(f"Senha incorreta. Aguarde {BLOQUEIO_SEGUNDOS} segundos para tentar de novo.", "erro")
            else:
                flash("Senha incorreta.", "erro")

            _guardar_falhas_login()

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
        erro_senha = seguranca.validar_senha(nova)

        if not check_password_hash(usuario["senha"], atual):
            flash("A senha atual está incorreta.", "erro")
        elif erro_senha:
            flash(erro_senha, "erro")
        elif nova != confirmar:
            flash("As duas senhas novas não são iguais.", "erro")
        else:
            banco.alterar_senha(generate_password_hash(nova))
            banco.definir_config("senha_fraca", "0")
            flash("Senha alterada com sucesso.", "sucesso")
            return redirect(url_for("mais"))

    return render_template(
        "senha.html",
        aba="mais",
        titulo_pagina="Alterar senha",
        voltar_url=url_for("mais"),
        voltar_texto="Mais",
    )


def senha_atual_confere(senha):
    usuario = banco.obter_usuario()
    return bool(usuario) and check_password_hash(usuario["senha"], senha)


@app.route("/entrada-rapida")
def entrada_rapida():
    """Entrada pelo bloqueio do celular. Só o app Android conhece o código (token) desta abertura."""
    token = os.getenv("MEUBOLSO_TOKEN_RAPIDO", "")
    enviado = request.args.get("t", "")
    usuario = banco.obter_usuario()

    if token and usuario and rapido_ativo() and hmac.compare_digest(enviado.encode(), token.encode()):
        entrar(usuario["nome"])
        return redirect(url_for("inicio"))

    flash("Não foi possível entrar com o bloqueio do celular. Digite sua senha.", "erro")
    return redirect(url_for("login"))


@app.route("/desbloqueio-rapido", methods=["GET", "POST"])
def desbloqueio_rapido():
    if request.method == "POST":
        acao = request.form.get("acao")

        if not rapido_disponivel():
            flash("Esse recurso só existe no aplicativo para Android.", "aviso")
        elif acao == "ativar":
            if senha_atual_confere(request.form.get("senha", "")):
                banco.definir_config("desbloqueio_rapido", "1")
                flash("Desbloqueio rápido ligado. Na próxima abertura o app pede o bloqueio do celular.", "sucesso")
            else:
                flash("Senha incorreta.", "erro")
        elif acao == "desativar":
            banco.definir_config("desbloqueio_rapido", "0")
            flash("Desbloqueio rápido desligado. Agora só a senha do app abre.", "sucesso")

        return redirect(url_for("desbloqueio_rapido"))

    return render_template(
        "desbloqueio.html",
        aba="mais",
        titulo_pagina="Desbloqueio rápido",
        subtitulo_pagina="Digital, rosto ou PIN do celular",
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

    hoje = date.today()
    garantir_recorrentes(hoje)
    n_atrasadas, total_atrasadas = banco.resumo_atrasadas(hoje.isoformat())
    itens_orcamento = orcamento_do_periodo(data_inicio, data_fim)

    # Lembrete de backup: só para quem já tem lançamentos e está há muito tempo sem cópia.
    ultimo_backup = backup.data_do_ultimo_backup()
    dias_sem_backup = (hoje - ultimo_backup).days if ultimo_backup else None
    lembrar_backup = bool(banco.contar_lancamentos()) and (
        dias_sem_backup is None or dias_sem_backup > DIAS_PARA_LEMBRAR_BACKUP
    )

    return render_template(
        "inicio.html",
        aba="inicio",
        endpoint_mes="inicio",
        nome=session.get("nome", ""),
        lembrar_backup=lembrar_backup,
        dias_sem_backup=dias_sem_backup,
        contas_proximas=[
            enriquecer_conta(c, hoje)
            for c in banco.listar_contas_em_aberto_ate((hoje + timedelta(days=7)).isoformat(), limite=4)
        ],
        n_atrasadas=n_atrasadas,
        total_atrasadas=total_atrasadas,
        orcamento_alertas=[o for o in itens_orcamento if o["limite"] and o["situacao"] != "ok"][:3],
        tem_orcamento=any(o["limite"] for o in itens_orcamento),
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

    reabriu_conta = banco.excluir_lancamento(id_lancamento)

    if reabriu_conta:
        flash("Lançamento excluído. A conta que ele pagava voltou para \"em aberto\".", "aviso")
    else:
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
# CONTAS A PAGAR
# ==========================================

def garantir_recorrentes(hoje, ate=None):
    """Gera as contas recorrentes até o mês seguinte (ou até o mês que estiver sendo visto)."""
    proximo_mes_, proximo_ano = mes_vizinho(hoje.month, hoje.year, 1)
    alvo = (proximo_ano, proximo_mes_)

    if ate:
        limite = mes_vizinho(hoje.month, hoje.year, 24)
        alvo = max(alvo, min((ate[1], ate[0]), (limite[1], limite[0])))

    banco.gerar_recorrentes(hoje, alvo[0], alvo[1])


def enriquecer_conta(conta, hoje):
    """Acrescenta a situação (paga, atrasada...) e o texto do selo de cada conta."""
    dados = dict(conta)

    if conta["paga"]:
        dados["situacao"] = "paga"
        dados["rotulo"] = f"Paga em {formatar_data(conta['data_pagamento'])}" if conta["data_pagamento"] else "Paga"
        return dados

    dias = (date.fromisoformat(conta["vencimento"]) - hoje).days

    if dias < 0:
        dados["situacao"] = "atrasada"
        dados["rotulo"] = "Atrasada há 1 dia" if dias == -1 else f"Atrasada há {-dias} dias"
    elif dias == 0:
        dados["situacao"] = "hoje"
        dados["rotulo"] = "Vence hoje"
    elif dias == 1:
        dados["situacao"] = "proxima"
        dados["rotulo"] = "Vence amanhã"
    elif dias <= 7:
        dados["situacao"] = "proxima"
        dados["rotulo"] = f"Vence em {dias} dias"
    else:
        dados["situacao"] = "futura"
        dados["rotulo"] = f"Vence em {formatar_data(conta['vencimento'])[:5]}"

    return dados


@app.route("/contas")
def contas():
    hoje = date.today()
    mes, ano = periodo()
    garantir_recorrentes(hoje, ate=(mes, ano))

    data_inicio, data_fim = limites_do_mes(mes, ano)
    a_pagar, ja_pago = banco.totais_das_contas(data_inicio, data_fim)
    n_atrasadas, total_atrasadas = banco.resumo_atrasadas(hoje.isoformat())

    atrasadas = [
        enriquecer_conta(c, hoje)
        for c in banco.listar_contas_em_aberto_ate((hoje - timedelta(days=1)).isoformat())
    ]
    ids_atrasadas = {c["id"] for c in atrasadas}

    do_mes = [
        enriquecer_conta(c, hoje)
        for c in banco.listar_contas_do_periodo(data_inicio, data_fim)
        if c["id"] not in ids_atrasadas
    ]

    return render_template(
        "contas.html",
        aba="contas",
        endpoint_mes="contas",
        a_pagar=a_pagar,
        ja_pago=ja_pago,
        atrasadas=atrasadas,
        n_atrasadas=n_atrasadas,
        total_atrasadas=total_atrasadas,
        itens=do_mes,
        **dados_do_periodo(mes, ano),
    )


def ler_formulario_conta():
    """Lê e confere o formulário de conta. Devolve (dados, mensagem_de_erro)."""
    descricao = request.form.get("descricao", "").strip()[:80]
    valor = ler_valor(request.form.get("valor"))
    vencimento = ler_data(request.form.get("vencimento"))

    try:
        categoria_id = int(request.form.get("categoria_id", ""))
    except ValueError:
        categoria_id = None

    categoria = banco.buscar_categoria(categoria_id) if categoria_id else None

    if not descricao:
        return None, "Diga o que é a conta. Exemplo: Aluguel"

    if valor is None:
        return None, "Informe um valor maior que zero. Exemplo: 850,00"

    if vencimento is None:
        return None, "Informe uma data de vencimento válida."

    if categoria is None or categoria["tipo"] != "despesa":
        return None, "Escolha uma categoria."

    return {
        "descricao": descricao,
        "valor": valor,
        "categoria_id": categoria["id"],
        "vencimento": vencimento,
    }, None


def voltar_para_contas(vencimento):
    return redirect(url_for("contas", **mes_do_lancamento(vencimento)))


def criar_contas_do_formulario(dados):
    """Cria a conta (única, recorrente ou parcelada). Devolve (mensagem_de_sucesso, mensagem_de_erro)."""
    repete = bool(request.form.get("repete"))

    try:
        parcelas = int(request.form.get("parcelas") or 1)
    except ValueError:
        parcelas = 0

    if not 1 <= parcelas <= 60:
        return None, "O número de parcelas deve ficar entre 1 e 60."

    if repete and parcelas > 1:
        return None, "Escolha só uma opção: repetir todo mês OU parcelar."

    if repete:
        banco.adicionar_conta_recorrente(
            dados["descricao"], dados["valor"], dados["categoria_id"], dados["vencimento"]
        )
        return "Conta criada. Ela vai se repetir todo mês.", None

    if parcelas == 1:
        banco.adicionar_conta(
            dados["descricao"], dados["valor"], dados["categoria_id"], dados["vencimento"]
        )
        return "Conta criada.", None

    if request.form.get("valor_tipo") == "total":
        if dados["valor"] < parcelas:
            return None, "O total é pequeno demais para esse número de parcelas."
        valores = banco.dividir_valor(dados["valor"], parcelas)
    else:
        valores = [dados["valor"]] * parcelas

    vencimentos = banco.gerar_vencimentos(dados["vencimento"], parcelas)
    banco.adicionar_parcelas(dados["descricao"], valores, dados["categoria_id"], vencimentos)

    return (
        f"Compra parcelada: {parcelas} contas criadas, de {formatar_data(vencimentos[0])} "
        f"a {formatar_data(vencimentos[-1])}.",
        None,
    )


@app.route("/contas/nova", methods=["GET", "POST"])
def conta_nova():
    if request.method == "POST":
        dados, erro = ler_formulario_conta()
        sucesso = None

        if not erro:
            sucesso, erro = criar_contas_do_formulario(dados)

        if erro:
            flash(erro, "erro")
        else:
            flash(sucesso, "sucesso")
            return voltar_para_contas(dados["vencimento"])

    if request.method == "POST":
        form = request.form
    else:
        form = {"vencimento": date.today().isoformat(), "repete": request.args.get("repete")}

    return render_template(
        "conta_form.html",
        aba="contas",
        editando=False,
        categorias=banco.listar_categorias("despesa"),
        form=form,
        titulo_pagina="Nova conta recorrente" if form.get("repete") else "Nova conta",
        voltar_url=url_for("recorrentes") if request.args.get("repete") else url_for("contas"),
        voltar_texto="Recorrentes" if request.args.get("repete") else "Contas",
    )


@app.route("/contas/<int:id_conta>/editar", methods=["GET", "POST"])
def conta_editar(id_conta):
    conta = banco.buscar_conta(id_conta)

    if conta is None:
        abort(404)

    if conta["paga"]:
        flash("Conta paga não pode ser editada. Desfaça o pagamento primeiro.", "aviso")
        return voltar_para_contas(conta["vencimento"])

    if request.method == "POST":
        dados, erro = ler_formulario_conta()

        if erro:
            flash(erro, "erro")
        else:
            banco.editar_conta(
                id_conta, dados["descricao"], dados["valor"], dados["categoria_id"], dados["vencimento"]
            )
            flash("Conta atualizada.", "sucesso")
            return voltar_para_contas(dados["vencimento"])

        form = request.form
    else:
        form = {
            "descricao": conta["descricao"],
            "valor": centavos_para_campo(conta["valor_centavos"]),
            "categoria_id": str(conta["categoria_id"]),
            "vencimento": conta["vencimento"],
        }

    categorias = [
        c for c in banco.listar_categorias("despesa", so_ativas=False)
        if c["ativa"] or c["id"] == conta["categoria_id"]
    ]

    return render_template(
        "conta_form.html",
        aba="contas",
        editando=True,
        id_conta=id_conta,
        faz_parte_de_recorrencia=bool(conta["recorrencia_id"]),
        parcela_numero=conta["parcela_numero"],
        parcela_total=conta["parcela_total"],
        categorias=categorias,
        form=form,
        titulo_pagina="Editar conta",
        voltar_url=url_for("contas", **mes_do_lancamento(conta["vencimento"])),
        voltar_texto="Contas",
    )


@app.route("/contas/<int:id_conta>/excluir", methods=["POST"])
def conta_excluir(id_conta):
    conta = banco.buscar_conta(id_conta)

    if conta is None:
        abort(404)

    if banco.excluir_conta(id_conta):
        flash("Conta excluída.", "sucesso")
    else:
        flash("Conta paga não pode ser excluída. Desfaça o pagamento primeiro.", "aviso")

    return voltar_para_contas(conta["vencimento"])


@app.route("/contas/<int:id_conta>/excluir-parcelas", methods=["POST"])
def conta_excluir_parcelas(id_conta):
    conta = banco.buscar_conta(id_conta)

    if conta is None:
        abort(404)

    apagadas = banco.excluir_parcelas_em_aberto(id_conta)

    if apagadas:
        flash(f"{apagadas} parcela(s) em aberto excluída(s). As já pagas continuam no histórico.", "sucesso")
    else:
        flash("Essa conta não é uma compra parcelada.", "aviso")

    return voltar_para_contas(conta["vencimento"])


@app.route("/contas/<int:id_conta>/pagar", methods=["GET", "POST"])
def conta_pagar(id_conta):
    conta = banco.buscar_conta(id_conta)

    if conta is None:
        abort(404)

    if conta["paga"]:
        flash("Essa conta já estava paga.", "aviso")
        return voltar_para_contas(conta["vencimento"])

    if request.method == "POST":
        # Sem os campos, vale o valor da conta e a data de hoje.
        texto_valor = request.form.get("valor", "").strip()
        texto_data = request.form.get("data", "").strip()
        valor = ler_valor(texto_valor) if texto_valor else conta["valor_centavos"]
        data_pagamento = ler_data(texto_data) if texto_data else date.today().isoformat()

        if valor is None:
            flash("Informe um valor maior que zero. Exemplo: 180,50", "erro")
        elif data_pagamento is None:
            flash("Informe uma data de pagamento válida.", "erro")
        elif banco.pagar_conta(id_conta, data_pagamento, valor):
            flash(f"Pagamento de {formatar_moeda(valor)} registrado nos lançamentos.", "sucesso")
            return voltar_para_contas(conta["vencimento"])
        else:
            flash("Essa conta já estava paga.", "aviso")
            return voltar_para_contas(conta["vencimento"])

        form = request.form
    else:
        form = {
            "valor": centavos_para_campo(conta["valor_centavos"]),
            "data": date.today().isoformat(),
        }

    return render_template(
        "pagar.html",
        aba="contas",
        conta=enriquecer_conta(conta, date.today()),
        form=form,
        titulo_pagina="Pagar conta",
        subtitulo_pagina=conta["descricao_exibida"],
        voltar_url=url_for("contas", **mes_do_lancamento(conta["vencimento"])),
        voltar_texto="Contas",
    )


@app.route("/contas/<int:id_conta>/desfazer", methods=["POST"])
def conta_desfazer(id_conta):
    conta = banco.buscar_conta(id_conta)

    if conta is None:
        abort(404)

    if banco.desfazer_pagamento(id_conta):
        flash("Pagamento desfeito: o lançamento foi removido e a conta voltou para \"em aberto\".", "sucesso")
    else:
        flash("Essa conta não estava paga.", "aviso")

    return voltar_para_contas(conta["vencimento"])


# ==========================================
# CONTAS RECORRENTES
# ==========================================

@app.route("/recorrentes")
def recorrentes():
    return render_template(
        "recorrentes.html",
        aba="contas",
        itens=banco.listar_recorrencias(),
        titulo_pagina="Contas recorrentes",
        subtitulo_pagina="As que se repetem todo mês",
        voltar_url=url_for("contas"),
        voltar_texto="Contas",
    )


def ler_formulario_recorrencia():
    """Lê e confere o formulário de recorrência. Devolve (dados, mensagem_de_erro)."""
    descricao = request.form.get("descricao", "").strip()[:80]
    valor = ler_valor(request.form.get("valor"))

    try:
        dia = int(request.form.get("dia", ""))
    except ValueError:
        dia = None

    try:
        categoria = banco.buscar_categoria(int(request.form.get("categoria_id", "")))
    except ValueError:
        categoria = None

    if not descricao:
        return None, "Diga o que é a conta. Exemplo: Aluguel"

    if valor is None:
        return None, "Informe um valor maior que zero. Exemplo: 850,00"

    if categoria is None or categoria["tipo"] != "despesa":
        return None, "Escolha uma categoria."

    if dia is None or not 1 <= dia <= 31:
        return None, "Informe o dia do mês, de 1 a 31."

    return {
        "descricao": descricao,
        "valor": valor,
        "categoria_id": categoria["id"],
        "dia": dia,
    }, None


@app.route("/recorrentes/<int:id_recorrencia>/editar", methods=["GET", "POST"])
def recorrente_editar(id_recorrencia):
    recorrencia = banco.buscar_recorrencia(id_recorrencia)

    if recorrencia is None:
        abort(404)

    if request.method == "POST":
        dados, erro = ler_formulario_recorrencia()

        if erro:
            flash(erro, "erro")
        else:
            banco.editar_recorrencia(
                id_recorrencia, dados["descricao"], dados["valor"], dados["categoria_id"], dados["dia"]
            )
            flash("Recorrência atualizada (inclusive as contas que ainda estão em aberto).", "sucesso")
            return redirect(url_for("recorrentes"))

        form = request.form
    else:
        form = {
            "descricao": recorrencia["descricao"],
            "valor": centavos_para_campo(recorrencia["valor_centavos"]),
            "categoria_id": str(recorrencia["categoria_id"]),
            "dia": str(recorrencia["dia"]),
        }

    categorias = [
        c for c in banco.listar_categorias("despesa", so_ativas=False)
        if c["ativa"] or c["id"] == recorrencia["categoria_id"]
    ]

    return render_template(
        "recorrente_form.html",
        aba="contas",
        categorias=categorias,
        form=form,
        titulo_pagina="Editar recorrência",
        voltar_url=url_for("recorrentes"),
        voltar_texto="Recorrentes",
    )


@app.route("/recorrentes/<int:id_recorrencia>/alternar", methods=["POST"])
def recorrente_alternar(id_recorrencia):
    if banco.buscar_recorrencia(id_recorrencia) is None:
        abort(404)

    banco.alternar_recorrencia(id_recorrencia)
    return redirect(url_for("recorrentes"))


@app.route("/recorrentes/<int:id_recorrencia>/excluir", methods=["POST"])
def recorrente_excluir(id_recorrencia):
    if banco.buscar_recorrencia(id_recorrencia) is None:
        abort(404)

    banco.excluir_recorrencia(id_recorrencia)
    flash("Recorrência excluída, junto com as contas dela que estavam em aberto.", "sucesso")
    return redirect(url_for("recorrentes"))


# ==========================================
# ORÇAMENTO (limite mensal por categoria)
# ==========================================

def orcamento_do_periodo(data_inicio, data_fim):
    """Categorias com gasto, limite, porcentagem e situação (ok, atenção ou estourado)."""
    itens = []

    for linha in banco.listar_orcamento(data_inicio, data_fim):
        item = dict(linha)
        limite = linha["limite_centavos"]
        item["limite"] = limite
        item["previsto"] = linha["gasto"] + linha["em_aberto"]

        if limite:
            percentual = round(linha["gasto"] * 100 / limite)
            item["percentual"] = percentual
            item["largura"] = min(percentual, 100)
            item["restante"] = limite - linha["gasto"]

            if linha["gasto"] > limite:
                item["situacao"] = "estourado"
            elif percentual >= 80:
                item["situacao"] = "atencao"
            else:
                item["situacao"] = "ok"
        else:
            item["situacao"] = "sem_limite"

        itens.append(item)

    # Com limite primeiro (os mais apertados no topo); depois as sem limite, com mais gasto antes.
    itens.sort(key=lambda i: (
        i["limite"] is None,
        -(i.get("percentual") or 0),
        -i["gasto"],
        i["nome"].lower(),
    ))

    return itens


@app.route("/orcamento")
def orcamento():
    mes, ano = periodo()
    data_inicio, data_fim = limites_do_mes(mes, ano)
    itens = orcamento_do_periodo(data_inicio, data_fim)
    com_limite = [i for i in itens if i["limite"]]

    return render_template(
        "orcamento.html",
        aba="mais",
        endpoint_mes="orcamento",
        itens=itens,
        total_limite=sum(i["limite"] for i in com_limite),
        total_gasto=sum(i["gasto"] for i in com_limite),
        **dados_do_periodo(mes, ano),
    )


@app.route("/orcamento/<int:id_categoria>/definir", methods=["POST"])
def orcamento_definir(id_categoria):
    categoria = banco.buscar_categoria(id_categoria)

    if categoria is None or categoria["tipo"] != "despesa":
        abort(404)

    destino = {}

    try:
        destino = {"mes": int(request.form.get("mes", "")), "ano": int(request.form.get("ano", ""))}
    except ValueError:
        pass

    texto = request.form.get("limite", "").strip()

    if request.form.get("acao") == "remover" or not texto:
        banco.definir_orcamento(id_categoria, None)
        flash(f"Limite de {categoria['nome']} removido.", "sucesso")
    else:
        limite = ler_valor(texto)

        if limite is None:
            flash("Informe um valor maior que zero. Exemplo: 800,00", "erro")
        else:
            banco.definir_orcamento(id_categoria, limite)
            flash(f"Limite mensal de {categoria['nome']}: {formatar_moeda(limite)}.", "sucesso")

    return redirect(url_for("orcamento", **destino))


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


@app.route("/saldo-inicial", methods=["GET", "POST"])
def saldo_inicial():
    if request.method == "POST":
        texto = request.form.get("valor", "").strip()

        if texto.replace("R$", "").strip("0,. ") == "":  # vazio, "0" ou "0,00"
            centavos = 0
        else:
            centavos = ler_valor(texto)

        if centavos is None:
            flash("Informe um valor válido. Exemplo: 1.250,00", "erro")
        else:
            if request.form.get("negativo"):
                centavos = -centavos

            banco.definir_config("saldo_inicial", centavos)
            flash(f"Saldo inicial: {formatar_moeda(centavos)}.", "sucesso")
            return redirect(url_for("mais"))

        form = request.form
    else:
        atual = banco.obter_saldo_inicial()
        form = {
            "valor": centavos_para_campo(abs(atual)) if atual else "",
            "negativo": "1" if atual < 0 else "",
        }

    return render_template(
        "saldo_inicial.html",
        aba="mais",
        form=form,
        titulo_pagina="Saldo inicial",
        subtitulo_pagina="Quanto você tinha antes de usar o app",
        voltar_url=url_for("mais"),
        voltar_texto="Mais",
    )


@app.route("/ocultar-valores", methods=["POST"])
def ocultar_valores():
    banco.definir_config("ocultar_valores", "1" if request.form.get("ocultar") == "1" else "0")

    # A página troca na hora pelo JavaScript; o formulário comum (sem JavaScript) volta para a mesma tela.
    if request.headers.get("X-Requested-With") == "fetch":
        return "", 204

    return redirect(destino_local(request.form.get("voltar")))


@app.route("/tema", methods=["POST"])
def mudar_tema():
    tema = request.form.get("tema")

    if tema in TEMAS:
        banco.definir_config("tema", tema)

    return redirect(url_for("mais"))


# ==========================================
# BACKUP (protegido por senha)
# ==========================================

@app.route("/backup")
def tela_backup():
    backups = backup.listar_backups()

    return render_template(
        "backup.html",
        aba="mais",
        backups=backups,
        antigos_sem_protecao=any(not b["protegido"] for b in backups),
        automatico_ligado=banco.obter_config("backup_automatico", "1") == "1",
        ultimo_automatico=banco.obter_config("ultimo_backup_automatico"),
        titulo_pagina="Backup",
        subtitulo_pagina="Cópia protegida por senha",
        voltar_url=url_for("mais"),
        voltar_texto="Mais",
    )


@app.route("/backup/criar", methods=["POST"])
def criar_backup_web():
    senha = request.form.get("senha", "")

    if not senha_atual_confere(senha):
        flash("Senha incorreta.", "erro")
    elif seguranca.validar_senha(senha):
        flash(
            "Sua senha atual é curta ou fácil demais para proteger um backup. "
            "Troque a senha em Mais → Alterar senha e tente de novo.",
            "erro",
        )
    else:
        try:
            backup.criar_backup(senha)
            banco.definir_config("ultimo_backup", date.today().isoformat())
            flash("Backup protegido criado. Toque em Baixar para guardar fora do celular.", "sucesso")
        except Exception as erro:
            flash(f"Não foi possível criar o backup: {erro}", "erro")

    return redirect(url_for("tela_backup"))


@app.route("/backup/automatico", methods=["POST"])
def backup_automatico_web():
    if request.form.get("acao") == "ligar":
        banco.definir_config("backup_automatico", "1")
        flash("Backup automático ligado. O próximo é criado quando você entrar com a senha.", "sucesso")
    else:
        banco.definir_config("backup_automatico", "0")
        flash("Backup automático desligado. Lembre-se de criar e baixar backups por conta própria.", "aviso")

    return redirect(url_for("tela_backup"))


@app.route("/backup/baixar/<nome>")
def baixar_backup(nome):
    if not backup.nome_valido(nome) or not nome.endswith(".mbk"):
        abort(404)

    return send_from_directory(
        os.path.abspath(backup.PASTA_BACKUPS),
        nome,
        as_attachment=True,
        mimetype="application/octet-stream",
    )


@app.route("/backup/excluir/<nome>", methods=["POST"])
def excluir_backup_web(nome):
    if not backup.excluir_backup(nome):
        abort(404)

    flash("Backup excluído do aparelho.", "sucesso")
    return redirect(url_for("tela_backup"))


@app.route("/backup/restaurar", methods=["POST"])
def restaurar_backup_web():
    arquivo = request.files.get("arquivo")
    senha_do_backup = request.form.get("senha_backup", "")
    senha_atual = request.form.get("senha_atual", "")

    if arquivo is None or not arquivo.filename:
        flash("Escolha o arquivo de backup.", "erro")
        return redirect(url_for("tela_backup"))

    if not senha_atual_confere(senha_atual):
        flash("A sua senha atual está incorreta.", "erro")
        return redirect(url_for("tela_backup"))

    if seguranca.validar_senha(senha_atual):
        flash(
            "Sua senha atual é curta ou fácil demais. Troque-a em Mais → Alterar senha "
            "antes de restaurar (ela protege a cópia dos seus dados de agora).",
            "erro",
        )
        return redirect(url_for("tela_backup"))

    descritor, caminho_temporario = tempfile.mkstemp(suffix=".tmp")
    os.close(descritor)

    try:
        arquivo.save(caminho_temporario)
        ok, resultado = backup.restaurar_de_upload(caminho_temporario, senha_do_backup, senha_atual)
    finally:
        os.remove(caminho_temporario)

    if not ok:
        flash(resultado, "erro")
        return redirect(url_for("tela_backup"))

    # Os dados mudaram por inteiro (inclusive a senha): entra de novo.
    session.clear()
    flash(
        "Backup restaurado. Entre com a senha que você usava quando fez aquela cópia. "
        "Seus dados de antes ficaram guardados em Backup.",
        "sucesso",
    )
    return redirect(url_for("login"))


# ==========================================
# RELATÓRIO EM PDF
# ==========================================

def dados_do_relatorio(mes, ano):
    hoje = date.today()
    data_inicio, data_fim = limites_do_mes(mes, ano)
    entradas, saidas = banco.totais_do_periodo(data_inicio, data_fim)

    contas_do_mes = [
        enriquecer_conta(c, hoje) for c in banco.listar_contas_do_periodo(data_inicio, data_fim)
    ]

    return {
        "nome": session.get("nome", ""),
        "mes": mes,
        "ano": ano,
        "gerado_em": datetime.now(),
        "entradas": entradas,
        "saidas": saidas,
        "saldo_anterior": banco.saldo_ate(data_inicio),
        "despesas_por_categoria": [
            {"nome": g["nome"], "total": g["total"]}
            for g in banco.gastos_por_categoria(data_inicio, data_fim)
        ],
        "receitas_por_categoria": [
            {"nome": g["nome"], "total": g["total"]}
            for g in banco.gastos_por_categoria(data_inicio, data_fim, tipo="receita")
        ],
        "orcamento": [
            {"nome": o["nome"], "gasto": o["gasto"], "limite": o["limite"], "percentual": o["percentual"]}
            for o in orcamento_do_periodo(data_inicio, data_fim)
            if o["limite"]
        ],
        "contas": [
            {
                "vencimento": c["vencimento"],
                "descricao": c["descricao_exibida"],
                "valor": c["valor_exibido"],
                "situacao": c["situacao"],
                "rotulo": c["rotulo"],
            }
            for c in contas_do_mes
        ],
        "lancamentos": [
            {
                "data": l["data"],
                "descricao": l["descricao"],
                "categoria": l["categoria_nome"],
                "tipo": l["tipo"],
                "valor": l["valor_centavos"],
            }
            for l in reversed(banco.listar_lancamentos(data_inicio, data_fim))
        ],
    }


@app.route("/relatorio")
def tela_relatorio():
    mes, ano = periodo()
    data_inicio, data_fim = limites_do_mes(mes, ano)
    entradas, saidas = banco.totais_do_periodo(data_inicio, data_fim)

    return render_template(
        "relatorio.html",
        aba="mais",
        endpoint_mes="tela_relatorio",
        entradas=entradas,
        saidas=saidas,
        saldo_mes=entradas - saidas,
        quantidade=len(banco.listar_lancamentos(data_inicio, data_fim)),
        **dados_do_periodo(mes, ano),
    )


@app.route("/relatorio/pdf")
def baixar_relatorio_pdf():
    mes, ano = periodo()
    pdf = relatorio.gerar_pdf(dados_do_relatorio(mes, ano))

    return send_file(
        BytesIO(pdf),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"MeuBolso_{ano}-{mes:02d}.pdf",
    )


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
