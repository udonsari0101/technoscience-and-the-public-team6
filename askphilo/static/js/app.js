// AskPhilo — shared page behaviour (no framework).
(function () {
  "use strict";

  var THEME_KEY = "askphilo-theme";

  function readTheme() {
    try {
      var v = localStorage.getItem(THEME_KEY);
      return v === "light" || v === "dark" ? v : "system";
    } catch (e) {
      return "system";
    }
  }

  function applyTheme(value) {
    var root = document.documentElement;
    if (value === "light" || value === "dark") {
      root.setAttribute("data-theme", value);
    } else {
      root.removeAttribute("data-theme");
    }
    try {
      if (value === "system") localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, value);
    } catch (e) {
      /* ignore */
    }
  }

  // Theme segmented control (settings)
  document.querySelectorAll("[data-theme-control]").forEach(function (group) {
    var current = readTheme();
    group.querySelectorAll('input[name="theme"]').forEach(function (input) {
      input.checked = input.value === current;
      input.addEventListener("change", function () {
        if (input.checked) applyTheme(input.value);
      });
    });
  });

  // Enable colour transitions only after first paint (no flash on load)
  requestAnimationFrame(function () {
    document.body.classList.add("theme-ready");
  });

  // Toasts auto-dismiss
  document.querySelectorAll("[data-toast]").forEach(function (toast) {
    setTimeout(function () {
      toast.classList.add("is-leaving");
      setTimeout(function () {
        toast.remove();
      }, 300);
    }, 3200);
  });

  // Password reveal
  document.querySelectorAll("[data-reveal]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.getElementById(btn.getAttribute("data-reveal"));
      if (!input) return;
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-pressed", show ? "true" : "false");
      btn.setAttribute("aria-label", show ? "비밀번호 숨기기" : "비밀번호 보기");
    });
  });

  // Prevent double submits (signup/login hashing can take a moment)
  document.querySelectorAll("form[data-submit-once]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector('button[type="submit"]');
      if (btn) {
        setTimeout(function () {
          btn.disabled = true;
        }, 0);
      }
    });
  });

  // Dialogs: [data-dialog-open="id"] opens <dialog id="id">; links keep a no-JS fallback
  function openDialog(dialog, trigger) {
    if (!dialog || typeof dialog.showModal !== "function") return false;
    dialog._trigger = trigger || null;
    dialog.showModal();
    return true;
  }
  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-dialog-open]");
    if (opener) {
      var dialog = document.getElementById(opener.getAttribute("data-dialog-open"));
      if (openDialog(dialog, opener)) event.preventDefault();
      return;
    }
    var closer = event.target.closest("[data-dialog-close]");
    if (closer) {
      var d = closer.closest("dialog");
      if (d) d.close();
      return;
    }
    // Click on the backdrop closes the dialog
    if (event.target.tagName === "DIALOG") {
      var rect = event.target.getBoundingClientRect();
      var inside =
        event.clientX >= rect.left && event.clientX <= rect.right &&
        event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (!inside) event.target.close();
    }
  });
  document.querySelectorAll("dialog").forEach(function (dialog) {
    dialog.addEventListener("close", function () {
      if (dialog._trigger && typeof dialog._trigger.focus === "function") dialog._trigger.focus();
    });
  });

  // Popup menus: [data-menu-toggle] controls next [data-menu]
  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("[data-menu-toggle]");
    document.querySelectorAll("[data-menu]").forEach(function (menu) {
      if (toggle && menu.id === toggle.getAttribute("aria-controls")) return;
      menu.hidden = true;
      var t = document.querySelector('[aria-controls="' + menu.id + '"]');
      if (t) t.setAttribute("aria-expanded", "false");
    });
    if (toggle) {
      var menu = document.getElementById(toggle.getAttribute("aria-controls"));
      if (!menu) return;
      menu.hidden = !menu.hidden;
      toggle.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    document.querySelectorAll("[data-menu]").forEach(function (menu) {
      if (menu.hidden) return;
      menu.hidden = true;
      var toggle = document.querySelector('[aria-controls="' + menu.id + '"]');
      if (toggle) {
        toggle.setAttribute("aria-expanded", "false");
        toggle.focus(); // return focus to the control that opened the menu
      }
    });
  });
})();
