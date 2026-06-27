(function () {
  var spotifyAuthStorageKey = "gateway.spotifyAuthStatusUrl";
  var clientSessionStorageKey = "gateway.clientSessionId";

  function getClientSessionId() {
    var sessionId = window.sessionStorage.getItem(clientSessionStorageKey);
    if (!sessionId) {
      sessionId = window.crypto.randomUUID
        ? window.crypto.randomUUID().replace(/-/g, "")
        : Array.from(window.crypto.getRandomValues(new Uint8Array(24)), function (value) {
            return value.toString(16).padStart(2, "0");
          }).join("");
      window.sessionStorage.setItem(clientSessionStorageKey, sessionId);
    }
    return sessionId;
  }

  function updateSpotifyAuthStatus(statusUrl) {
    var target = document.querySelector("#music-task-panel");
    if (!statusUrl || !target || !window.htmx) {
      return false;
    }
    window.htmx.ajax("GET", statusUrl, { target: "#music-task-panel", swap: "innerHTML" });
    return true;
  }

  function consumeSpotifyAuthStatus() {
    var statusUrl = window.localStorage.getItem(spotifyAuthStorageKey);
    if (updateSpotifyAuthStatus(statusUrl)) {
      window.localStorage.removeItem(spotifyAuthStorageKey);
    }
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

  function initTabGroups(root) {
    root.querySelectorAll("[data-tab-group]").forEach(function (group) {
      group.querySelectorAll("[data-tab]").forEach(function (button) {
        button.addEventListener("click", function () {
          group.querySelectorAll("[data-tab]").forEach(function (candidate) {
            candidate.classList.remove("is-active");
          });
          button.classList.add("is-active");
          (button.dataset.clearTargets || "").split(",").forEach(function (selector) {
            var target = document.querySelector(selector.trim());
            if (!target) {
              return;
            }
            if (target.id === "music-task-panel") {
              target.innerHTML = '<article class="empty-state"><h3>No music task yet</h3><p>Submit a song, YouTube, or Spotify workflow to start a queued download or auth-guided process.</p></article>';
            } else {
              target.innerHTML = "";
            }
          });
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

  function init(root) {
    initOutputModeGroups(root);
    initModeGroups(root);
    initTabGroups(root);
    initSpotifyAuthLinks(root);
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
    var form = event.target.closest ? event.target.closest("form") : null;
    if (!form) {
      return;
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
    var form = event.target.closest ? event.target.closest("form") : null;
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
