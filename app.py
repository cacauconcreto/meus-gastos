import os
import math
import calendar
from datetime import date
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, flash, session, g
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
)

app = Flask(__name__)

# Em produção, defina a variável de ambiente SECRET_KEY.
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-por-uma-chave-secreta")

criar_banco()

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]
CATEGORIAS = ["Alimentação", "Transporte", "Casa", "Educação", "Saúde", "Lazer", "Outros"]
TIPOS_CONTA = {
    "corrente": "Conta corrente",
    "poupanca": "Poupança",
    "carteira": "Carteira",
    "investimento": "Investimentos",
}
CORES = ["#7138F2", "#8B5CF6", "#A78BFA", "#C4B5FD", "#DDD6FE", "#EDE9FE"]


# ==========================================================
# FILTROS DE TEMPLATE
# ==========================================================

@app.template_filter("brl")
def brl(valor):
    """1234.5 -> 1.234,50"""
    texto = f"{float(valor or 0):,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("data_br")
def data_br(texto):
    """2026-11-05 -> 05/11"""
    try:
        return date.fromisoformat(texto).strftime("%d/%m")
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
    return {
        "usuario": usuario,
        "categorias_opcoes": CATEGORIAS,
        "contas_opcoes": contas,
        "tipos_conta": TIPOS_CONTA,
        "hoje": date.today().isoformat(),
    }


# ==========================================================
# PÁGINAS PÚBLICAS
# ==========================================================

@app.route("/")
def inicio():
    return render_template("inicio.html")


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

@app.route("/transacao", methods=["POST"])
@login_required
def nova_transacao():
    tipo = request.form.get("tipo", "")
    descricao = request.form.get("descricao", "").strip()
    categoria = request.form.get("categoria", "")
    conta = request.form.get("conta", "")
    data = request.form.get("data", "") or date.today().isoformat()

    try:
        valor = float(request.form.get("valor", "").replace(",", "."))
        data_valida = date.fromisoformat(data)
    except ValueError:
        flash("Confira o valor e a data informados.", "erro")
        return redirect(url_for("dashboard"))

    contas_validas = [c["nome"] for c in listar_contas(session["usuario_id"])]
    erro = None
    if tipo not in ("receita", "despesa"):
        erro = "Escolha se é receita ou despesa."
    elif not descricao or len(descricao) > 80:
        erro = "A descrição deve ter entre 1 e 80 caracteres."
    elif not math.isfinite(valor) or valor <= 0:
        erro = "O valor precisa ser maior que zero."
    elif categoria not in CATEGORIAS or conta not in contas_validas:
        erro = "Categoria ou conta inválida."

    if erro:
        flash(erro, "erro")
        return redirect(url_for("dashboard"))

    adicionar_transacao(
        session["usuario_id"], tipo, descricao, round(valor, 2),
        categoria, conta, data_valida.isoformat(),
    )
    flash("Transação salva.", "sucesso")

    # Abre o mês da transação para o usuário vê-la na lista
    return redirect(url_for("dashboard", mes=data_valida.strftime("%Y-%m")))


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
    pct = gasto / limite * 100
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
    saldo_total, _, disponivel = dinheiro_para_o_mes(uid)

    return render_template(
        "planejamento.html",
        saldo_total=saldo_total,
        disponivel=disponivel,
        mes_nome=MESES[hoje.month - 1],
        total=linha_limite("Total do mês", LIMITE_TOTAL, total_gasto, limites[LIMITE_TOTAL])
              if LIMITE_TOTAL in limites else None,
        por_categoria=sorted(
            (linha_limite(c, c, gastos.get(c, 0), limites[c]) for c in CATEGORIAS if c in limites),
            key=lambda l: l["pct_real"], reverse=True),
        fixas=fixas,
        fixas_pendentes=sum(f["valor"] for f in fixas if not f["paga"]),
    )


def _valor_positivo(texto):
    try:
        v = float(texto.replace(",", "."))
    except ValueError:
        return None
    return round(v, 2) if math.isfinite(v) and v > 0 else None


@app.route("/limites", methods=["POST"])
@login_required
def definir_limite():
    categoria = request.form.get("categoria", "")
    texto = request.form.get("valor", "").strip()

    if categoria != LIMITE_TOTAL and categoria not in CATEGORIAS:
        flash("Categoria inválida.", "erro")
    elif texto in ("", "0", "0,00", "0.00"):
        remover_limite(session["usuario_id"], categoria)
        flash("Limite removido.", "sucesso")
    elif (valor := _valor_positivo(texto)) is None:
        flash("Informe um valor maior que zero.", "erro")
    elif categoria == LIMITE_TOTAL and valor > round(dinheiro_para_o_mes(session["usuario_id"])[2], 2):
        disponivel = dinheiro_para_o_mes(session["usuario_id"])[2]
        if disponivel <= 0:
            flash("Você não tem saldo positivo em contas para definir um limite.", "erro")
        else:
            flash(f"O limite não pode passar do dinheiro que você tem para o mês: R$ {brl(disponivel)}.", "erro")
    else:
        salvar_limite(session["usuario_id"], categoria, valor)
        flash("Limite salvo.", "sucesso")
    return redirect(url_for("planejamento"))


@app.route("/limites/remover", methods=["POST"])
@login_required
def apagar_limite():
    remover_limite(session["usuario_id"], request.form.get("categoria", ""))
    flash("Limite removido.", "sucesso")
    return redirect(url_for("planejamento"))


@app.route("/fixas/nova", methods=["POST"])
@login_required
def nova_fixa():
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
            or categoria not in CATEGORIAS or conta not in nomes:
        flash("Confira descrição, valor, dia (1 a 31), categoria e conta.", "erro")
    else:
        criar_fixa(uid, descricao, valor, categoria, conta, dia)
        flash("Despesa fixa adicionada.", "sucesso")
    return redirect(url_for("planejamento") + "#fixas")


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
    return redirect(url_for("planejamento") + "#fixas")


@app.route("/fixas/<int:fixa_id>/excluir", methods=["POST"])
@login_required
def apagar_fixa(fixa_id):
    excluir_fixa(session["usuario_id"], fixa_id)
    flash("Despesa fixa removida.", "sucesso")
    return redirect(url_for("planejamento") + "#fixas")


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
    return redirect(url_for("inicio"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")