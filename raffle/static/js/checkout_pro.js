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

  const mp = new MercadoPago(publicKey, { locale: "es-CL" });
  const bricksBuilder = mp.bricks();

  let walletRendered = false;
  let creatingPreference = false; // para evitar múltiples requests en paralelo

  async function createWalletIfNeeded() {
    console.log("[checkout_pro] createWalletIfNeeded llamado");
    if (walletRendered) {
      console.log("[checkout_pro] ya hay walletRendered, salgo");
      return;
    }
    if (creatingPreference) {
      console.log("[checkout_pro] ya se está creando una preferencia, salgo");
      return;
    }

    const name = buyerNameInput?.value.trim();
    const email = buyerEmailInput?.value.trim();
    const phone = buyerPhoneInput?.value.trim();

    const selectedSet = window.selected;
    console.log("[checkout_pro] selectedSet:", selectedSet);

    if (!selectedSet || !(selectedSet instanceof Set)) {
      console.error("[checkout_pro] window.selected no está definido o no es un Set.");
      return;
    }

    const selectedNumbers = Array.from(selectedSet).sort((a, b) => a - b);
    console.log("[checkout_pro] selectedNumbers:", selectedNumbers);
    console.log("[checkout_pro] name:", name, "email:", email);

    // Condiciones para mostrar el botón de MP
    if (!name || !email || !email.includes("@") || selectedNumbers.length === 0) {
      console.log("[checkout_pro] condiciones NO cumplidas, no creo preferencia todavía");
      return; // aún no estamos listos
    }

    creatingPreference = true;
    console.log("[checkout_pro] condiciones OK, creando preferencia...");

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
      await bricksBuilder.create("wallet", "walletBrick_container", {
        initialization: {
          preferenceId: preferenceId,
        },
      });
      walletRendered = true;
      creatingPreference = false;
      console.log("[checkout_pro] Wallet Brick creado OK");
    } catch (err) {
      console.error("[checkout_pro] Error creando Wallet Brick:", err);
      creatingPreference = false;
      return;
    }
  }

  // Esta función se llamará desde detail_page.js cada vez que cambie algo relevante
  window.updateWalletIfReady = () => {
    console.log("[checkout_pro] window.updateWalletIfReady disparado");
    // no esperamos el promise, solo lo disparamos
    createWalletIfNeeded();
  };
});
