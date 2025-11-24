document.addEventListener("DOMContentLoaded", () => {
  console.log("[checkout_pro] DOMContentLoaded");

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  }
  const csrftoken = getCookie("csrftoken");

  const mpConfig = document.querySelector("#mp-config");
  if (!mpConfig) {
    console.log("[checkout_pro] No se encontró #mp-config, no es página de pago");
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
  let lastSignature = null;  // estado con el que se creó la última preferencia
  let pendingUpdate = false; // indica si hubo cambios mientras se creaba la preferencia

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

    // Si NO estamos listos para pagar: desmontar y mostrar texto de ayuda
    if (!readyForPayment) {
      console.log("[checkout_pro] no listo para pagar, desmontando wallet si existe");
      lastSignature = null;
      pendingUpdate = false;

      if (walletController) {
        try {
          walletController.unmount();
        } catch (err) {
          console.warn("[checkout_pro] error al unmount wallet:", err);
        }
        walletController = null;
      }

      if (walletContainer) {
        // Mantener visible el contenedor y mostrar el placeholder
        walletContainer.classList.remove("hidden");
        walletContainer.innerHTML = `
          <p id="wallet-placeholder" class="text-xs text-gray-500 text-center px-2">
            El botón de pago aparecerá aquí cuando selecciones tus números
            e ingreses tu nombre y correo.
          </p>
        `;
      }
      return;
    }

    // Firma del estado actual
    const signature = JSON.stringify({
      numbers: selectedNumbers,
      name,
      email,
      phone,
    });

    // Si ya hay una preferencia para este mismo estado y el wallet está montado, no hacer nada
    if (!creatingPreference && signature === lastSignature && walletController) {
      console.log("[checkout_pro] estado no cambió, no recreo wallet");
      return;
    }

    // Si YA se está creando una preferencia, marcamos que hay cambios pendientes
    if (creatingPreference) {
      console.log("[checkout_pro] ya se está creando una preferencia, marco pendingUpdate");
      pendingUpdate = true;
      return;
    }

    // Empezamos una nueva creación de preferencia con el estado actual
    creatingPreference = true;
    pendingUpdate = false;  // vamos a trabajar con este snapshot

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
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrftoken || "",
        },
        body: JSON.stringify({
          chosen_numbers: selectedNumbers,
          buyer: { name, email, phone },
        }),
      });
    } catch (err) {
      console.error("[checkout_pro] Error de red al crear preferencia:", err);
      creatingPreference = false;
      // Si hubo cambios mientras tanto, reintentar
      if (pendingUpdate) {
        console.log("[checkout_pro] reintentando tras error de red por pendingUpdate=true");
        pendingUpdate = false;
        createOrUpdateWallet();
      }
      return;
    }

    let data;
    try {
      data = await resp.json();
    } catch (err) {
      console.error("[checkout_pro] Error parseando respuesta de preferencia:", err);
      creatingPreference = false;
      if (pendingUpdate) {
        console.log("[checkout_pro] reintentando tras error de parseo por pendingUpdate=true");
        pendingUpdate = false;
        createOrUpdateWallet();
      }
      return;
    }

    console.log("[checkout_pro] respuesta de create_preference:", data);

    if (!resp.ok || !data.preference_id) {
      console.error("[checkout_pro] Error creando preferencia:", data);
      creatingPreference = false;
      if (pendingUpdate) {
        console.log("[checkout_pro] reintentando tras error de preferencia por pendingUpdate=true");
        pendingUpdate = false;
        createOrUpdateWallet();
      }
      return;
    }

    const preferenceId = data.preference_id;
    console.log("[checkout_pro] preferenceId:", preferenceId);

    try {
      if (walletContainer) {
        // Aseguramos que el contenedor esté visible y vacío
        walletContainer.classList.remove("hidden");
        walletContainer.innerHTML = "";
      }

      walletController = await bricksBuilder.create("wallet", "walletBrick_container", {
        initialization: {
          preferenceId: preferenceId,
        },
      });
      console.log("[checkout_pro] Wallet Brick creado OK");
      // Recién ahora damos por bueno este estado
      lastSignature = signature;
    } catch (err) {
      console.error("[checkout_pro] Error creando Wallet Brick:", err);
      walletController = null;
    } finally {
      creatingPreference = false;
      // Si hubo cambios durante la creación, volvemos a lanzar
      if (pendingUpdate) {
        console.log("[checkout_pro] había cambios pendientes, relanzando createOrUpdateWallet");
        pendingUpdate = false;
        createOrUpdateWallet();
      }
    }
  }

  // Esta función se llamará desde detail_page.js cada vez que cambie algo relevante
  window.updateWalletIfReady = () => {
    console.log("[checkout_pro] window.updateWalletIfReady disparado");
    createOrUpdateWallet();
  };
});