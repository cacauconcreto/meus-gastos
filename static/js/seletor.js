/* Seletor em folha (bottom sheet) no lugar do <select> nativo. */
(function () {
    let fundo, titulo, corpo, formNovo, inputNovo, erroEl;
    let atual = null, focoAnterior = null;

    function criarFolha() {
        fundo = document.createElement("div");
        fundo.className = "sheet-fundo";
        fundo.innerHTML =
            '<div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-titulo">' +
            '  <div class="sheet-alca"></div>' +
            '  <h3 class="sheet-titulo" id="sheet-titulo"></h3>' +
            '  <div class="sheet-corpo"></div>' +
            '  <p class="sheet-erro" hidden></p>' +
            '  <form class="sheet-novo" hidden>' +
            '    <input type="text" maxlength="30" autocomplete="off">' +
            '    <button type="submit" class="btn-principal">Adicionar</button>' +
            '  </form>' +
            '</div>';
        document.body.appendChild(fundo);

        titulo = fundo.querySelector(".sheet-titulo");
        corpo = fundo.querySelector(".sheet-corpo");
        erroEl = fundo.querySelector(".sheet-erro");
        formNovo = fundo.querySelector(".sheet-novo");
        inputNovo = formNovo.querySelector("input");

        fundo.addEventListener("click", e => { if (e.target === fundo) fechar(); });
        document.addEventListener("keydown", e => { if (e.key === "Escape" && atual) fechar(); });

        formNovo.addEventListener("submit", async e => {
            e.preventDefault();
            const nome = inputNovo.value.trim();
            if (!nome || !atual) return;
            const sel = atual;
            try {
                const valor = await sel.criar(nome);
                escolher(sel, valor);
            } catch (err) {
                erroEl.textContent = err.message || "Não foi possível adicionar.";
                erroEl.hidden = false;
            }
        });
    }

    function tituloDe(sel) {
        if (sel.dataset.titulo) return sel.dataset.titulo;
        if (sel.getAttribute("aria-label")) return sel.getAttribute("aria-label");
        const l = sel.id && document.querySelector('label[for="' + sel.id + '"]');
        return l ? l.textContent.trim() : "Escolher";
    }

    function atualizarBtn(sel) {
        const opt = sel.options[sel.selectedIndex];
        const vazio = !opt || opt.value === "";
        const txt = vazio ? (sel.dataset.vazio || (opt ? opt.text : "Escolher")) : opt.text;
        sel._btn.querySelector(".seletor-txt").textContent = txt;
        sel._btn.classList.toggle("vazio", vazio);
    }

    function desenhar(sel) {
        const chips = sel.dataset.modo === "chips";
        corpo.className = "sheet-corpo " + (chips ? "chips" : "lista");
        corpo.textContent = "";

        if (!sel.options.length) {
            const p = document.createElement("p");
            p.className = "sheet-vazio";
            p.textContent = "Nada por aqui ainda.";
            corpo.appendChild(p);
            return;
        }

        Array.from(sel.options).forEach(opt => {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "opcao" + (opt.value === "" ? " neutra" : "");
            b.setAttribute("aria-selected", opt.value === sel.value);

            if (!chips) {
                const av = document.createElement("span");
                av.className = "opcao-av";
                av.textContent = (opt.text.replace(/^(Sai de|Vai para):\s*/, "")[0] || "?").toUpperCase();
                b.appendChild(av);
            }
            const t = document.createElement("span");
            t.className = "opcao-txt";
            t.textContent = opt.text;
            b.appendChild(t);

            b.addEventListener("click", () => escolher(sel, opt.value));
            corpo.appendChild(b);
        });
    }

    function abrir(sel) {
        if (!fundo) criarFolha();
        atual = sel;
        focoAnterior = document.activeElement;
        titulo.textContent = tituloDe(sel);
        erroEl.hidden = true;
        desenhar(sel);

        const podeCriar = typeof sel.criar === "function";
        formNovo.hidden = !podeCriar;
        inputNovo.value = "";
        inputNovo.placeholder = sel.dataset.novoTexto || "Nova opção";

        fundo.classList.add("aberto");
        document.body.style.overflow = "hidden";

        const marcado = corpo.querySelector('[aria-selected="true"]');
        if (marcado) { marcado.focus({ preventScroll: true }); marcado.scrollIntoView({ block: "center" }); }
    }

    function fechar() {
        if (!fundo) return;
        fundo.classList.remove("aberto");
        document.body.style.overflow = "";
        atual = null;
        if (focoAnterior && focoAnterior.focus) focoAnterior.focus({ preventScroll: true });
    }

    function escolher(sel, valor) {
        sel.value = valor;
        atualizarBtn(sel);
        fechar();
        sel.dispatchEvent(new Event("change", { bubbles: true }));

        // Fluxo em sequência: categoria -> subcategoria
        const prox = sel.dataset.proximo && document.querySelector(sel.dataset.proximo);
        if (prox) setTimeout(() => abrir(prox), 200);
    }

    function montar(sel) {
        if (sel.dataset.montado) return;
        sel.dataset.montado = "1";

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "seletor-btn";
        btn.innerHTML = '<span class="seletor-txt"></span>' +
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
        btn.setAttribute("aria-haspopup", "dialog");
        btn.setAttribute("aria-label", tituloDe(sel));

        sel.insertAdjacentElement("afterend", btn);
        sel.style.display = "none";
        sel._btn = btn;
        sel._atualizar = () => atualizarBtn(sel);

        btn.addEventListener("click", () => abrir(sel));
        sel.addEventListener("change", () => atualizarBtn(sel));
        atualizarBtn(sel);
    }

    document.querySelectorAll(".nova-form select, .form-padrao select").forEach(montar);
    window.Seletor = { abrir, montar };
})();