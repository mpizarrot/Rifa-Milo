document.addEventListener("DOMContentLoaded", () => {
  console.log("[donation] DOMContentLoaded");

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
  const phoneInput = document.getElementById("buyer_phone");

  const walletContainer = document.getElementById("walletBrick_container");

  const mp = new MercadoPago(publicKey, { locale: "es-CL" });
  const bricksBuilder = mp.bricks();

  let walletController = null;
  let creatingPreference = false;
  let lastSignature = null;

  function formatAmountCLP(value) {
    const n = Number(value) || 0;
    return n.toLocaleString("es-CL");
  }

  async function createOrUpdateWallet() {
    const amount = parseInt(amountInput?.value || "0", 10) || 0;
    const name = (nameInput?.value || "").trim();
    const email = (emailInput?.value || "").trim();
    const phone = (phoneInput?.value || "").trim();

    amountDisplay.textContent = formatAmountCLP(amount);

    const hasValidAmount = amount >= 1000; // mínimo sugerido
    const hasBuyer = name && email && email.includes("@");
    const readyForPayment = hasValidAmount && hasBuyer;

    if (!readyForPayment) {
      lastSignature = null;

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

    const signature = JSON.stringify({ amount, name, email, phone });
    if (signature === lastSignature && walletController) {
      console.log("[donation] estado no cambió, no recreo wallet");
      return;
    }
    lastSignature = signature;

    if (creatingPreference) {
      console.log("[donation] ya se está creando una preferencia, salgo");
      return;
    }

    creatingPreference = true;

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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount_clp: amount,
          buyer: { name, email, phone },
        }),
      });
    } catch (err) {
      console.error("[donation] Error de red al crear preferencia:", err);
      creatingPreference = false;
      return;
    }

    let data;
    try {
      data = await resp.json();
    } catch (err) {
      console.error("[donation] Error parseando respuesta de preferencia:", err);
      creatingPreference = false;
      return;
    }

    console.log("[donation] respuesta de create_donation_preference:", data);

    if (!resp.ok || !data.preference_id) {
      console.error("[donation] Error creando preferencia:", data);
      creatingPreference = false;
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
    } catch (err) {
      console.error("[donation] Error creando Wallet Brick:", err);
      walletController = null;
    } finally {
      creatingPreference = false;
    }
  }

  const inputs = [amountInput, nameInput, emailInput, phoneInput];
  inputs.forEach((el) => {
    el?.addEventListener("input", () => {
      createOrUpdateWallet();
    });
  });

  // Inicial
  createOrUpdateWallet();
});
