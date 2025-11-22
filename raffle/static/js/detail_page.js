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
      // Quitar de la selección
      selected.delete(n);
      btn.classList.remove(
        "bg-blue-600",
        "text-white",
        "border-blue-600",
        "is-selected",
      );
      btn.classList.add("bg-white", "text-gray-800");
    } else {
      // Agregar a la selección
      selected.add(n);
      btn.classList.remove("bg-white", "text-gray-800");
      btn.classList.add(
        "bg-blue-600",
        "text-white",
        "border-blue-600",
        "is-selected",
      );
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
          btn.classList.remove("bg-white", "text-gray-800");
          btn.classList.add(
            "bg-blue-600",
            "text-white",
            "border-blue-600",
            "is-selected",
          );
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
    if (numbers.length > 50) {
      transferMsg.textContent = "No puedes reservar más de 50 números por transferencia.";
      return;
    }

    transferMsg.textContent = "Creando reserva para transferencia...";

    let resp;
    let data = null;

    try {
      resp = await fetch("/transfer/reserve/", {
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
    } catch (e) {
      console.error("Error de red en reserva por transferencia:", e);
      transferMsg.textContent = "No se pudo contactar al servidor. Intenta nuevamente.";
      return;
    }

    try {
      data = await resp.json();
    } catch (e) {
      data = null;
    }

    // Si el backend devolvió error (400, 409, 500, etc.)
    if (!resp.ok || !data || data.ok === false) {
      const msg =
        (data && data.error) ||
        "No se pudo crear la reserva. Verifica tus datos o intenta nuevamente.";
      transferMsg.textContent = msg;
      return;
    }

    // Éxito: el backend creó el Payment gateway="transfer"
    window.selected.clear();
    refreshSummary();
    document.body.dispatchEvent(new Event("refreshGrid"));

    const until = data.reserved_until || "";
    transferMsg.textContent =
      `Tus números han sido reservados para transferencia. ` +
      `Tienes 12 horas para realizarla. ` +
      (until ? `Reserva válida hasta: ${until}` : "");

    if (data.redirect_url) {
      // Redirigir después de un pequeño delay, solo para que el usuario vea el mensaje
      setTimeout(() => {
        window.location.href = data.redirect_url;
      }, 1500);
    }
  }

  transferBtn?.addEventListener("click", reserveByTransfer);

  // Init
  refreshSummary();
})();
