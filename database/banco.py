import os
import calendar
import sqlite3
from datetime import date

BANCO = os.environ.get("BANCO_PATH", "banco.db")


def conectar():
    conexao = sqlite3.connect(BANCO)
    conexao.row_factory = sqlite3.Row
    return conexao


def criar_banco():
    conexao = conectar()
    c = conexao.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            senha_hash TEXT NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

    c.execute("""
        CREATE TABLE IF NOT EXISTS transacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            categoria TEXT NOT NULL,
            conta TEXT DEFAULT 'Carteira',
            data TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )""")

    # Migração: marca transações geradas automaticamente (ex.: salário)
    colunas = [col["name"] for col in c.execute("PRAGMA table_info(transacoes)")]
    if "origem" not in colunas:
        c.execute("ALTER TABLE transacoes ADD COLUMN origem TEXT")

    c.execute("""
        CREATE TABLE IF NOT EXISTS contas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'corrente',
            saldo_inicial REAL NOT NULL DEFAULT 0,
            UNIQUE (usuario_id, nome),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )""")

    c.execute("""
        CREATE TABLE IF NOT EXISTS salario (
            usuario_id INTEGER PRIMARY KEY,
            valor REAL NOT NULL,
            dia INTEGER NOT NULL,
            conta TEXT NOT NULL,
            ativo INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )""")

    c.execute("""
        CREATE TABLE IF NOT EXISTS limites (
            usuario_id INTEGER NOT NULL,
            categoria TEXT NOT NULL,          -- '*' = limite total do mês
            valor REAL NOT NULL,
            PRIMARY KEY (usuario_id, categoria),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )""")

    c.execute("""
        CREATE TABLE IF NOT EXISTS despesas_fixas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            categoria TEXT NOT NULL,
            conta TEXT NOT NULL,
            dia INTEGER NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )""")

    conexao.commit()
    conexao.close()


# ---------------------------------------------------------- usuários

def cadastrar_usuario(nome, email, senha_hash):
    conexao = conectar()
    try:
        conexao.execute(
            "INSERT INTO usuarios (nome, email, senha_hash) VALUES (?, ?, ?)",
            (nome, email, senha_hash))
        conexao.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conexao.close()


