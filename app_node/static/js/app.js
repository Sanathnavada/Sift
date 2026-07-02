(function () {
  var spotifyAuthStorageKey = "gateway.spotifyAuthStatusUrl";
  var clientSessionStorageKey = "gateway.clientSessionId";
  var instagramAuthPopup = null;
  var instagramAuthCloseUrl = "";
  var instagramAuthPopupWatch = null;
  var instagramAuthClosingExpected = false;

  function readCookie(name) {
    var prefix = name + "=";
    return document.cookie.split(";").map(function (part) {
      return part.trim();
    }).reduce(function (found, part) {
      if (found) {
        return found;
      }
      if (part.indexOf(prefix) === 0) {
        try {
          return decodeURIComponent(part.slice(prefix.length));
        } catch (error) {
          return part.slice(prefix.length);
        }
      }
      return "";
    }, "");
  }

  function rememberClientSessionCookie(sessionId) {
    if (!sessionId) {
      return;
    }
    try {
      document.cookie = "gateway_client_session_id=" + encodeURIComponent(sessionId) + "; path=/; SameSite=Lax";
    } catch (error) {
      // Cookie persistence is best-effort; HTMX requests still carry the header.
    }
  }

  function getClientSessionId() {
    var storedSessionId = window.localStorage.getItem(clientSessionStorageKey);
    var cookieSessionId = readCookie("gateway_client_session_id");
    var legacySessionId = window.sessionStorage.getItem(clientSessionStorageKey);
    var sessionId = storedSessionId || cookieSessionId || legacySessionId;
    if (!sessionId) {
      sessionId = window.crypto.randomUUID
        ? window.crypto.randomUUID().replace(/-/g, "")
        : Array.from(window.crypto.getRandomValues(new Uint8Array(24)), function (value) {
            return value.toString(16).padStart(2, "0");
          }).join("");
    }
    window.localStorage.setItem(clientSessionStorageKey, sessionId);
    window.sessionStorage.removeItem(clientSessionStorageKey);
    rememberClientSessionCookie(sessionId);
    return sessionId;
  }

  window.gatewayGetClientSessionId = getClientSessionId;

  function updateSpotifyAuthStatus(statusUrl) {
    var target = document.querySelector("#music-library-auth-panel");
    if (!statusUrl || !target || !window.htmx) {
      return false;
    }
    window.htmx.ajax("GET", statusUrl, { target: "#music-library-auth-panel", swap: "innerHTML" });
    return true;
  }

  function consumeSpotifyAuthStatus() {
    var statusUrl = window.localStorage.getItem(spotifyAuthStorageKey);
    if (updateSpotifyAuthStatus(statusUrl)) {
      window.localStorage.removeItem(spotifyAuthStorageKey);
    }
  }

  function stopInstagramAuthPopupWatch() {
    if (instagramAuthPopupWatch) {
      window.clearInterval(instagramAuthPopupWatch);
      instagramAuthPopupWatch = null;
    }
  }

  function notifyInstagramAuthPopupClosed() {
    if (!instagramAuthCloseUrl || instagramAuthClosingExpected) {
      return;
    }
    var headers = { "X-Client-Session-ID": getClientSessionId() };
    try {
      fetch(instagramAuthCloseUrl, {
        method: "POST",
        headers: headers,
        credentials: "same-origin",
        keepalive: true
      }).catch(function () {});
    } catch (error) {
      // keepalive fetch is best-effort; the timeout guard still closes the job.
    }
  }

  function watchInstagramAuthPopupClose() {
    stopInstagramAuthPopupWatch();
    if (!instagramAuthPopup) {
      return;
    }
    instagramAuthPopupWatch = window.setInterval(function () {
      if (!instagramAuthPopup || instagramAuthPopup.closed) {
        stopInstagramAuthPopupWatch();
        notifyInstagramAuthPopupClosed();
      }
    }, 1000);
  }

  function openInstagramAuthPopup(url, closeUrl) {
    if (closeUrl) {
      instagramAuthCloseUrl = closeUrl;
    }
    instagramAuthClosingExpected = false;
    var popupWidth = Math.max(window.screen && window.screen.availWidth ? window.screen.availWidth : 1920, 1200);
    var popupHeight = Math.max(window.screen && window.screen.availHeight ? window.screen.availHeight : 1080, 800);
    instagramAuthPopup = window.open(
      url || "about:blank",
      "instagram-auth",
      "popup=yes,width=" + popupWidth + ",height=" + popupHeight + ",left=0,top=0"
    );
    if (instagramAuthPopup) {
      try {
        instagramAuthPopup.moveTo(0, 0);
        instagramAuthPopup.resizeTo(popupWidth, popupHeight);
        instagramAuthPopup.focus();
      } catch (error) {
        // Browser may restrict popup resizing/focus; the page still works.
      }
      watchInstagramAuthPopupClose();
    }
    if (instagramAuthPopup && !url) {
      try {
        instagramAuthPopup.document.title = "Instagram Login Console";
        instagramAuthPopup.document.body.innerHTML = "<p style='font-family:sans-serif;padding:24px'>Opening Instagram login console...</p>";
      } catch (error) {
        // Some browsers restrict writing to popup windows. Navigation below still works.
      }
    }
    return instagramAuthPopup;
  }

  function withCacheBuster(url) {
    if (!url) {
      return url;
    }
    var separator = url.indexOf("?") === -1 ? "?" : "&";
    return url + separator + "view=" + Date.now();
  }

  function navigateInstagramAuthPopup(url, closeUrl) {
    if (!url) {
      return;
    }
    if (!instagramAuthPopup || instagramAuthPopup.closed) {
      return;
    }
    if (closeUrl) {
      instagramAuthCloseUrl = closeUrl;
    }
    instagramAuthClosingExpected = false;
    instagramAuthPopup.location.href = withCacheBuster(url);
    try {
      instagramAuthPopup.focus();
    } catch (error) {
      // Focus can be blocked by browser policy; it is optional.
    }
    watchInstagramAuthPopupClose();
  }

  function closeInstagramAuthPopup() {
    instagramAuthClosingExpected = true;
    stopInstagramAuthPopupWatch();
    if (instagramAuthPopup && !instagramAuthPopup.closed) {
      try {
        instagramAuthPopup.close();
      } catch (error) {
        // Browser may block closing in edge cases. The popup page shows a manual close fallback.
      }
    }
  }

  function syncInstagramAuthCards(root) {
    root.querySelectorAll("[data-instagram-auth-card]").forEach(function (card) {
      var url = card.dataset.instagramAuthWindowUrl;
      var closeUrl = card.dataset.instagramAuthCloseUrl || instagramAuthCloseUrl;
      if (closeUrl) {
        instagramAuthCloseUrl = closeUrl;
      }
      if (card.dataset.instagramAuthOpenPopup === "true") {
        navigateInstagramAuthPopup(url, closeUrl);
      }
      if (card.dataset.instagramAuthConnected === "true") {
        closeInstagramAuthPopup();
      }
    });
  }

  function initInstagramAuthControls(root) {
    root.querySelectorAll("[data-instagram-auth-popup-trigger]").forEach(function (button) {
      if (button.dataset.boundInstagramAuthPopup) {
        return;
      }
      button.dataset.boundInstagramAuthPopup = "true";
      button.addEventListener("click", function () {
        var card = button.closest("[data-instagram-auth-card]");
        openInstagramAuthPopup(null, card ? card.dataset.instagramAuthCloseUrl : "");
      });
    });

    root.querySelectorAll("[data-instagram-auth-popup-link]").forEach(function (link) {
      if (link.dataset.boundInstagramAuthPopupLink) {
        return;
      }
      link.dataset.boundInstagramAuthPopupLink = "true";
      link.addEventListener("click", function (event) {
        event.preventDefault();
        openInstagramAuthPopup(withCacheBuster(link.href), link.dataset.instagramAuthCloseUrl || "");
      });
    });
  }

  function initInstagramAuthPopupPage() {
    var body = document.body;
    if (!body || !body.matches("[data-instagram-auth-popup-page]")) {
      return;
    }
    var statusUrl = body.dataset.instagramAuthStateUrl;
    var statusTarget = document.querySelector("#instagram-auth-window-status");
    if (!statusUrl || body.dataset.boundInstagramAuthPopupPage) {
      return;
    }
    body.dataset.boundInstagramAuthPopupPage = "true";

    function updateStatus(text, variant) {
      if (!statusTarget) {
        return;
      }
      statusTarget.textContent = text;
      statusTarget.className = "status-pill status-pill--" + variant;
    }

    function poll() {
      fetch(statusUrl, {
        headers: { "X-Client-Session-ID": getClientSessionId() },
        credentials: "same-origin"
      })
        .then(function (response) { return response.json(); })
        .then(function (state) {
          if (state.authenticated) {
            updateStatus(state.username ? "Connected as @" + state.username : "Connected", "success");
            window.setTimeout(function () { window.close(); }, 700);
            return;
          }
          if (state.status === "failed") {
            updateStatus("Login failed", "danger");
            return;
          }
          if (state.status === "cancelled" || state.status === "stale") {
            updateStatus("Login session stopped", "warning");
            return;
          }
          updateStatus("Waiting for login", "accent");
          window.setTimeout(poll, 2000);
        })
        .catch(function () {
          updateStatus("Waiting for server", "warning");
          window.setTimeout(poll, 3000);
        });
    }

    poll();
  }

  function initOutputModeGroups(root) {
    root.querySelectorAll("[data-output-mode-group]").forEach(function (group) {
      var radios = group.querySelectorAll('input[type="radio"][name="output_mode"]');
      var panel = group.parentElement.querySelector("[data-outdir-field]");
      if (!panel) {
        return;
      }

      function sync() {
        var selected = group.querySelector('input[type="radio"][name="output_mode"]:checked');
        var persistent = selected && selected.value === "persistent";
        panel.classList.toggle("is-hidden", !persistent);
        panel.querySelectorAll("input").forEach(function (input) {
          input.required = persistent;
        });
      }

      radios.forEach(function (radio) {
        radio.addEventListener("change", sync);
      });
      sync();
    });
  }

  function initModeGroups(root) {
    root.querySelectorAll("[data-mode-group]").forEach(function (group) {
      if (group.dataset.boundModeGroup) {
        return;
      }
      group.dataset.boundModeGroup = "true";
      var groupName = group.dataset.modeGroup;
      var radios = group.querySelectorAll('input[type="radio"]');
      var container = group.closest("form") || document;

      function sync() {
        var selected = group.querySelector('input[type="radio"]:checked');
        var value = selected ? selected.value : "";
        container.querySelectorAll('[data-mode-panel="' + groupName + '"]').forEach(function (panel) {
          var active = panel.dataset.modeValue === value;
          panel.classList.toggle("is-hidden", !active);
          panel.querySelectorAll("input, textarea, select").forEach(function (input) {
            input.disabled = !active;
            if (input.dataset.requiredWhenActive === "true") {
              input.required = active;
            }
          });
        });
        if (groupName === "instagram-source") {
          syncInstagramTaskPanel(container, value);
        }
      }

      radios.forEach(function (radio) {
        radio.addEventListener("change", sync);
      });
      sync();
    });
  }

  function ensureInstagramTaskPanels() {
    var host = document.querySelector("#media-task-panel");
    if (!host) {
      return null;
    }
    var panels = host.querySelector("[data-instagram-task-panels]");
    if (panels) {
      return panels;
    }
    host.innerHTML = [
      '<div data-instagram-task-panels>',
      '  <div id="media-instagram-posts-task-panel" data-instagram-task-mode="posts">',
      '    <article class="empty-state"><h3>No post task yet</h3><p>Submit Instagram post or reel URLs to start a task.</p></article>',
      '  </div>',
      '  <div id="media-instagram-public-profile-task-panel" class="is-hidden" data-instagram-task-mode="public_profile">',
      '    <article class="empty-state"><h3>No public profile task yet</h3><p>Submit a public profile to start a task.</p></article>',
      '  </div>',
      '  <div id="media-instagram-private-collection-task-panel" class="is-hidden" data-instagram-task-mode="private_collection">',
      '    <article class="empty-state"><h3>No private collection task yet</h3><p>Submit a saved collection to start a task.</p></article>',
      '  </div>',
      '</div>'
    ].join("");
    return host.querySelector("[data-instagram-task-panels]");
  }

  function syncInstagramTaskPanel(form, mode) {
    if (!form || !form.matches("[data-instagram-mode-form]")) {
      return;
    }
    var panels = ensureInstagramTaskPanels();
    if (!panels) {
      return;
    }
    var targetId = "media-instagram-" + mode.replace(/_/g, "-") + "-task-panel";
    form.setAttribute("hx-target", "#" + targetId);
    panels.querySelectorAll("[data-instagram-task-mode]").forEach(function (panel) {
      panel.classList.toggle("is-hidden", panel.dataset.instagramTaskMode !== mode);
    });
  }

  function getSubmittingForm(event) {
    var element = event.detail && event.detail.elt ? event.detail.elt : event.target;
    var form = element && element.closest ? element.closest("form") : null;
    if (!form) {
      return null;
    }
    if (element === form || form.matches(".htmx-request")) {
      return form;
    }
    if (element.matches && element.matches('button[type="submit"], input[type="submit"]')) {
      return form;
    }
    return null;
  }


  function setMediaWorkspaceMode(mode) {
    var workspace = document.querySelector("[data-media-task-workspace]");
    if (!workspace || !mode) {
      return;
    }
    workspace.querySelectorAll("[data-media-task-mode]").forEach(function (panel) {
      panel.classList.toggle("is-hidden", panel.dataset.mediaTaskMode !== mode);
    });
  }

  function setMusicWorkspaceMode(mode) {
    if (!mode) {
      return;
    }
    document.querySelectorAll("[data-music-task-mode]").forEach(function (panel) {
      panel.classList.toggle("is-hidden", panel.dataset.musicTaskMode !== mode);
    });
  }

  function applyWorkspaceMode(group, mode) {
    var workspace = group.dataset.workspace;
    if (workspace === "media") {
      setMediaWorkspaceMode(mode);
    } else if (workspace === "music") {
      setMusicWorkspaceMode(mode);
    }
  }

  function initTabGroups(root) {
    root.querySelectorAll("[data-tab-group]").forEach(function (group) {
      var active = group.querySelector("[data-tab].is-active");
      if (active && active.dataset.workspaceMode) {
        applyWorkspaceMode(group, active.dataset.workspaceMode);
      }
      group.querySelectorAll("[data-tab]").forEach(function (button) {
        if (button.dataset.boundWorkspaceTab) {
          return;
        }
        button.dataset.boundWorkspaceTab = "true";
        button.addEventListener("click", function () {
          group.querySelectorAll("[data-tab]").forEach(function (candidate) {
            candidate.classList.remove("is-active");
          });
          button.classList.add("is-active");
          applyWorkspaceMode(group, button.dataset.workspaceMode || "");
        });
      });
    });
  }

  function initSpotifyAuthLinks(root) {
    root.querySelectorAll("[data-spotify-auth-url]").forEach(function (link) {
      if (link.dataset.boundSpotifyAuth) {
        return;
      }
      link.dataset.boundSpotifyAuth = "true";
      link.addEventListener("click", function (event) {
        event.preventDefault();
        window.open(link.href, "spotify-auth", "popup=yes,width=520,height=760");
      });
    });
  }

  function getNavigationType() {
    try {
      var entries = window.performance && window.performance.getEntriesByType
        ? window.performance.getEntriesByType("navigation")
        : [];
      if (entries && entries.length && entries[0].type) {
        return entries[0].type;
      }
      if (window.performance && window.performance.navigation) {
        return window.performance.navigation.type === 1 ? "reload" : "navigate";
      }
    } catch (error) {
      // Navigation timing can be unavailable in older or restricted browsers.
    }
    return "navigate";
  }

  function playAudioOnce(audio, markPlayed) {
    if (!audio) {
      return;
    }
    try {
      audio.currentTime = 0;
    } catch (error) {
      // Some browsers may reject currentTime changes before metadata is ready.
    }
    var playResult;
    try {
      playResult = audio.play();
    } catch (error) {
      return;
    }
    if (playResult && typeof playResult.then === "function") {
      playResult.then(function () {
        markPlayed();
      }).catch(function () {
        // Audible autoplay was blocked. The caller will attach a first-interaction fallback.
      });
    } else {
      markPlayed();
    }
  }

  function initHomeIntroAudio() {
    var audio = document.querySelector("[data-home-intro-audio]");
    if (!audio || audio.dataset.boundHomeIntroAudio) {
      return;
    }
    audio.dataset.boundHomeIntroAudio = "true";

    var storageKey = "gateway.homeIntroPlayed";
    var navigationType = getNavigationType();
    var isReload = navigationType === "reload";
    var alreadyPlayedThisSession = window.sessionStorage.getItem(storageKey) === "true";

    if (alreadyPlayedThisSession && !isReload) {
      return;
    }

    var delayMs = Number.parseInt(audio.dataset.homeIntroDelayMs || "2500", 10);
    if (!Number.isFinite(delayMs) || delayMs < 0) {
      delayMs = 2500;
    }

    var consumed = false;
    function markPlayed() {
      consumed = true;
      window.sessionStorage.setItem(storageKey, "true");
      removeInteractionFallback();
    }

    function attemptPlay() {
      if (consumed) {
        return;
      }
      // Mark this home-page visit as consumed before attempting playback.
      // This keeps Home -> other page -> Home from replaying, while reloads are still allowed.
      window.sessionStorage.setItem(storageKey, "true");
      playAudioOnce(audio, markPlayed);
    }

    function interactionFallback() {
      attemptPlay();
    }

    function removeInteractionFallback() {
      ["pointerdown", "keydown", "touchstart"].forEach(function (eventName) {
        document.removeEventListener(eventName, interactionFallback, true);
      });
    }

    window.setTimeout(function () {
      attemptPlay();
      // Chrome may block audible autoplay until user gesture. Keep a one-shot fallback armed.
      if (!consumed) {
        ["pointerdown", "keydown", "touchstart"].forEach(function (eventName) {
          document.addEventListener(eventName, interactionFallback, { once: true, capture: true });
        });
      }
    }, delayMs);
  }

  function init(root) {
    initOutputModeGroups(root);
    initModeGroups(root);
    initTabGroups(root);
    initSpotifyAuthLinks(root);
    initInstagramAuthControls(root);
    syncInstagramAuthCards(root);
    root.querySelectorAll(".button, .segment-bar__button").forEach(function (button) {
      if (button.dataset.boundClickFeedback) {
        return;
      }
      button.dataset.boundClickFeedback = "true";
      button.addEventListener("pointerdown", function () {
        button.classList.add("is-pressed");
      });
      ["pointerup", "pointercancel", "pointerleave"].forEach(function (eventName) {
        button.addEventListener(eventName, function () {
          button.classList.remove("is-pressed");
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    getClientSessionId();
    init(document);
    initInstagramAuthPopupPage();
    initHomeIntroAudio();
    consumeSpotifyAuthStatus();
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    init(event.target);
    consumeSpotifyAuthStatus();
  });

  window.addEventListener("storage", function (event) {
    if (event.key === spotifyAuthStorageKey && updateSpotifyAuthStatus(event.newValue)) {
      window.localStorage.removeItem(spotifyAuthStorageKey);
    }
  });

  document.body.addEventListener("htmx:beforeRequest", function (event) {
    event.detail.xhr.setRequestHeader("X-Client-Session-ID", getClientSessionId());
    var form = getSubmittingForm(event);
    if (!form) {
      return;
    }
    if (form.matches("[data-instagram-mode-form]")) {
      var selectedMode = form.querySelector('input[name="instagram_mode"]:checked');
      if (selectedMode) {
        syncInstagramTaskPanel(form, selectedMode.value);
      }
    }
    var processingMessage = form.querySelector("[data-processing-message]");
    if (processingMessage) {
      processingMessage.classList.add("is-visible");
    }
    form.querySelectorAll('button[type="submit"]').forEach(function (button) {
      button.dataset.originalText = button.textContent;
      button.textContent = form.dataset.submitLabel || "Submitting...";
      button.classList.add("is-submitting");
    });
  });

  document.body.addEventListener("htmx:afterRequest", function (event) {
    var form = getSubmittingForm(event);
    if (!form) {
      return;
    }
    if (!event.detail.successful) {
      var processingMessage = form.querySelector("[data-processing-message]");
      if (processingMessage) {
        processingMessage.classList.remove("is-visible");
      }
    }
    form.querySelectorAll('button[type="submit"]').forEach(function (button) {
      if (button.dataset.originalText) {
        button.textContent = button.dataset.originalText;
        delete button.dataset.originalText;
      }
      button.classList.remove("is-submitting");
    });
  });

  document.body.addEventListener("htmx:beforeSwap", function (event) {
    if (event.detail.xhr && event.detail.xhr.status >= 400 && event.detail.xhr.responseText) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });
})();
