document.addEventListener("DOMContentLoaded", () => {
  console.log("[donation] DOMContentLoaded");

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  }
  const csrftoken = getCookie("csrftoken");

  const mpConfig = document.querySelector("#mp-config");
  if (!mpConfig) {
    console.error("[donation] No se encontró #mp-config");
    return;
  }

  const publicKey = mpConfig.dataset.publicKey;
  if (!publicKey) {
    console.error("[donation] Falta MP_PUBLIC_KEY en data-public-key");
    return;
  }

  const amountInput = document.getElementById("donation_amount");
  const amountDisplay = document.getElementById("donation-amount-display");

  const nameInput = document.getElementById("buyer_name");
  const emailInput = document.getElementById("buyer_email");

  const walletContainer = document.getElementById("walletBrick_container");

  const mp = new MercadoPago(publicKey, { locale: "es-CL" });
  const bricksBuilder = mp.bricks();

  let walletController = null;
  let creatingPreference = false;
  let lastSignature = null;
  let pendingUpdate = false;

  function formatAmountCLP(value) {
    const n = Number(value) || 0;
    return n.toLocaleString("es-CL");
  }

  async function createOrUpdateWallet() {
    const amount = parseInt(amountInput?.value || "0", 10) || 0;
    const name = (nameInput?.value || "").trim();
    const email = (emailInput?.value || "").trim();

    amountDisplay.textContent = formatAmountCLP(amount);

    const hasValidAmount = amount >= 1000 && amount <= 5000000;
    const readyForPayment = hasValidAmount; // nombre/email son opcionales

    if (!readyForPayment) {
      console.log("[donation] no listo para pagar, ocultando wallet");
      lastSignature = null;
      pendingUpdate = false;

      if (walletController) {
        try {
          walletController.unmount();
        } catch (err) {
          console.warn("[donation] error al unmount wallet:", err);
        }
        walletController = null;
      }

      if (walletContainer) {
        walletContainer.classList.add("hidden");
        walletContainer.innerHTML = "";
      }
      return;
    }

    if (walletContainer) {
      walletContainer.classList.remove("hidden");
    }

    const signature = JSON.stringify({ amount, name, email });

    // Si ya existe un wallet creado con este mismo estado y no estamos creando nada, no hacer nada
    if (!creatingPreference && signature === lastSignature && walletController) {
      console.log("[donation] estado no cambió, no recreo wallet");
      return;
    }

    // Si ya se está creando una preferencia, marcar que hay cambios pendientes
    if (creatingPreference) {
      console.log("[donation] ya se está creando una preferencia, marco pendingUpdate");
      pendingUpdate = true;
      return;
    }

    // Empezamos una nueva creación con el snapshot actual
    creatingPreference = true;
    pendingUpdate = false;

    // Desmontar wallet anterior si existiera
    if (walletController) {
      try {
        walletController.unmount();
      } catch (err) {
        console.warn("[donation] error al unmount previo wallet:", err);
      }
      walletController = null;
    }

    console.log("[donation] creando preferencia con monto:", amount);

    let resp;
    try {
      resp = await fetch("/mp/create_donation_preference/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrftoken || "",
        },
        body: JSON.stringify({
          amount_clp: amount,
          buyer: { name, email },
        }),
      });
    } catch (err) {
      console.error("[donation] Error de red al crear preferencia:", err);
      creatingPreference = false;

      if (pendingUpdate) {
        console.log("[donation] reintentando tras error de red por pendingUpdate=true");
        pendingUpdate = false;
        createOrUpdateWallet();
      }
      return;
    }

    let data;
    try {
      data = await resp.json();
    } catch (err) {
      console.error("[donation] Error parseando respuesta de preferencia:", err);
      creatingPreference = false;

      if (pendingUpdate) {
        console.log("[donation] reintentando tras error de parseo por pendingUpdate=true");
        pendingUpdate = false;
        createOrUpdateWallet();
      }
      return;
    }

    console.log("[donation] respuesta de create_donation_preference:", data);

    if (!resp.ok || !data.preference_id) {
      console.error("[donation] Error creando preferencia:", data);
      creatingPreference = false;

      if (pendingUpdate) {
        console.log("[donation] reintentando tras error de preferencia por pendingUpdate=true");
        pendingUpdate = false;
        createOrUpdateWallet();
      }
      return;
    }

    const preferenceId = data.preference_id;
    console.log("[donation] preferenceId:", preferenceId);

    try {
      walletController = await bricksBuilder.create("wallet", "walletBrick_container", {
        initialization: {
          preferenceId: preferenceId,
        },
      });
      console.log("[donation] Wallet Brick creado OK");
      lastSignature = signature;
    } catch (err) {
      console.error("[donation] Error creando Wallet Brick:", err);
      walletController = null;
    } finally {
      creatingPreference = false;

      if (pendingUpdate) {
        console.log("[donation] había cambios pendientes, relanzando createOrUpdateWallet");
        pendingUpdate = false;
        createOrUpdateWallet();
      }
    }
  }

  const inputs = [amountInput, nameInput, emailInput];
  inputs.forEach((el) => {
    el?.addEventListener("input", () => {
      createOrUpdateWallet();
    });
  });

  // Inicial
  createOrUpdateWallet();
});