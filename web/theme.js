/* ============================================================
   AI聊不停 · Phase 3 界面缩放
   Ctrl + 滚轮 / Ctrl + + / Ctrl + - / Ctrl + 0
   90% · 100% · 110% · 125% 四档；localStorage 记忆；URL ?zoom= 可临时覆盖
   ============================================================ */
(function () {
  var KEY = "deck_ui_zoom";
  var STEPS = [0.9, 1, 1.1, 1.25];
  var MIN = 0.9, MAX = 1.25;

  function clamp(v) { return Math.min(MAX, Math.max(MIN, Math.round(v * 100) / 100)); }

  function fromUrl() {
    try {
      var q = new URLSearchParams(location.search).get("zoom");
      var v = parseFloat(q || "");
      return isFinite(v) && v > 0 ? clamp(v) : null;
    } catch (e) { return null; }
  }

  function stored() {
    try {
      var v = parseFloat(localStorage.getItem(KEY) || "");
      return isFinite(v) && v > 0 ? clamp(v) : 1;
    } catch (e) { return 1; }
  }

  var zoom = fromUrl() || stored();

  function badge(v) {
    var b = document.getElementById("uiZoomBadge");
    if (!b) {
      b = document.createElement("div");
      b.id = "uiZoomBadge";
      (document.body || document.documentElement).appendChild(b);
    }
    b.textContent = "界面缩放 " + Math.round(v * 100) + "%";
    b.classList.add("show");
    clearTimeout(b._t);
    b._t = setTimeout(function () { b.classList.remove("show"); }, 900);
  }

  function apply(v, silent) {
    zoom = clamp(v);
    document.documentElement.style.setProperty("--ui-zoom", String(zoom));
    try { localStorage.setItem(KEY, String(zoom)); } catch (e) { }
    if (!silent) badge(zoom);
  }

  function step(dir) {
    var i = STEPS.indexOf(zoom);
    if (i < 0) {
      i = 0;
      for (var k = 0; k < STEPS.length; k++) if (STEPS[k] <= zoom) i = k;
    }
    i = Math.min(STEPS.length - 1, Math.max(0, i + dir));
    apply(STEPS[i]);
  }

  apply(zoom, true);

  window.AI_ZOOM = {
    get: function () { return zoom; },
    set: function (v) { apply(v); }
  };

  document.addEventListener("wheel", function (e) {
    if (!e.ctrlKey) return;
    e.preventDefault();
    step(e.deltaY > 0 ? -1 : 1);
  }, { passive: false });

  document.addEventListener("keydown", function (e) {
    if (!e.ctrlKey || e.altKey || e.metaKey) return;
    if (e.key === "0") { apply(1); e.preventDefault(); return; }
    if (e.key === "=" || e.key === "+") { step(1); e.preventDefault(); return; }
    if (e.key === "-" || e.key === "_") { step(-1); e.preventDefault(); return; }
    if (e.key === "0" && (e.ctrlKey || e.metaKey)) { apply(1); e.preventDefault(); }
  });
})();
