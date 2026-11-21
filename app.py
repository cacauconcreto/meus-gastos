import os
import math
import calendar
from datetime import date, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, flash, session, g, abort, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

from database.banco import (
    criar_banco,
    cadastrar_usuario,
    buscar_usuario_email,
    adicionar_transacao,
    buscar_transacoes,
    conectar,
    listar_contas,
    criar_conta,
    excluir_conta,
    buscar_salario,
    salvar_salario,
    aplicar_salario,
    LIMITE_TOTAL,
    listar_limites,
    salvar_limite,
    remover_limite,
    listar_fixas,
    buscar_fixa,
    criar_fixa,
    excluir_fixa,
    fixas_pagas,
    pagar_fixa,
    listar_categorias,
    criar_categoria,
    excluir_categoria,
    criar_subcategoria,
    excluir_subcategoria,
    criar_transferencia,
    buscar_plano,
    salvar_plano
)

app = Flask(__name__)

# Em produção, defina a variável de ambiente SECRET_KEY.
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-por-uma-chave-secreta")

criar_banco()

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]
TIPOS_CONTA = {
    "corrente": "Conta corrente",
    "poupanca": "Poupança",
    "carteira": "Carteira",
    "investimento": "Investimentos",
}
CORES = ["#7138F2", "#F59E0B", "#10B981", "#EC4899", "#06B6D4", "#3B82F6", "#84CC16", "#64748B"]


def nomes_categorias(uid):
    return [c["nome"] for c in listar_categorias(uid)]


# ==========================================================
# FILTROS DE TEMPLATE
# ==========================================================

@app.template_filter("brl")
def brl(valor):
    texto = f"{float(valor or 0):.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("data_br")
def data_br(texto):
    try:
        return date.fromisoformat(texto).strftime("%d/%m")
    except (TypeError, ValueError):
        return texto


MESES_CURTOS = ["jan", "fev", "mar", "abr", "mai", "jun",
                "jul", "ago", "set", "out", "nov", "dez"]


@app.template_filter("data_curta")
def data_curta(texto):
    try:
        d = date.fromisoformat(texto)
        return f"{d.day:02d} {MESES_CURTOS[d.month - 1]}"
    except (TypeError, ValueError):
        return texto


# ==========================================================
# LOGIN OBRIGATÓRIO + USUÁRIO ATUAL
# ==========================================================

def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("login"))

        conexao = conectar()
        g.usuario = conexao.execute(
            "SELECT * FROM usuarios WHERE id = ?", (session["usuario_id"],)
        ).fetchone()
        conexao.close()

        # Sessão de um usuário que não existe mais
        if g.usuario is None:
            session.clear()
            return redirect(url_for("login"))

        aplicar_salario(g.usuario["id"])
        return func(*args, **kwargs)

    return wrapper


@app.context_processor
def injetar_usuario():
    usuario = g.get("usuario")
    contas = [c["nome"] for c in listar_contas(usuario["id"])] if usuario else []
    cats = listar_categorias(usuario["id"]) if usuario else []
    return {
        "usuario": usuario,
        "categorias_opcoes": [c["nome"] for c in cats],
        "categorias_arvore": {c["nome"]: [s["nome"] for s in c["subs"]] for c in cats},
        "contas_opcoes": contas,
        "tipos_conta": TIPOS_CONTA,
        "hoje": date.today().isoformat(),
    }


# ==========================================================
# PÁGINAS PÚBLICAS
# ==========================================================

