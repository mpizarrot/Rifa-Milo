(function () {
  "use strict";

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  }
  const csrftoken = getCookie("csrftoken");

  const priceEl = document.getElementById("mp-config");
  const PRICE = Number(priceEl?.dataset.price || "0");

  const grid = document.getElementById("numbers-grid");

  const selCount = document.getElementById("selected-count");
  const selList = document.getElementById("selected-list");
  const selTotal = document.getElementById("selected-total");

  const buyerName = document.getElementById("buyer_name");
  const buyerEmail = document.getElementById("buyer_email");
  const buyerPhone = document.getElementById("buyer_phone");

  const transferSection = document.getElementById("transfer-section");
  const transferBtn = document.getElementById("transfer-btn");
  const transferMsg = document.getElementById("transfer-message");

  // Si no estamos en la página de la rifa (no hay grilla ni resumen), salimos
  if (!grid || !selCount || !selList || !selTotal) {
    console.warn("[detail_page] No se encontraron elementos de selección; no se inicializa script.");
    return;
  }

  // Set global para que lo use checkout_pro.js
  const selected = new Set();
  window.selected = selected;

  function refreshSummary() {
    if (!selCount || !selList || !selTotal) {
      return;
    }
    
    const arr = Array.from(selected).sort((a, b) => a - b);
    selCount.textContent = arr.length;
    selList.textContent = arr.length ? arr.join(", ") : "—";
    const total = PRICE * arr.length;
    selTotal.textContent = total.toLocaleString("es-CL");

    const name = buyerName?.value.trim() || "";
    const email = buyerEmail?.value.trim() || "";
    const hasSelection = arr.length > 0;
    const hasBuyer = name && email && email.includes("@");
    const readyForPayment = hasSelection && hasBuyer;

    // 🔹 SIEMPRE avisamos a checkout_pro que algo cambió
    if (typeof window.updateWalletIfReady === "function") {
      window.updateWalletIfReady();
    }

    // 🔹 Transferencia: opcional que dependa de readyForPayment
    if (transferSection) {
      if (readyForPayment) {
        transferSection.classList.remove("hidden");
      } else {
        transferSection.classList.add("hidden");
        transferMsg.textContent = "";
      }
    }
  }


  function toggleSelected(btn) {
    const n = Number(btn.dataset.number);
    if (!n) return;

    if (selected.has(n)) {
      selected.delete(n);
      btn.classList.remove("bg-blue-600", "text-white", "border-blue-600");
      btn.classList.add("bg-white", "text-gray-800");
    } else {
      selected.add(n);
      btn.classList.remove("bg-white", "text-gray-800");
      btn.classList.add("bg-blue-600", "text-white", "border-blue-600");
    }
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

    async function reserveByTransfer() {
    if (!window.selected || !(window.selected instanceof Set)) {
      alert("Error interno: selección no disponible.");
      return;
    }

    const numbers = Array.from(window.selected).sort((a, b) => a - b);
    const name = buyerName?.value.trim();
    const email = buyerEmail?.value.trim();
    const phone = buyerPhone?.value.trim();

    if (!numbers.length) {
      alert("Primero selecciona al menos un número.");
      return;
    }
    if (!name || !email || !email.includes("@")) {
      alert("Debes ingresar tu nombre y un correo válido.");
      return;
    }
    if (numbers.length === 0) {
      transferMsg.textContent = "No has seleccionado números.";
      return;
    }
    if (numbers.length > 50) {
      transferMsg.textContent = "No puedes reservar más de 50 números por transferencia.";
      return;
    }

    transferMsg.textContent = "Creando reserva para transferencia...";

    const resp = await fetch("/transfer/reserve/", {
      method: "POST",
      headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrftoken || "",
        },
        body: JSON.stringify({
          chosen_numbers: numbers,
          buyer: { name, email, phone },
      }),
    });

    const data = await resp.json().catch(() => ({}));

    if (!resp.ok || !data.ok) {
      const msg = data.error || "No se pudo crear la reserva.";
      transferMsg.textContent = msg;
      return;
    }

    if (data.redirect_url) {
      window.location.href = data.redirect_url;
      return;
    }

    window.selected.clear();
    refreshSummary();
    document.body.dispatchEvent(new Event("refreshGrid"));

    const until = data.reserved_until || "";
    transferMsg.textContent =
      `Tus números han sido reservados para transferencia. ` +
      `Tienes 12 horas para realizarla. ` +
      (until ? `Reserva válida hasta: ${until}` : "");

  }

  transferBtn?.addEventListener("click", reserveByTransfer);

  // Init
  refreshSummary();
})();
