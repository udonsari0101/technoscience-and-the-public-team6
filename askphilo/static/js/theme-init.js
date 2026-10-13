// Runs synchronously in <head> to apply the saved theme before first paint.
(function () {
  try {
    var saved = localStorage.getItem("askphilo-theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (e) {
    /* storage unavailable: follow the system preference */
  }
})();