@app.route("/")
def inicio():
    if "usuario_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if request.method == "POST":
        nome = request.form["nome"].strip()
        email = request.form["email"].strip().lower()
        senha = request.form["senha"]
        confirmar = request.form["confirmar_senha"]

        if not nome or not email or not senha:
            flash("Preencha todos os campos.", "erro")
        elif senha != confirmar:
            flash("As senhas não são iguais.", "erro")
        elif len(senha) < 6:
            flash("A senha precisa ter pelo menos 6 caracteres.", "erro")
        elif not cadastrar_usuario(nome, email, generate_password_hash(senha)):
            flash("Este e-mail já está cadastrado.", "erro")
        else:
            flash("Cadastro realizado com sucesso! Faça login.", "sucesso")
            return redirect(url_for("login"))

        return redirect(url_for("cadastro"))

    return render_template("cadastro.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "usuario_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form["email"].strip().lower()
        senha = request.form["senha"]
        usuario = buscar_usuario_email(email)

        if usuario is None or not check_password_hash(usuario["senha_hash"], senha):
            flash("E-mail ou senha inválidos.", "erro")
            return redirect(url_for("login"))

        session.clear()
        session["usuario_id"] = usuario["id"]
        return redirect(url_for("dashboard"))

    return render_template("login.html")


# ==========================================================
# FUNÇÕES AUXILIARES DO DASHBOARD
# ==========================================================

def primeiro_dia_do_mes(texto):
    """Converte '2026-11' em date(2026, 11, 1). Valor inválido = mês atual."""
    try:
        ano, mes = (int(p) for p in texto.split("-"))
        return date(ano, mes, 1)
    except (AttributeError, ValueError):
        return date.today().replace(day=1)


def deslocar_mes(d, n):
    indice = d.month - 1 + n
    return date(d.year + indice // 12, indice % 12 + 1, 1)


def calcular_resumo(transacoes):
    receitas = sum(t["valor"] for t in transacoes if t["tipo"] == "receita")
    despesas = sum(t["valor"] for t in transacoes if t["tipo"] == "despesa")
    return {"receitas": receitas, "despesas": despesas, "saldo": receitas - despesas}


def preparar_categorias(transacoes):
    """Soma despesas por categoria, da maior para a menor, com faixas do gráfico."""
    totais = {}
    for t in transacoes:
        if t["tipo"] == "despesa":
            totais[t["categoria"]] = totais.get(t["categoria"], 0) + t["valor"]

    total_geral = sum(totais.values())
    resultado, inicio = [], 0.0

    for i, (nome, valor) in enumerate(
        sorted(totais.items(), key=lambda item: item[1], reverse=True)
    ):
        porcentagem = valor / total_geral * 100 if total_geral else 0
        resultado.append({
            "nome": nome,
            "valor": valor,
            "porcentagem": porcentagem,
            "cor": CORES[i % len(CORES)],
            "inicio": inicio,
            "fim": inicio + porcentagem,
        })
        inicio += porcentagem

    return resultado


def calcular_fixas(usuario_id, hoje=None):
    """Despesas fixas do mês atual com vencimento e situação (paga, atrasada...)."""
    hoje = hoje or date.today()
    pagas = fixas_pagas(usuario_id, hoje.strftime("%Y-%m"))
    ultimo_dia = calendar.monthrange(hoje.year, hoje.month)[1]
    resultado = []

    for f in listar_fixas(usuario_id):
        venc = date(hoje.year, hoje.month, min(f["dia"], ultimo_dia))
        dias = (venc - hoje).days
        paga = f["id"] in pagas

        if paga:
            estado, texto = "paga", "Paga"
        elif dias < 0:
            estado, texto = "atrasada", f"Atrasada há {-dias} dia{'s' if dias < -1 else ''}"
        elif dias == 0:
            estado, texto = "hoje", "Vence hoje"
        elif dias <= 7:
            estado, texto = "breve", f"Vence em {dias} dia{'s' if dias > 1 else ''}"
        else:
            estado, texto = "pendente", f"Vence em {venc:%d/%m}"

        resultado.append({**dict(f), "venc": venc, "paga": paga, "estado": estado, "texto": texto})

    resultado.sort(key=lambda x: (x["paga"], x["venc"]))
    return resultado

PALETA = ["#7138F2", "#F59E0B", "#10B981", "#EC4899", "#06B6D4", "#EF4444", "#8B5CF6", "#84CC16"]


def agrupar_relatorio(transacoes, tipo):
    """Totais por categoria (com subcategorias) e faixas do gráfico."""
    cats = {}
    for t in transacoes:
        if t["tipo"] != tipo:
            continue
        c = cats.setdefault(t["categoria"], {"valor": 0, "subs": {}})
        c["valor"] += t["valor"]
        sub = t["subcategoria"] or "Sem subcategoria"
        c["subs"][sub] = c["subs"].get(sub, 0) + t["valor"]

    total = sum(c["valor"] for c in cats.values())
    resultado, inicio = [], 0.0
    for i, (nome, c) in enumerate(sorted(cats.items(), key=lambda x: x[1]["valor"], reverse=True)):
        pct = c["valor"] / total * 100 if total else 0
        resultado.append({
            "nome": nome, "valor": c["valor"], "pct": pct,
            "cor": PALETA[i % len(PALETA)],
            "inicio": inicio, "fim": inicio + pct,
            "subs": sorted(({"nome": n, "valor": v} for n, v in c["subs"].items()),
                           key=lambda s: s["valor"], reverse=True),
        })
        inicio += pct
    return resultado, total


@app.route("/relatorio")
@login_required
def relatorio():
    uid = session["usuario_id"]
    hoje = date.today()
    modo = "anual" if request.args.get("modo") == "anual" else "mensal"
    aba = "receita" if request.args.get("aba") == "receita" else "despesa"
    p = request.args.get("p", "")

    if modo == "mensal":
        ref = primeiro_dia_do_mes(p)
        chave, tam = ref.strftime("%Y-%m"), 7
        titulo = f"{MESES[ref.month - 1]} de {ref.year}"
        anterior = deslocar_mes(ref, -1).strftime("%Y-%m")
        proximo = deslocar_mes(ref, 1).strftime("%Y-%m") if ref < hoje.replace(day=1) else None
    else:
        try:
            ano = int(p)
        except ValueError:
            ano = hoje.year
        chave, tam, titulo = str(ano), 4, str(ano)
        anterior = str(ano - 1)
        proximo = str(ano + 1) if ano < hoje.year else None

    do_periodo = [t for t in buscar_transacoes(uid) if str(t["data"])[:tam] == chave]
    resumo = calcular_resumo(do_periodo)
    itens, total = agrupar_relatorio(do_periodo, aba)

    return render_template(
        "relatorio.html",
        modo=modo, aba=aba, p=chave, titulo=titulo,
        anterior=anterior, proximo=proximo,
        resumo=resumo, itens=itens, total=total,
    )

# ==========================================================
# DASHBOARD
# ==========================================================

@app.route("/dashboard")
@login_required
def dashboard():
    mes = primeiro_dia_do_mes(request.args.get("mes", ""))
    chave_mes = mes.strftime("%Y-%m")
    mes_atual = date.today().replace(day=1)

    todas = buscar_transacoes(session["usuario_id"])
    do_mes = sorted(
        (t for t in todas if str(t["data"])[:7] == chave_mes),
        key=lambda t: str(t["data"]),
        reverse=True,
    )

    categorias = preparar_categorias(do_mes)
    resumo = calcular_resumo(do_mes)

    contas_lista = listar_contas(session["usuario_id"])

    return render_template(
        "dashboard.html",
        contas_lista=contas_lista,
        vencimentos=[f for f in calcular_fixas(session["usuario_id"]) if not f["paga"]][:3],
        resumo=resumo,
        categorias=categorias,
        total_despesas=resumo["despesas"],
        transacoes=do_mes,
        mes_nome=f"{MESES[mes.month - 1]} de {mes.year}",
        mes_anterior=deslocar_mes(mes, -1).strftime("%Y-%m"),
        mes_proximo=deslocar_mes(mes, 1).strftime("%Y-%m") if mes < mes_atual else None,
        chave_mes=chave_mes,
        saldo_contas=sum(c["saldo"] for c in contas_lista),
    )


# ==========================================================
# NOVA TRANSAÇÃO
# ==========================================================

@app.route("/nova/<tipo>")
@login_required
def tela_nova(tipo):
    if tipo not in ("receita", "despesa", "transferencia"):
        abort(404)
    ontem = (date.today() - timedelta(days=1)).isoformat()
    return render_template("nova.html", tipo=tipo, ontem=ontem)


@app.route("/transacao", methods=["POST"])
@login_required
def nova_transacao():
    uid = session["usuario_id"]
    tipo = request.form.get("tipo", "")
    descricao = request.form.get("descricao", "").strip()
    categoria = request.form.get("categoria", "")
    subcategoria = request.form.get("subcategoria", "").strip() or None
    conta = request.form.get("conta", "")
    observacao = request.form.get("observacao", "").strip()[:200] or None
    data = request.form.get("data", "") or date.today().isoformat()

    voltar = url_for("tela_nova", tipo=tipo) if tipo in ("receita", "despesa") else url_for("dashboard")

    try:
        valor = float(request.form.get("valor", "").replace(",", "."))
        data_valida = date.fromisoformat(data)
    except ValueError:
        flash("Confira o valor e a data informados.", "erro")
        return redirect(voltar)

    contas_validas = [c["nome"] for c in listar_contas(uid)]
    arvore = {c["nome"]: [s["nome"] for s in c["subs"]] for c in listar_categorias(uid)}

    erro = None
    if tipo not in ("receita", "despesa"):
        erro = "Escolha se é receita ou despesa."
    elif not descricao or len(descricao) > 80:
        erro = "A descrição deve ter entre 1 e 80 caracteres."
    elif not math.isfinite(valor) or valor <= 0:
        erro = "O valor precisa ser maior que zero."
    elif categoria not in arvore or conta not in contas_validas:
        erro = "Categoria ou conta inválida."
    elif subcategoria and subcategoria not in arvore[categoria]:
        erro = "Subcategoria inválida."

    if erro:
        flash(erro, "erro")
        return redirect(voltar)

    adicionar_transacao(
        uid, tipo, descricao, round(valor, 2),
        categoria, conta, data_valida.isoformat(),
        subcategoria=subcategoria, observacao=observacao,
    )
    flash("Transação salva.", "sucesso")
    return redirect(url_for("dashboard", mes=data_valida.strftime("%Y-%m")))


@app.route("/transferencia", methods=["POST"])
@login_required
def nova_transferencia():
    uid = session["usuario_id"]
    origem = request.form.get("origem", "")
    destino = request.form.get("destino", "")
    observacao = request.form.get("observacao", "").strip()[:200] or None
    data = request.form.get("data", "") or date.today().isoformat()
    voltar = url_for("tela_nova", tipo="transferencia")

    try:
        valor = float(request.form.get("valor", "").replace(",", "."))
        data_valida = date.fromisoformat(data)
    except ValueError:
        flash("Confira o valor e a data informados.", "erro")
        return redirect(voltar)

    nomes = [c["nome"] for c in listar_contas(uid)]
    if not math.isfinite(valor) or valor <= 0:
        flash("O valor precisa ser maior que zero.", "erro")
    elif origem not in nomes or destino not in nomes:
        flash("Conta inválida.", "erro")
    elif origem == destino:
        flash("Escolha contas diferentes na origem e no destino.", "erro")
    else:
        criar_transferencia(uid, origem, destino, round(valor, 2),
                            data_valida.isoformat(), observacao)
        flash("Transferência realizada.", "sucesso")
        return redirect(url_for("dashboard"))
    return redirect(voltar)

# ==========================================================
# OUTRAS PÁGINAS
# ==========================================================

@app.route("/transacoes")
@login_required
def transacoes():
    tipo = request.args.get("tipo", "")
    lista = sorted(
        buscar_transacoes(session["usuario_id"]),
        key=lambda t: str(t["data"]),
        reverse=True,
    )
    if tipo in ("receita", "despesa"):
        lista = [t for t in lista if t["tipo"] == tipo]
    else:
        tipo = ""
    return render_template("transacoes.html", transacoes=lista, filtro=tipo)


def dinheiro_para_o_mes(usuario_id):
    """(saldo em contas, gasto no mês, disponível para planejar).
    Disponível = saldo atual + o que já foi gasto no mês, ou seja, o que você
    tinha para gastar no início do mês. Assim o teto não encolhe a cada compra."""
    chave_mes = date.today().strftime("%Y-%m")
    saldo = sum(c["saldo"] for c in listar_contas(usuario_id))
    gasto = sum(
        t["valor"] for t in buscar_transacoes(usuario_id)
        if t["tipo"] == "despesa" and str(t["data"])[:7] == chave_mes
    )
    return saldo, gasto, saldo + gasto


def linha_limite(nome, chave, gasto, limite):
    pct = gasto / limite * 100 if limite > 0 else (100 if gasto > 0 else 0)
    return {
        "nome": nome, "chave": chave, "gasto": gasto, "limite": limite,
        "pct": min(pct, 100), "pct_real": pct,
        "estado": "estourou" if pct >= 100 else "alerta" if pct >= 80 else "ok",
    }


@app.route("/planejamento")
@login_required
def planejamento():
    uid = session["usuario_id"]
    hoje = date.today()
    chave_mes = hoje.strftime("%Y-%m")

    gastos, total_gasto = {}, 0
    for t in buscar_transacoes(uid):
        if t["tipo"] == "despesa" and str(t["data"])[:7] == chave_mes:
            gastos[t["categoria"]] = gastos.get(t["categoria"], 0) + t["valor"]
            total_gasto += t["valor"]

    limites = listar_limites(uid)
    fixas = calcular_fixas(uid)
    fixas_pendentes = sum(f["valor"] for f in fixas if not f["paga"])

    total = por_categoria = restantes = None
    por_dia = 0
    if LIMITE_TOTAL in limites:
        limite_total = limites[LIMITE_TOTAL]
        total = linha_limite("Total do mês", LIMITE_TOTAL, total_gasto, limite_total)

        nomes = [n for n in nomes_categorias(uid) if n in limites]
        por_categoria = sorted(
            (linha_limite(n, n, gastos.get(n, 0), limites[n]) for n in nomes),
            key=lambda l: l["pct_real"], reverse=True)

        soma_limites = sum(limites[n] for n in nomes)
        gasto_com_limite = sum(gastos.get(n, 0) for n in nomes)
        restantes = linha_limite(
            "Categorias restantes", None,
            total_gasto - gasto_com_limite, max(limite_total - soma_limites, 0))

        dias_restantes = calendar.monthrange(hoje.year, hoje.month)[1] - hoje.day + 1
        por_dia = max(limite_total - total_gasto, 0) / dias_restantes

    return render_template(
        "planejamento.html",
        mes_nome=MESES[hoje.month - 1],
        total=total,
        por_categoria=por_categoria,
        restantes=restantes,
        por_dia=por_dia,
        fixas=fixas,
        fixas_pendentes=fixas_pendentes,
    )


@app.route("/planejamento/configurar", methods=["GET", "POST"])
@login_required
def configurar_plano():
    uid = session["usuario_id"]
    if request.method == "POST":
        renda = _valor_positivo(request.form.get("renda", ""))
        try:
            economia = float(request.form.get("economia", "0").replace(",", "."))
        except ValueError:
            economia = -1
        if renda is None or not 0 <= economia < 100:
            flash("Informe uma renda maior que zero e uma economia entre 0% e 99%.", "erro")
            return redirect(url_for("configurar_plano"))

        salvar_plano(uid, renda, economia)
        salvar_limite(uid, LIMITE_TOTAL, round(renda * (1 - economia / 100), 2))
        return redirect(url_for("limites_categorias"))

    return render_template("plano_configurar.html", plano=buscar_plano(uid))


@app.route("/planejamento/categorias", methods=["GET", "POST"])
@login_required
def limites_categorias():
    uid = session["usuario_id"]
    limites = listar_limites(uid)
    orcamento = limites.get(LIMITE_TOTAL)
    if orcamento is None:
        return redirect(url_for("configurar_plano"))

    cats = listar_categorias(uid)

    if request.method == "POST":
        novos = {}
        for c in cats:
            texto = request.form.get(f"cat_{c['id']}", "").strip()
            if texto in ("", "0", "0,00", "0.00"):
                continue
            valor = _valor_positivo(texto)
            if valor is None:
                flash(f"Valor inválido em {c['nome']}.", "erro")
                return redirect(url_for("limites_categorias"))
            novos[c["nome"]] = valor

        if round(sum(novos.values()), 2) > round(orcamento, 2):
            flash(f"A soma dos limites passa do orçamento (R$ {brl(orcamento)}).", "erro")
            return redirect(url_for("limites_categorias"))

        for c in cats:
            if c["nome"] in novos:
                salvar_limite(uid, c["nome"], novos[c["nome"]])
            else:
                remover_limite(uid, c["nome"])
        flash("Planejamento salvo.", "sucesso")
        return redirect(url_for("planejamento"))

    return render_template("plano_categorias.html", orcamento=orcamento, cats=cats, limites=limites)

def _valor_positivo(texto):
    try:
        v = float(texto.replace(",", "."))
    except ValueError:
        return None
    return round(v, 2) if math.isfinite(v) and v > 0 else None


@app.route("/limites", methods=["POST"])
@login_required
def definir_limite():
    uid = session["usuario_id"]
    categoria = request.form.get("categoria", "")
    texto = request.form.get("valor", "").strip()

    if categoria != LIMITE_TOTAL and categoria not in nomes_categorias(uid):
        flash("Categoria inválida.", "erro")
    elif texto in ("", "0", "0,00", "0.00"):
        remover_limite(uid, categoria)
        flash("Limite removido.", "sucesso")
    elif (valor := _valor_positivo(texto)) is None:
        flash("Informe um valor maior que zero.", "erro")
    elif categoria == LIMITE_TOTAL and valor > round(dinheiro_para_o_mes(uid)[2], 2):
        disponivel = dinheiro_para_o_mes(uid)[2]
        if disponivel <= 0:
            flash("Você não tem saldo positivo em contas para definir um limite.", "erro")
        else:
            flash(f"O limite não pode passar do dinheiro que você tem para o mês: R$ {brl(disponivel)}.", "erro")
    else:
        salvar_limite(uid, categoria, valor)
        flash("Limite salvo.", "sucesso")
    return redirect(url_for("planejamento"))


@app.route("/limites/remover", methods=["POST"])
@login_required
def apagar_limite():
    remover_limite(session["usuario_id"], request.form.get("categoria", ""))
    flash("Limite removido.", "sucesso")
    return redirect(url_for("planejamento"))


@app.route("/fixas")
@login_required
def fixas_lista():
    fixas = calcular_fixas(session["usuario_id"])
    return render_template(
        "fixas.html",
        fixas=fixas,
        fixas_pendentes=sum(f["valor"] for f in fixas if not f["paga"]),
    )


@app.route("/fixas/nova", methods=["GET", "POST"])
@login_required
def nova_fixa():
    if request.method == "GET":
        return render_template("fixa_nova.html")

    uid = session["usuario_id"]
    descricao = request.form.get("descricao", "").strip()
    categoria = request.form.get("categoria", "")
    conta = request.form.get("conta", "")
    valor = _valor_positivo(request.form.get("valor", ""))
    try:
        dia = int(request.form.get("dia", ""))
    except ValueError:
        dia = 0

    nomes = [c["nome"] for c in listar_contas(uid)]
    if not 1 <= len(descricao) <= 60 or valor is None or not 1 <= dia <= 31 \
            or categoria not in nomes_categorias(uid) or conta not in nomes:
        flash("Confira descrição, valor, dia (1 a 31), categoria e conta.", "erro")
        return redirect(url_for("nova_fixa"))

    criar_fixa(uid, descricao, valor, categoria, conta, dia)
    flash("Despesa fixa adicionada.", "sucesso")
    return redirect(url_for("fixas_lista"))


@app.route("/fixas/<int:fixa_id>/pagar", methods=["POST"])
@login_required
def pagar_fixa_rota(fixa_id):
    fixa = buscar_fixa(session["usuario_id"], fixa_id)
    if fixa is None:
        flash("Despesa não encontrada.", "erro")
    elif pagar_fixa(session["usuario_id"], fixa):
        flash(f"{fixa['descricao']} marcada como paga e lançada nas transações.", "sucesso")
    else:
        flash("Essa despesa já foi paga neste mês.", "erro")
    return redirect(url_for("fixas_lista"))


@app.route("/fixas/<int:fixa_id>/excluir", methods=["POST"])
@login_required
def apagar_fixa(fixa_id):
    excluir_fixa(session["usuario_id"], fixa_id)
    flash("Despesa fixa removida.", "sucesso")
    return redirect(url_for("fixas_lista"))

# ==========================================================
# CATEGORIAS E SUBCATEGORIAS
# ==========================================================

@app.route("/categorias")
@login_required
def categorias():
    return render_template("categorias.html", categorias=listar_categorias(session["usuario_id"]))


@app.route("/categorias/nova", methods=["GET", "POST"])
@login_required
def nova_categoria():
    if request.method == "GET":
        return render_template("categoria_nova.html")

    nome = request.form.get("nome", "").strip()
    if not 1 <= len(nome) <= 30:
        flash("O nome da categoria deve ter entre 1 e 30 caracteres.", "erro")
        return redirect(url_for("nova_categoria"))
    if not criar_categoria(session["usuario_id"], nome):
        flash("Você já tem uma categoria com esse nome.", "erro")
        return redirect(url_for("nova_categoria"))
    flash("Categoria adicionada.", "sucesso")
    return redirect(url_for("categorias"))


@app.route("/categorias/<int:categoria_id>/excluir", methods=["POST"])
@login_required
def apagar_categoria(categoria_id):
    r = excluir_categoria(session["usuario_id"], categoria_id)
    if r == "ok":
        flash("Categoria removida.", "sucesso")
    elif r == "em_uso":
        flash("Essa categoria já tem transações ou despesas fixas e não pode ser removida.", "erro")
    else:
        flash("Categoria não encontrada.", "erro")
    return redirect(url_for("categorias"))


@app.route("/categorias/<int:categoria_id>")
@login_required
def categoria_detalhe(categoria_id):
    cat = next((c for c in listar_categorias(session["usuario_id"])
                if c["id"] == categoria_id), None)
    if cat is None:
        abort(404)
    return render_template("categoria_detalhe.html", cat=cat)


@app.route("/categorias/<int:categoria_id>/sub/nova", methods=["POST"])
@login_required
def nova_subcategoria(categoria_id):
    nome = request.form.get("nome", "").strip()
    if not 1 <= len(nome) <= 30:
        flash("O nome da subcategoria deve ter entre 1 e 30 caracteres.", "erro")
    elif not criar_subcategoria(session["usuario_id"], categoria_id, nome):
        flash("Subcategoria repetida ou categoria inválida.", "erro")
    else:
        flash("Subcategoria adicionada.", "sucesso")
    return redirect(url_for("categoria_detalhe", categoria_id=categoria_id))


@app.route("/categorias/<int:categoria_id>/sub/<int:sub_id>/excluir", methods=["POST"])
@login_required
def apagar_subcategoria(categoria_id, sub_id):
    excluir_subcategoria(session["usuario_id"], sub_id)
    flash("Subcategoria removida.", "sucesso")
    return redirect(url_for("categoria_detalhe", categoria_id=categoria_id))

@app.route("/categorias/sub/rapida", methods=["POST"])
@login_required
def sub_rapida():
    uid = session["usuario_id"]
    nome_cat = request.form.get("categoria", "")
    nome = request.form.get("nome", "").strip()

    cat = next((c for c in listar_categorias(uid) if c["nome"] == nome_cat), None)
    if cat is None:
        return jsonify(ok=False, erro="Categoria inválida."), 400
    if not 1 <= len(nome) <= 30:
        return jsonify(ok=False, erro="Use de 1 a 30 caracteres."), 400
    if not criar_subcategoria(uid, cat["id"], nome):
        return jsonify(ok=False, erro="Essa subcategoria já existe."), 400
    return jsonify(ok=True, nome=nome)

# ==========================================================
# CONTAS, SALÁRIO, MAIS
# ==========================================================

@app.route("/contas")
@login_required
def contas():
    lista = listar_contas(session["usuario_id"])
    return render_template(
        "contas.html",
        contas=lista,
        contas_lista=lista,
        saldo_contas=sum(c["saldo"] for c in lista),
        tipos=TIPOS_CONTA,
        salario=buscar_salario(session["usuario_id"]),
    )


@app.route("/contas/nova", methods=["POST"])
@login_required
def nova_conta():
    nome = request.form.get("nome", "").strip()
    tipo = request.form.get("tipo", "")
    try:
        saldo = float(request.form.get("saldo_inicial", "0").replace(",", ".") or 0)
    except ValueError:
        saldo = float("nan")

    if not 1 <= len(nome) <= 40 or tipo not in TIPOS_CONTA or not math.isfinite(saldo):
        flash("Confira o nome, o tipo e o saldo inicial da conta.", "erro")
    elif not criar_conta(session["usuario_id"], nome, tipo, round(saldo, 2)):
        flash("Você já tem uma conta com esse nome.", "erro")
    else:
        flash("Conta adicionada.", "sucesso")
    return redirect(url_for("contas"))


@app.route("/contas/<int:conta_id>/excluir", methods=["POST"])
@login_required
def apagar_conta(conta_id):
    resultado = excluir_conta(session["usuario_id"], conta_id)
    if resultado == "ok":
        flash("Conta removida.", "sucesso")
    elif resultado == "em_uso":
        flash("Essa conta já tem transações (ou é a conta do salário) e não pode ser removida.", "erro")
    else:
        flash("Conta não encontrada.", "erro")
    return redirect(url_for("contas"))


@app.route("/salario", methods=["POST"])
@login_required
def salvar_salario_rota():
    nomes = [c["nome"] for c in listar_contas(session["usuario_id"])]
    conta = request.form.get("conta", "")
    ativo = request.form.get("ativo") == "on"
    try:
        valor = float(request.form.get("valor", "").replace(",", "."))
        dia = int(request.form.get("dia", ""))
    except ValueError:
        flash("Confira o valor e o dia do salário.", "erro")
        return redirect(url_for("contas") + "#salario")

    if not math.isfinite(valor) or valor <= 0 or not 1 <= dia <= 31 or conta not in nomes:
        flash("Informe um valor maior que zero, um dia de 1 a 31 e uma conta válida.", "erro")
    else:
        salvar_salario(session["usuario_id"], round(valor, 2), dia, conta, ativo)
        flash("Salário salvo. Ele será lançado automaticamente todo mês.", "sucesso")
    return redirect(url_for("contas") + "#salario")


@app.route("/mais")
@login_required
def mais():
    return render_template("mais.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")