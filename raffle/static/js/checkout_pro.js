document.addEventListener("DOMContentLoaded", () => {
  console.log("[checkout_pro] DOMContentLoaded");

  const mpConfig = document.querySelector("#mp-config");
  if (!mpConfig) {
    console.error("[checkout_pro] No se encontró #mp-config");
    return;
  }

  const publicKey = mpConfig.dataset.publicKey;
  console.log("[checkout_pro] publicKey:", publicKey);
  if (!publicKey) {
    console.error("[checkout_pro] Falta MP_PUBLIC_KEY en data-public-key");
    return;
  }

  const buyerNameInput = document.getElementById("buyer_name");
  const buyerEmailInput = document.getElementById("buyer_email");
  const buyerPhoneInput = document.getElementById("buyer_phone");
  const walletContainer = document.getElementById("walletBrick_container");

  const mp = new MercadoPago(publicKey, { locale: "es-CL" });
  const bricksBuilder = mp.bricks();

  let creatingPreference = false;
  let walletController = null;
  let lastSignature = null; // para no recrear si nada cambió

  async function createOrUpdateWallet() {
    console.log("[checkout_pro] createOrUpdateWallet llamado");

    const selectedSet = window.selected;
    if (!selectedSet || !(selectedSet instanceof Set)) {
      console.error("[checkout_pro] window.selected no está definido o no es un Set.");
      return;
    }

    const name = buyerNameInput?.value.trim() || "";
    const email = buyerEmailInput?.value.trim() || "";
    const phone = buyerPhoneInput?.value.trim() || "";

    const selectedNumbers = Array.from(selectedSet).sort((a, b) => a - b);
    const hasSelection = selectedNumbers.length > 0;
    const hasBuyer = name && email && email.includes("@");
    const readyForPayment = hasSelection && hasBuyer;

    console.log("[checkout_pro] estado:", {
      selectedNumbers,
      name,
      email,
      hasSelection,
      hasBuyer,
      readyForPayment,
    });

    // Si NO estamos listos para pagar: ocultar y desmontar si existe
    if (!readyForPayment) {
      console.log("[checkout_pro] no listo para pagar, desmontando wallet si existe");
      lastSignature = null;

      if (walletController) {
        try {
          walletController.unmount();
        } catch (err) {
          console.warn("[checkout_pro] error al unmount wallet:", err);
        }
        walletController = null;
      }

      if (walletContainer) {
        walletContainer.classList.add("hidden");
        walletContainer.innerHTML = "";
      }
      return;
    }

    // Desde aquí sí estamos listos para pagar
    if (walletContainer) {
      walletContainer.classList.remove("hidden");
    }

    // Crear firma del estado actual (para no recrear si es igual)
    const signature = JSON.stringify({
      numbers: selectedNumbers,
      name,
      email,
      phone,
    });

    if (signature === lastSignature && walletController) {
      console.log("[checkout_pro] estado no cambió, no recreo wallet");
      return;
    }

    // Si cambió algo, guardamos firma y recreamos
    lastSignature = signature;

    if (creatingPreference) {
      console.log("[checkout_pro] ya se está creando una preferencia, salgo");
      return;
    }

    creatingPreference = true;

    // Desmontar wallet anterior si existía
    if (walletController) {
      try {
        walletController.unmount();
      } catch (err) {
        console.warn("[checkout_pro] error al unmount previo wallet:", err);
      }
      walletController = null;
    }

    console.log("[checkout_pro] creando preferencia con números:", selectedNumbers);

    let resp;
    try {
      resp = await fetch("/mp/create_preference/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chosen_numbers: selectedNumbers,
          buyer: { name, email, phone },
        }),
      });
    } catch (err) {
      console.error("[checkout_pro] Error de red al crear preferencia:", err);
      creatingPreference = false;
      return;
    }

    let data;
    try {
      data = await resp.json();
    } catch (err) {
      console.error("[checkout_pro] Error parseando respuesta de preferencia:", err);
      creatingPreference = false;
      return;
    }

    console.log("[checkout_pro] respuesta de create_preference:", data);

    if (!resp.ok || !data.preference_id) {
      console.error("[checkout_pro] Error creando preferencia:", data);
      creatingPreference = false;
      return;
    }

    const preferenceId = data.preference_id;
    console.log("[checkout_pro] preferenceId:", preferenceId);

    try {
      walletController = await bricksBuilder.create("wallet", "walletBrick_container", {
        initialization: {
          preferenceId: preferenceId,
        },
      });
      console.log("[checkout_pro] Wallet Brick creado OK");
    } catch (err) {
      console.error("[checkout_pro] Error creando Wallet Brick:", err);
      walletController = null;
    } finally {
      creatingPreference = false;
    }
  }

  // Esta función se llamará desde detail_page.js cada vez que cambie algo relevante
  window.updateWalletIfReady = () => {
    console.log("[checkout_pro] window.updateWalletIfReady disparado");
    createOrUpdateWallet();
  };
});