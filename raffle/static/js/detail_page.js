(function () {
  "use strict";

  console.log("[detail_page] init");

  const priceEl = document.getElementById("mp-config");
  const PRICE = Number(priceEl?.dataset.price || "0");

  const grid = document.getElementById("numbers-grid");

  const selCount = document.getElementById("selected-count");
  const selList = document.getElementById("selected-list");
  const selTotal = document.getElementById("selected-total");

  const buyerName = document.getElementById("buyer_name");
  const buyerEmail = document.getElementById("buyer_email");
  const buyerPhone = document.getElementById("buyer_phone");

  // Set global para que lo use checkout_pro.js
  const selected = new Set();
  window.selected = selected;
  console.log("[detail_page] window.selected inicializado");

  function refreshSummary() {
    const arr = Array.from(selected).sort((a, b) => a - b);
    selCount.textContent = String(arr.length);
    selList.textContent = arr.length ? arr.join(", ") : "—";

    const total = PRICE * arr.length;
    selTotal.textContent = String(total);

    console.log("[detail_page] refreshSummary -> count:", arr.length);

    // Avisar al módulo de pago que algo cambió
    if (typeof window.updateWalletIfReady === "function") {
      console.log("[detail_page] llamando updateWalletIfReady()");
      window.updateWalletIfReady();
    } else {
      console.log("[detail_page] updateWalletIfReady aún no definido");
    }
  }

  function toggleSelected(btn) {
    const n = Number(btn.dataset.number);
    if (!n) return;

    if (selected.has(n)) {
      selected.delete(n);
      btn.classList.remove("bg-blue-600", "text-white", "border-blue-600");
      btn.classList.add("bg-white");
    } else {
      selected.add(n);
      btn.classList.remove("bg-white");
      btn.classList.add("bg-blue-600", "text-white", "border-blue-600");
    }
    console.log("[detail_page] toggleSelected -> seleccionado", n, "size:", selected.size);
    refreshSummary();
  }

  // Click en la grilla (delegado)
  grid?.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".number-btn");
    if (!btn || btn.disabled) return;
    toggleSelected(btn);
  });

  // Cambios en datos comprador → también disparan updateWalletIfReady
  [buyerName, buyerEmail, buyerPhone].forEach((el) => {
    el?.addEventListener("input", () => {
      console.log("[detail_page] cambio en input comprador");
      refreshSummary();
    });
  });

  // Cuando htmx cambia de página en la grilla, re-marcar seleccionados
  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.target && e.target.id === "numbers-grid") {
      const buttons = e.target.querySelectorAll(".number-btn");
      buttons.forEach((btn) => {
        const n = Number(btn.dataset.number);
        if (selected.has(n)) {
          btn.classList.remove("bg-white");
          btn.classList.add("bg-blue-600", "text-white", "border-blue-600");
        }
      });
    }
  });

  // Init
  refreshSummary();
})();
