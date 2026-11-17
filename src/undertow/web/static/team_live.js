/* Team tab: the Move sheet. The page lists only moves the server would accept; the server checks
   again when one is chosen, and its answer (or error) is what is shown. */
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("team-data").textContent);
  var csrf = JSON.parse(document.getElementById("team-csrf").textContent);
  var errorBox = document.getElementById("move-error");
  var back = null;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function label(slot) { return (data.labels && data.labels[slot]) || slot; }

  function close() { if (back) { back.remove(); back = null; } }

  function send(playerId, option, sheetError) {
    var body = { team_id: data.team_id, player_id: playerId, to_slot: option.slot };
    if (option.swap_with != null) body.swap_with = option.swap_with;
    fetch("/leagues/" + data.league_id + "/team/move", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, json: j }; });
    }).then(function (res) {
      if (res.ok) { location.reload(); return; }
      sheetError.textContent = res.json.error || "That move did not work. Reload and try again.";
      sheetError.hidden = false;
    }).catch(function () {
      sheetError.textContent = "We could not reach the server. Check your connection and try again.";
      sheetError.hidden = false;
    });
  }

  function open(playerId) {
    var options = data.moves[playerId] || [];
    close();
    back = el("div", "mv-back");
    back.addEventListener("click", function (e) { if (e.target === back) close(); });
    var sheet = el("div", "mv");
    sheet.setAttribute("role", "dialog"); sheet.setAttribute("aria-modal", "true");
    sheet.appendChild(el("h2", null, "Move " + (data.names[playerId] || "player")));
    var err = el("p", "alert", ""); err.hidden = true; err.setAttribute("role", "alert");
    sheet.appendChild(err);
    var list = el("ul");
    options.forEach(function (o) {
      var li = el("li"), b = el("button", null,
        o.swap_with != null ? "Swap with " + o.swap_name + " (" + label(o.slot) + ")" : "Move to " + label(o.slot));
      b.type = "button";
      b.addEventListener("click", function () { err.hidden = true; send(playerId, o, err); });
      li.appendChild(b); list.appendChild(li);
    });
    if (!options.length) list.appendChild(el("li", "none", "No moves available right now."));
    sheet.appendChild(list);
    var cancel = el("button", "mini", "Cancel"); cancel.type = "button";
    cancel.addEventListener("click", close);
    sheet.appendChild(cancel);
    back.appendChild(sheet);
    document.body.appendChild(back);
    var first = sheet.querySelector("button"); if (first) first.focus();
  }

  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  Array.prototype.forEach.call(document.querySelectorAll(".js-move"), function (b) {
    b.addEventListener("click", function () { open(parseInt(b.dataset.player, 10)); });
  });
  if (errorBox) errorBox.hidden = true;
})();
