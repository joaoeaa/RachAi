/*
 * RachAi · comportamento das telas.
 *
 * Tudo aqui é melhoria progressiva: sem JavaScript os formulários continuam
 * enviando normalmente (só perdem o modal de confirmação, o card do Pix, o
 * copiar e as contas ao vivo da despesa).
 */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function $(sel, root) { return (root || document).querySelector(sel); }

  function openDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  }

  // ---------- copiar ----------
  function copyFrom(button) {
    var input = document.getElementById(button.dataset.copyTarget);
    if (!input) return;
    var label = button.querySelector("[data-copy-label]") || button;
    var original = label.dataset.original || label.textContent;
    label.dataset.original = original;
    var done = function () {
      label.textContent = "Copiado!";
      clearTimeout(button._copyTimer);
      button._copyTimer = setTimeout(function () { label.textContent = original; }, 1800);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(input.value).then(done, function () { input.select(); });
    } else {
      input.focus();
      input.select();
      try { document.execCommand("copy"); done(); } catch (e) { /* o texto fica selecionado */ }
    }
  }

  // ---------- card do Pix ----------
  function openPerson(trigger) {
    var dialog = document.getElementById("person-dialog");
    if (!dialog) return;
    var d = trigger.dataset;
    var pix = d.personPix || "";
    $("[data-person-name]", dialog).textContent = d.personName || "";
    $("[data-person-none-name]", dialog).textContent = d.personName || "";
    var sub = $("[data-person-sub]", dialog);
    sub.textContent = d.personSub || "";
    sub.hidden = !d.personSub;
    var avatar = $("[data-person-avatar]", dialog);
    avatar.textContent = d.personIni || "";
    avatar.className = "avatar avatar-lg av-" + (d.personTone || "1");
    $("#person-dialog-key").value = pix;
    $("[data-person-key-text]", dialog).textContent = pix;
    $("[data-pix-has]", dialog).hidden = !pix;
    $("[data-pix-none]", dialog).hidden = !!pix;
    var copyLabel = $("[data-copy-label]", dialog);
    if (copyLabel && copyLabel.dataset.original) copyLabel.textContent = copyLabel.dataset.original;
    openDialog(dialog);
  }

  // ---------- confirmação (substitui o confirm() do navegador) ----------
  var pending = null;

  function askConfirm(source) {
    var dialog = document.getElementById("confirm-dialog");
    if (!dialog || typeof dialog.showModal !== "function") return false;
    var d = source.dataset;
    var tone = d.confirmTone || "primary";
    $("[data-confirm-title]", dialog).textContent = d.confirm || "Confirmar?";
    $("[data-confirm-body]", dialog).textContent = d.confirmBody || "";
    var ok = $("[data-confirm-ok]", dialog);
    ok.textContent = d.confirmOk || "Confirmar";
    ok.className = "btn " + (tone === "danger" ? "btn-danger" : "btn-primary");
    dialog.querySelectorAll("[data-confirm-icon]").forEach(function (el) {
      el.hidden = el.dataset.confirmIcon !== tone;
    });
    pending = source;
    openDialog(dialog);
    return true;
  }

  function runConfirmed() {
    var source = pending;
    pending = null;
    if (!source) return;
    if (source.tagName === "A") {
      window.location.href = source.href;
      return;
    }
    source.dataset.confirmed = "1";
    if (source.hasAttribute("data-celebrate") && !reduceMotion) {
      source.classList.add("settling");
      var coin = document.querySelector("[data-hero-coin]");
      if (coin && source.hasAttribute("data-mine")) coin.classList.add("joined");
      setTimeout(function () { source.submit(); }, 1000);
    } else {
      source.submit();
    }
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.hasAttribute("data-confirm") || form.dataset.confirmed) return;
    if (askConfirm(form)) event.preventDefault();
  });

  document.addEventListener("click", function (event) {
    var t = event.target;
    var el;
    if ((el = t.closest("[data-copy-target]"))) { copyFrom(el); return; }
    if ((el = t.closest(".person-link"))) { openPerson(el); return; }
    if ((el = t.closest("[data-open-dialog]"))) { openDialog(document.getElementById(el.dataset.openDialog)); return; }
    if ((el = t.closest("[data-close-dialog]"))) { var dlg = el.closest("dialog"); if (dlg) dlg.close(); return; }
    if ((el = t.closest("[data-confirm-ok]"))) { var cd = el.closest("dialog"); if (cd) cd.close(); runConfirmed(); return; }
    if ((el = t.closest("a[data-confirm]"))) { if (askConfirm(el)) event.preventDefault(); return; }
    if ((el = t.closest("[data-toast-close]"))) { dismissToast(el.closest(".toast")); return; }
    if ((el = t.closest("[data-fill-target]"))) {
      var target = document.getElementById(el.dataset.fillTarget);
      if (target) { target.value = el.dataset.fillValue || el.textContent.trim(); target.focus(); }
    }
  });

  // clique no fundo escuro fecha o diálogo
  document.querySelectorAll("dialog").forEach(function (dialog) {
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", function () {
      if (dialog.id === "confirm-dialog") pending = null;
    });
  });

  // ---------- toasts ----------
  function dismissToast(toast) {
    if (!toast || toast.classList.contains("is-leaving")) return;
    toast.classList.add("is-leaving");
    setTimeout(function () { toast.hidden = true; }, 260);
  }
  document.querySelectorAll(".toast").forEach(function (toast) {
    setTimeout(function () { dismissToast(toast); }, 5000);
  });

  // ---------- formulário de despesa: contas ao vivo ----------
  var form = document.getElementById("expense-form");
  if (form) setupExpenseForm(form);

  function setupExpenseForm(form) {
    var amountInput = form.querySelector("#amount");
    var valueInputs = Array.prototype.slice.call(form.querySelectorAll(".split-input-value"));
    var percInputs = Array.prototype.slice.call(form.querySelectorAll(".split-input-perc"));
    var eqCells = Array.prototype.slice.call(form.querySelectorAll("[data-eq-share]"));
    var pcCells = Array.prototype.slice.call(form.querySelectorAll("[data-pc-amount]"));
    var statusMsg = form.querySelector("[data-split-msg]");
    var statusText = form.querySelector("[data-split-text]");
    var statusProgress = form.querySelector("[data-split-progress]");
    var bar = form.querySelector("[data-split-bar]");
    var okIcon = form.querySelector("[data-split-ok]");
    var badIcon = form.querySelector("[data-split-bad]");
    var submit = form.querySelector("[data-expense-submit]");
    var count = eqCells.length;

    // Mesmas regras do servidor (views.py): em valores o ponto é milhar
    // ("1.234,56"); em porcentagem o ponto é decimal ("60.5").
    function parse(v) {
      var s = String(v || "").replace(/\s/g, "").replace(/\./g, "").replace(",", ".");
      var n = parseFloat(s);
      return isNaN(n) ? 0 : n;
    }
    function parsePct(v) {
      var n = parseFloat(String(v || "").replace(/\s/g, "").replace(",", "."));
      return isNaN(n) ? 0 : n;
    }
    function cents(n) { return Math.round(n * 100); }
    function brl(n) {
      var fixed = Math.abs(n).toFixed(2).split(".");
      return "R$ " + fixed[0].replace(/\B(?=(\d{3})+(?!\d))/g, ".") + "," + fixed[1];
    }
    function pct(n) { return String(Math.round(n * 100) / 100).replace(".", ","); }
    function method() {
      var checked = form.querySelector('input[name="split_method"]:checked');
      return checked ? checked.value : "EQUAL";
    }

    function render() {
      var total = parse(amountInput.value);
      var m = method();
      var ok = true, text, progress, width = 100;

      var share = count ? total / count : 0;
      eqCells.forEach(function (cell) { cell.textContent = brl(share); });
      pcCells.forEach(function (cell, i) {
        cell.textContent = "= " + brl(total * parsePct(percInputs[i] && percInputs[i].value) / 100);
      });

      if (m === "UNEQUAL_VALUE") {
        var sum = valueInputs.reduce(function (a, el) { return a + parse(el.value); }, 0);
        var diff = cents(total) - cents(sum);
        ok = diff === 0 && total > 0;
        text = ok ? "Fechou certinho" : (diff > 0 ? "Faltam " + brl(diff / 100) : "Passou " + brl(-diff / 100) + " do total");
        progress = brl(sum) + " de " + brl(total);
        width = total > 0 ? Math.min(100, sum / total * 100) : 0;
        if (diff < 0) width = 100;
      } else if (m === "UNEQUAL_PERCENTAGE") {
        var psum = percInputs.reduce(function (a, el) { return a + parsePct(el.value); }, 0);
        var pdiff = cents(100) - cents(psum);
        ok = pdiff === 0 && total > 0;
        text = ok ? "Fechou 100%" : (pdiff > 0 ? "Faltam " + pct(pdiff / 100) + "%" : "Passou " + pct(-pdiff / 100) + "% de 100");
        progress = pct(psum) + "% de 100%";
        width = pdiff < 0 ? 100 : Math.min(100, psum);
      } else {
        ok = total > 0;
        text = count === 1 ? "Fica tudo com 1 pessoa" : "Dividido igualmente entre " + count + " pessoas";
        progress = brl(total);
      }

      statusText.textContent = text;
      statusProgress.textContent = progress;
      statusMsg.classList.toggle("is-bad", !ok);
      bar.classList.toggle("is-bad", !ok);
      bar.firstElementChild.style.width = width + "%";
      okIcon.hidden = !ok;
      badIcon.hidden = ok;
      if (submit) submit.disabled = !ok && m !== "EQUAL";
    }

    // Com 2 pessoas, preencher uma parte completa a outra (como já era antes).
    function complement(inputs, totalFn, decimals, parser) {
      inputs.forEach(function (input) {
        input.addEventListener("input", function () {
          if (inputs.length === 2) {
            var other = inputs[0] === input ? inputs[1] : inputs[0];
            var rest = Math.max(0, totalFn() - parser(input.value));
            other.value = rest.toFixed(decimals).replace(".", ",");
          }
          render();
        });
      });
    }
    complement(valueInputs, function () { return parse(amountInput.value); }, 2, parse);
    complement(percInputs, function () { return 100; }, 1, parsePct);

    amountInput.addEventListener("input", render);
    // "change" cobre preenchimento automático, colar e valores definidos por script.
    // Fica em cada campo também: um "change" disparado por script não sobe até o form.
    form.addEventListener("change", render);
    valueInputs.concat(percInputs, [amountInput]).forEach(function (el) { el.addEventListener("change", render); });
    render();
  }
})();
