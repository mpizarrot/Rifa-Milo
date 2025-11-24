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
  const globalLoader = document.getElementById("global-loader");

  function showLoader() {
    if (globalLoader) globalLoader.classList.remove("hidden");
  }

  function hideLoader() {
    if (globalLoader) globalLoader.classList.add("hidden");
  }

    function pageHasFreeNumbers(root) {
    if (!root) return true; // si no hay grid, no hacemos nada

    const buttons = root.querySelectorAll(".number-btn");
    if (!buttons.length) return true; // si no hay botones, tampoco saltamos

    // Consideramos que un número está disponible si el botón NO está disabled
    // (ajusta esta lógica si tienes otra clase tipo .is-taken)
    return Array.from(buttons).some((btn) => !btn.disabled);
  }

  function autoSkipSoldOut(root) {
    if (!root) return;

    const hasFree = pageHasFreeNumbers(root);
    if (hasFree) return; // ya hay números disponibles en esta página

    // No hay números disponibles → buscamos el botón "Siguiente"
    const nextBtn = document.querySelector("[data-grid-next]");
    if (nextBtn && !nextBtn.disabled) {
      // Esto hará que HTMX cargue la siguiente página
      nextBtn.click();
    }
  }

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
      // Después de cargar una nueva página de números, saltar si está llena
      autoSkipSoldOut(e.target);
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

    // Mostrar loader y deshabilitar botón
    if (transferBtn) transferBtn.disabled = true;
    if (transferMsg) {
      transferMsg.textContent = "Creando reserva para transferencia...";
    }
    showLoader();

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
      if (transferMsg) {
        transferMsg.textContent =
          "No se pudo contactar al servidor. Intenta nuevamente.";
      }
      hideLoader();
      if (transferBtn) transferBtn.disabled = false;
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
      if (transferMsg) transferMsg.textContent = msg;

      hideLoader();
      if (transferBtn) transferBtn.disabled = false;
      return;
    }

    // Éxito: el backend creó el Payment gateway="transfer"
    window.selected.clear();
    refreshSummary();
    document.body.dispatchEvent(new Event("refreshGrid"));

    const until = data.reserved_until || "";
    if (transferMsg) {
      transferMsg.textContent =
        `Tus números han sido reservados para transferencia. ` +
        `Tienes 12 horas para realizarla. ` +
        (until ? `Reserva válida hasta: ${until}` : "");
    }

    // Si hay redirect, dejamos el loader puesto hasta cambiar de página
    if (data.redirect_url) {
      setTimeout(() => {
        window.location.href = data.redirect_url;
      }, 1500);
    } else {
      // Si no hay redirect, entonces liberamos loader y botón
      hideLoader();
      if (transferBtn) transferBtn.disabled = false;
    }
  }

  transferBtn?.addEventListener("click", reserveByTransfer);

  // Init
  refreshSummary();
  // Si la primera página está completamente vendida, saltar automáticamente
  autoSkipSoldOut(grid);
})();
