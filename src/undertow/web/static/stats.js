/* Stats page: switch skaters and goalies, filter by search and position, sort by any column.
   Everything is already in the page, so this only shows, hides and reorders rows. */
(function () {
  "use strict";
  var q = document.getElementById("q");
  if (!q) return;
  var views = document.querySelectorAll("[data-table]");
  var chips = document.getElementById("poschips");
  var none = document.getElementById("none");
  var state = { view: "skaters", pos: "all", q: "" };

  function visibleRows() {
    var table = document.getElementById("t-" + state.view);
    return Array.prototype.slice.call(table.tBodies[0].rows);
  }
  function apply() {
    var shown = 0, term = state.q.trim().toLowerCase();
    visibleRows().forEach(function (tr) {
      var ok = (state.pos === "all" || tr.dataset.pos === state.pos) &&
               (!term || tr.dataset.name.indexOf(term) !== -1);
      tr.hidden = !ok;
      if (ok) shown++;
    });
    none.hidden = shown !== 0;
  }
  function setView(view) {
    state.view = view; state.pos = "all";
    Array.prototype.forEach.call(views, function (v) { v.hidden = v.dataset.table !== view; });
    Array.prototype.forEach.call(document.querySelectorAll("[data-view]"), function (b) {
      b.setAttribute("aria-pressed", b.dataset.view === view ? "true" : "false");
    });
    chips.hidden = view === "goalies";
    Array.prototype.forEach.call(chips.querySelectorAll("[data-pos]"), function (c) {
      c.setAttribute("aria-pressed", c.dataset.pos === "all" ? "true" : "false");
    });
    apply();
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-view]"), function (b) {
    b.addEventListener("click", function () { setView(b.dataset.view); });
  });
  Array.prototype.forEach.call(chips.querySelectorAll("[data-pos]"), function (c) {
    c.addEventListener("click", function () {
      state.pos = c.dataset.pos;
      Array.prototype.forEach.call(chips.querySelectorAll("[data-pos]"), function (o) {
        o.setAttribute("aria-pressed", o === c ? "true" : "false");
      });
      apply();
    });
  });
  q.addEventListener("input", function () { state.q = q.value; apply(); });

  function cellValue(tr, i, type) {
    var td = tr.cells[i];
    var raw = td.dataset.v != null ? td.dataset.v : td.textContent.trim();
    return type === "num" ? parseFloat(raw) || 0 : raw.toLowerCase();
  }
  Array.prototype.forEach.call(document.querySelectorAll("table.sortable"), function (table) {
    var heads = table.tHead.rows[0].cells;
    Array.prototype.forEach.call(heads, function (th, i) {
      if (!th.dataset.sort) return;
      if (th.hasAttribute("data-default")) th.setAttribute("aria-sort", "descending");
      th.addEventListener("click", function () {
        var type = th.dataset.sort;
        // Text columns start A to Z; number columns start with the biggest.
        var dir = th.getAttribute("aria-sort") === "descending" ? 1
                : th.getAttribute("aria-sort") === "ascending" ? -1
                : (type === "num" ? -1 : 1);
        Array.prototype.forEach.call(heads, function (h) { h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
        var body = table.tBodies[0];
        Array.prototype.slice.call(body.rows).sort(function (a, b) {
          var x = cellValue(a, i, type), y = cellValue(b, i, type);
          return x < y ? -dir : x > y ? dir : 0;
        }).forEach(function (tr) { body.appendChild(tr); });
      });
    });
  });
  setView("skaters");
})();
