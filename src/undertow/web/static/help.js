/* "?" help tips: open on tap or click, close on outside click or Escape. Hover and focus are CSS. */
(function () {
  function closeAll(except) {
    document.querySelectorAll(".help.open").forEach(function (h) {
      if (h !== except) { h.classList.remove("open"); h.querySelector(".help-btn").setAttribute("aria-expanded", "false"); }
    });
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".help-btn");
    if (!btn) { closeAll(null); return; }
    e.preventDefault(); e.stopPropagation();
    var h = btn.parentElement, open = !h.classList.contains("open");
    closeAll(h);
    h.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeAll(null); });
})();