def buscar_usuario_email(email):
    conexao = conectar()
    usuario = conexao.execute(
        "SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
    conexao.close()
    return usuario


# ------------------------------------------------------- transações

def adicionar_transacao(usuario_id, tipo, descricao, valor, categoria, conta, data, origem=None):
    conexao = conectar()
    conexao.execute("""
        INSERT INTO transacoes
        (usuario_id, tipo, descricao, valor, categoria, conta, data, origem)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (usuario_id, tipo, descricao, valor, categoria, conta, data, origem))
    conexao.commit()
    conexao.close()


def buscar_transacoes(usuario_id):
    conexao = conectar()
    linhas = conexao.execute("""
        SELECT * FROM transacoes WHERE usuario_id = ?
        ORDER BY data DESC, id DESC
    """, (usuario_id,)).fetchall()
    conexao.close()
    return linhas


def resumo_financeiro(usuario_id):
    conexao = conectar()
    soma = lambda tipo: conexao.execute(
        "SELECT COALESCE(SUM(valor), 0) FROM transacoes WHERE usuario_id = ? AND tipo = ?",
        (usuario_id, tipo)).fetchone()[0]
    receitas, despesas = soma("receita"), soma("despesa")
    conexao.close()
    return {"receitas": receitas, "despesas": despesas, "saldo": receitas - despesas}


def despesas_por_categoria(usuario_id):
    conexao = conectar()
    dados = conexao.execute("""
        SELECT categoria, SUM(valor) AS total FROM transacoes
        WHERE usuario_id = ? AND tipo = 'despesa'
        GROUP BY categoria ORDER BY total DESC
    """, (usuario_id,)).fetchall()
    conexao.close()
    return dados


# ------------------------------------------------------------ contas

def listar_contas(usuario_id):
    """Contas do usuário com saldo atual. Na primeira vez, cria as contas
    a partir das que já aparecem nas transações antigas (ou só 'Carteira')."""
    conexao = conectar()

    if not conexao.execute(
            "SELECT 1 FROM contas WHERE usuario_id = ?", (usuario_id,)).fetchone():
        nomes = [r[0] for r in conexao.execute(
            "SELECT DISTINCT conta FROM transacoes WHERE usuario_id = ? AND conta IS NOT NULL",
            (usuario_id,))]
        for nome in (nomes or ["Carteira"]):
            conexao.execute(
                "INSERT OR IGNORE INTO contas (usuario_id, nome, tipo) VALUES (?, ?, ?)",
                (usuario_id, nome, "carteira" if nome == "Carteira" else "corrente"))
        conexao.commit()

    linhas = conexao.execute("""
        SELECT c.id, c.nome, c.tipo, c.saldo_inicial,
               c.saldo_inicial + COALESCE(SUM(
                   CASE t.tipo WHEN 'receita' THEN t.valor
                               WHEN 'despesa' THEN -t.valor END), 0) AS saldo
        FROM contas c
        LEFT JOIN transacoes t
               ON t.usuario_id = c.usuario_id AND t.conta = c.nome
        WHERE c.usuario_id = ?
        GROUP BY c.id ORDER BY c.nome
    """, (usuario_id,)).fetchall()
    conexao.close()
    return linhas


def criar_conta(usuario_id, nome, tipo, saldo_inicial):
    conexao = conectar()
    try:
        conexao.execute(
            "INSERT INTO contas (usuario_id, nome, tipo, saldo_inicial) VALUES (?, ?, ?, ?)",
            (usuario_id, nome, tipo, saldo_inicial))
        conexao.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conexao.close()


def excluir_conta(usuario_id, conta_id):
    """Só exclui contas sem transações. Retorna 'ok', 'em_uso' ou 'inexistente'."""
    conexao = conectar()
    conta = conexao.execute(
        "SELECT nome FROM contas WHERE id = ? AND usuario_id = ?",
        (conta_id, usuario_id)).fetchone()
    if conta is None:
        conexao.close()
        return "inexistente"
    em_uso = conexao.execute(
        "SELECT 1 FROM transacoes WHERE usuario_id = ? AND conta = ? LIMIT 1",
        (usuario_id, conta["nome"])).fetchone()
    em_uso = em_uso or conexao.execute(
        "SELECT 1 FROM salario WHERE usuario_id = ? AND conta = ?",
        (usuario_id, conta["nome"])).fetchone()
    em_uso = em_uso or conexao.execute(
        "SELECT 1 FROM despesas_fixas WHERE usuario_id = ? AND conta = ?",
        (usuario_id, conta["nome"])).fetchone()
    if em_uso:
        conexao.close()
        return "em_uso"
    conexao.execute("DELETE FROM contas WHERE id = ? AND usuario_id = ?", (conta_id, usuario_id))
    conexao.commit()
    conexao.close()
    return "ok"


# ----------------------------------------------------------- salário

def buscar_salario(usuario_id):
    conexao = conectar()
    s = conexao.execute("SELECT * FROM salario WHERE usuario_id = ?", (usuario_id,)).fetchone()
    conexao.close()
    return s


def salvar_salario(usuario_id, valor, dia, conta, ativo):
    conexao = conectar()
    conexao.execute("""
        INSERT INTO salario (usuario_id, valor, dia, conta, ativo)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(usuario_id) DO UPDATE SET
            valor = excluded.valor, dia = excluded.dia,
            conta = excluded.conta, ativo = excluded.ativo
    """, (usuario_id, valor, dia, conta, 1 if ativo else 0))
    conexao.commit()
    conexao.close()


def aplicar_salario(usuario_id, hoje=None):
    """Lança o salário do mês quando o dia de recebimento chega.
    Só lança uma vez por mês (controlado pela coluna 'origem')."""
    hoje = hoje or date.today()
    conexao = conectar()
    s = conexao.execute(
        "SELECT * FROM salario WHERE usuario_id = ? AND ativo = 1", (usuario_id,)).fetchone()

    if s:
        ultimo_dia = calendar.monthrange(hoje.year, hoje.month)[1]
        dia = min(s["dia"], ultimo_dia)          # dia 31 em mês curto -> último dia
        origem = f"salario:{hoje:%Y-%m}"
        ja_lancado = conexao.execute(
            "SELECT 1 FROM transacoes WHERE usuario_id = ? AND origem = ?",
            (usuario_id, origem)).fetchone()

        if hoje.day >= dia and not ja_lancado:
            conexao.execute("""
                INSERT INTO transacoes
                (usuario_id, tipo, descricao, valor, categoria, conta, data, origem)
                VALUES (?, 'receita', 'Salário', ?, 'Salário', ?, ?, ?)
            """, (usuario_id, s["valor"], s["conta"],
                  date(hoje.year, hoje.month, dia).isoformat(), origem))
            conexao.commit()
    conexao.close()


# ------------------------------------------------------------ limites

LIMITE_TOTAL = "*"


def listar_limites(usuario_id):
    conexao = conectar()
    linhas = conexao.execute(
        "SELECT categoria, valor FROM limites WHERE usuario_id = ?", (usuario_id,)).fetchall()
    conexao.close()
    return {l["categoria"]: l["valor"] for l in linhas}


def salvar_limite(usuario_id, categoria, valor):
    conexao = conectar()
    conexao.execute("""
        INSERT INTO limites (usuario_id, categoria, valor) VALUES (?, ?, ?)
        ON CONFLICT(usuario_id, categoria) DO UPDATE SET valor = excluded.valor
    """, (usuario_id, categoria, valor))
    conexao.commit()
    conexao.close()


def remover_limite(usuario_id, categoria):
    conexao = conectar()
    conexao.execute(
        "DELETE FROM limites WHERE usuario_id = ? AND categoria = ?", (usuario_id, categoria))
    conexao.commit()
    conexao.close()


# ---------------------------------------------------- despesas fixas

def listar_fixas(usuario_id):
    conexao = conectar()
    linhas = conexao.execute(
        "SELECT * FROM despesas_fixas WHERE usuario_id = ? ORDER BY dia, descricao",
        (usuario_id,)).fetchall()
    conexao.close()
    return linhas


def buscar_fixa(usuario_id, fixa_id):
    conexao = conectar()
    f = conexao.execute(
        "SELECT * FROM despesas_fixas WHERE id = ? AND usuario_id = ?",
        (fixa_id, usuario_id)).fetchone()
    conexao.close()
    return f


def criar_fixa(usuario_id, descricao, valor, categoria, conta, dia):
    conexao = conectar()
    conexao.execute("""
        INSERT INTO despesas_fixas (usuario_id, descricao, valor, categoria, conta, dia)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (usuario_id, descricao, valor, categoria, conta, dia))
    conexao.commit()
    conexao.close()


def excluir_fixa(usuario_id, fixa_id):
    conexao = conectar()
    conexao.execute(
        "DELETE FROM despesas_fixas WHERE id = ? AND usuario_id = ?", (fixa_id, usuario_id))
    conexao.commit()
    conexao.close()


def fixas_pagas(usuario_id, chave_mes):
    """Ids das despesas fixas já pagas no mês (formato 'YYYY-MM')."""
    conexao = conectar()
    linhas = conexao.execute(
        "SELECT origem FROM transacoes WHERE usuario_id = ? AND origem LIKE ?",
        (usuario_id, f"fixa:%:{chave_mes}")).fetchall()
    conexao.close()
    return {int(l["origem"].split(":")[1]) for l in linhas}


def pagar_fixa(usuario_id, fixa, hoje=None):
    """Registra o pagamento do mês como despesa. Retorna False se já estava paga."""
    hoje = hoje or date.today()
    chave = hoje.strftime("%Y-%m")
    if fixa["id"] in fixas_pagas(usuario_id, chave):
        return False
    adicionar_transacao(
        usuario_id, "despesa", fixa["descricao"], fixa["valor"], fixa["categoria"],
        fixa["conta"], hoje.isoformat(), origem=f"fixa:{fixa['id']}:{chave}")
    return True