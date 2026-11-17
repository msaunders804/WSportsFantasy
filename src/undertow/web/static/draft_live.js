/* The real draft room. The server owns the draft: this page polls /draft/state, shows it, and sends
   picks, queue changes and commissioner controls back. It never decides whose turn it is or
   whether a pick is legal; the server does, and its answer is what gets shown. */
(function () {
  "use strict";
  var cfg = JSON.parse(document.getElementById("draft-data").textContent);
  var N = cfg.teams.length, ME = cfg.me, ROUNDS = cfg.rounds, TOTAL = N * ROUNDS;
  var BASE = "/leagues/" + cfg.league_id + "/draft";
  var POLL_MS = 1500, POLL_HIDDEN_MS = 5000;
  var byId = {};
  cfg.pool.forEach(function (p) { byId[p.id] = p; });
  var root = document.getElementById("dr");
  var $ = function (id) { return document.getElementById(id); };
  var desktop = window.matchMedia("(min-width: 1000px)");

  var st = {
    s: null, queue: [], deadline: null, tab: desktop.matches ? "players" : "board",
    pos: "all", q: "", pending: null, notice: "", busy: false, saving: false,
    offline: false, lastPick: 0, wasMine: false, titleBase: document.title
  };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function snake(pickNo) {
    var rnd = Math.floor((pickNo - 1) / N), i = (pickNo - 1) % N;
    return { round: rnd + 1, team: rnd % 2 === 0 ? i : N - 1 - i };
  }
  function surname(name) { var p = name.split(" "); return p[p.length - 1]; }
  function fmtTime(s) { return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
  function ordinal(n) {
    var v = n % 100;
    if (v >= 11 && v <= 13) return n + "th";
    return n + (["th", "st", "nd", "rd"][n % 10] || "th");
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function drafted() { var d = {}; (st.s ? st.s.picks : []).forEach(function (p) { d[p[2]] = true; }); return d; }
  function live() { return st.s && st.s.status === "live"; }
  function onClock() { return live() && st.s.my_team != null && st.s.on_clock === st.s.my_team; }
  function remaining() {
    if (!st.s) return 0;
    if (st.s.status === "paused") return st.s.seconds_left || 0;
    if (st.deadline == null) return 0;
    return Math.max(0, Math.ceil((st.deadline - Date.now()) / 1000));
  }
  function teamIndex(teamId) {
    for (var i = 0; i < N; i++) { if (cfg.teams[i].id === teamId) return i; }
    return -1;
  }

  /* ---------- server ---------- */
  function api(method, path, body) {
    var opts = { method: method, credentials: "same-origin", headers: { "X-CSRF-Token": cfg.csrf } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(BASE + path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        return { ok: r.ok, status: r.status, json: j };
      });
    });
  }

  function poll() {
    api("GET", "/state").then(function (res) {
      if (res.status === 404 || res.status === 403 || res.status === 401) { location.reload(); return; }
      if (!res.ok) throw new Error("state " + res.status);
      st.offline = false;
      apply(res.json);
    }).catch(function () {
      st.offline = true;
      renderClock();
    }).then(function () {
      setTimeout(poll, document.hidden ? POLL_HIDDEN_MS : POLL_MS);
    });
  }

  function apply(s) {
    var prev = st.s;
    var changed = !prev || s.version !== prev.version || s.picks.length !== prev.picks.length ||
      s.my_team !== prev.my_team;
    st.s = s;
    st.deadline = s.seconds_left == null ? null : Date.now() + s.seconds_left * 1000;
    var queueChanged = !st.saving && JSON.stringify(st.queue) !== JSON.stringify(s.queue);
    if (!st.saving) st.queue = s.queue.slice();
    var mine = onClock();
    if (mine && !st.wasMine) { document.title = "★ Your pick! " + st.titleBase; }
    if (!mine && st.wasMine) { document.title = st.titleBase; }
    st.wasMine = mine;
    if (changed) renderAll();
    else { renderClock(); renderControls(); if (queueChanged) { renderPlayers(); renderQueue(); } }
  }

  function makePick(id) {
    if (!onClock() || st.busy) return;
    st.busy = true; st.notice = "";
    api("POST", "/pick", { player_id: id }).then(function (res) {
      st.busy = false;
      if (res.ok) { apply(res.json.state); return; }
      st.notice = res.json.error || "That pick did not go through.";
      if (res.json.state) apply(res.json.state); else renderClock();
    }).catch(function () {
      st.busy = false; st.notice = "We could not reach the server. Check your connection and try again."; renderClock();
    });
  }

  function control(action) {
    api("POST", "/control", { action: action }).then(function (res) {
      if (res.ok) { st.notice = ""; apply(res.json.state); return; }
      st.notice = res.json.error || "That did not work.";
      renderClock();
    }).catch(function () { st.notice = "We could not reach the server."; renderClock(); });
  }

  function saveQueue() {
    st.saving = true;
    api("POST", "/queue", { player_ids: st.queue }).then(function (res) {
      st.saving = false;
      if (res.ok) st.queue = res.json.queue; else st.notice = res.json.error || "Could not save your queue.";
      renderPlayers(); renderQueue(); renderClock();
    }).catch(function () { st.saving = false; st.notice = "Could not save your queue."; renderClock(); });
  }

  /* ---------- render ---------- */
  function renderAll() {
    renderClock(); renderControls(); renderRec(); renderFeed(); renderBoard();
    renderPlayers(); renderQueue(); renderTeam();
  }

  function picksUntilMine(s) {
    if (ME < 0) return null;
    for (var n = s.pick_no; n <= TOTAL; n++) { if (snake(n).team === ME) return n - s.pick_no; }
    return null;
  }

  function renderClock() {
    var box = $("clock"), s = st.s;
    box.textContent = "";
    box.className = "dr-clock";
    if (!s) { box.appendChild(el("div", "what", "Loading the draft…")); return; }
    var what = el("div"), time = el("div", "time");
    if (s.status === "complete") {
      what.appendChild(el("div", "what", "Draft complete"));
      what.appendChild(el("div", "sub", "Your roster is under My team."));
      var back = el("a", "mini", "Back to the league"); back.href = "/leagues/" + cfg.league_id;
      time.appendChild(back);
    } else if (s.status === "paused") {
      what.appendChild(el("div", "what", "Draft paused"));
      what.appendChild(el("div", "sub", "The commissioner paused the draft. " + remaining() + " seconds will be left on the clock when it resumes."));
    } else if (onClock()) {
      box.classList.add("me");
      what.appendChild(el("div", "what", "You're on the clock"));
      what.appendChild(el("div", "sub", "Round " + s.round + " · Pick " + s.pick_no + " overall"));
      var left = remaining();
      time.textContent = left > 0 ? fmtTime(left) : "0:00";
      if (left <= 10) time.classList.add("low");
    } else {
      var idx = teamIndex(s.on_clock), until = picksUntilMine(s);
      what.appendChild(el("div", "what", (idx >= 0 ? cfg.teams[idx].name : "A team") + " is picking"));
      what.appendChild(el("div", "sub", "Round " + s.round + " · Pick " + s.pick_no + (until != null ? " · You're up in " + plural(until, "pick", "picks") : "")));
      time.textContent = fmtTime(remaining());
    }
    if (live() && remaining() === 0) what.appendChild(el("div", "sub", "Time's up. Picking now…"));
    box.appendChild(what); box.appendChild(time);
    if (st.offline) box.appendChild(el("p", "notice", "Connection lost. Retrying… Your picks are safe on the server."));
    else if (st.notice) box.appendChild(el("p", "notice", st.notice));
  }

  function renderControls() {
    var box = $("controls"), s = st.s;
    box.textContent = "";
    if (!s || !s.can_manage || s.status === "complete") { box.hidden = true; return; }
    box.hidden = false;
    box.appendChild(el("span", "muted", "Commissioner"));
    var toggle = el("button", "mini", s.status === "paused" ? "Resume draft" : "Pause draft");
    toggle.type = "button";
    toggle.addEventListener("click", function () { control(s.status === "paused" ? "resume" : "pause"); });
    box.appendChild(toggle);
    var undo = el("button", "mini", "Undo last pick");
    undo.type = "button"; undo.disabled = s.picks.length === 0;
    undo.addEventListener("click", function () {
      var last = s.picks[s.picks.length - 1], p = last && byId[last[2]];
      if (window.confirm("Take back pick " + last[0] + (p ? " (" + p.name + ")" : "") + "? That team goes back on the clock.")) control("undo");
    });
    box.appendChild(undo);
  }

  function renderRec() {
    var box = $("rec"), s = st.s;
    box.textContent = "";
    if (!s || !onClock() || !s.recommend.length) { box.hidden = true; return; }
    box.hidden = false;
    var r = s.recommend[0], p = byId[r.id];
    if (!p) { box.hidden = true; return; }
    box.appendChild(el("h2", null, "Recommended for you"));
    var main = el("div", "rec-main"), info = el("div");
    info.appendChild(el("div", "rec-name", p.name));
    info.appendChild(el("div", "meta muted", p.pos + " · " + p.team + " · " + p.fpg.toFixed(1) + " pts/game last season"));
    main.appendChild(info);
    var btn = el("button", "btn", "Draft " + surname(p.name));
    btn.type = "button"; btn.addEventListener("click", function () { openConfirm(r.id, r.reason); });
    main.appendChild(btn);
    box.appendChild(main);
    box.appendChild(el("p", "rec-why", r.reason));
    if (s.recommend.length > 1) {
      var more = el("details", "rec-more");
      more.appendChild(el("summary", null, "Other good options"));
      s.recommend.slice(1).forEach(function (o) {
        var q = byId[o.id]; if (!q) return;
        var row = el("div", "rec-alt");
        row.appendChild(el("div", null, q.name + " (" + q.pos + " · " + q.team + ")"));
        var b = el("button", "mini", "Draft"); b.type = "button";
        b.addEventListener("click", function () { openConfirm(o.id, o.reason); });
        row.appendChild(b);
        row.appendChild(el("p", "why", o.reason));
        more.appendChild(row);
      });
      box.appendChild(more);
    }
  }

  function renderFeed() {
    var box = $("feed");
    box.textContent = "";
    if (!st.s) return;
    st.s.picks.slice(-6).reverse().forEach(function (pk) {
      var p = byId[pk[2]], idx = teamIndex(pk[1]);
      var chip = el("div", "feed-chip" + (idx === ME ? " mine" : ""));
      chip.appendChild(el("span", null, "#" + pk[0] + " " + (p ? p.name + " (" + p.pos + ")" : "a player") + (pk[3] ? " · auto" : "")));
      chip.appendChild(el("span", "who", idx === ME ? "You" : (idx >= 0 ? cfg.teams[idx].name : "")));
      box.appendChild(chip);
    });
  }

  var cells = [];
  function buildBoard() {
    var t = $("board");
    t.textContent = "";
    cells = [];
    var head = el("tr");
    head.appendChild(el("th", "rnd", "Rd"));
    cfg.teams.forEach(function (tm, i) {
      var th = el("th", i === ME ? "me" : "");
      th.appendChild(document.createTextNode(i === ME ? "You" : tm.name));
      th.appendChild(el("small", null, i === ME ? tm.name : tm.owner));
      th.title = tm.name;
      head.appendChild(th);
    });
    var thead = el("thead"); thead.appendChild(head); t.appendChild(thead);
    var tb = el("tbody");
    for (var r = 1; r <= ROUNDS; r++) {
      var tr = el("tr");
      tr.appendChild(el("td", "rnd", r + (r % 2 === 1 ? " →" : " ←")));
      for (var c = 0; c < N; c++) {
        var pickNo = (r - 1) * N + (r % 2 === 1 ? c : N - 1 - c) + 1;
        var td = el("td", c === ME ? "mine" : "");
        td.appendChild(el("span", "pn", r + "." + String(r % 2 === 1 ? c + 1 : N - c).padStart(2, "0")));
        cells[pickNo] = td;
        tr.appendChild(td);
      }
      tb.appendChild(tr);
    }
    t.appendChild(tb);
  }

  function renderBoard() {
    if (!st.s) return;
    var byNo = {};
    st.s.picks.forEach(function (pk) { byNo[pk[0]] = pk; });
    var nextNo = st.s.status === "complete" ? null : st.s.pick_no;
    for (var n = 1; n <= TOTAL; n++) {
      var td = cells[n], pk = byNo[n], sl = snake(n);
      var keep = td.querySelector(".pn");
      td.textContent = "";
      td.appendChild(keep);
      td.className = sl.team === ME ? "mine" : "";
      if (pk) {
        var p = byId[pk[2]];
        if (p) {
          td.classList.add("pos-" + p.pos);
          td.appendChild(el("span", "nm", surname(p.name)));
          td.appendChild(el("span", "ps", p.pos + " · " + p.team));
          td.title = p.name + (pk[3] ? " (auto pick)" : "");
        }
      }
      if (n === nextNo) td.classList.add("now");
    }
    var focusNo = nextNo || (st.s.picks.length ? TOTAL : null);
    if (focusNo && focusNo !== st.lastPick && cells[focusNo]) {
      st.lastPick = focusNo;
      var sc = $("board-scroll"), td2 = cells[focusNo];
      sc.scrollTo({
        left: Math.max(0, td2.offsetLeft - sc.clientWidth / 2 + td2.offsetWidth / 2),
        top: Math.max(0, td2.offsetTop - sc.clientHeight / 2 + td2.offsetHeight / 2),
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"
      });
    }
  }

  function renderPlayers() {
    var list = $("plist"), d = drafted(), q = st.q.trim().toLowerCase(), shown = 0;
    var recId = onClock() && st.s.recommend.length ? st.s.recommend[0].id : null;
    var queued = {}; st.queue.forEach(function (id) { queued[id] = true; });
    var can = onClock();
    var frag = document.createDocumentFragment();
    cfg.pool.forEach(function (p) {
      if (d[p.id]) return;
      if (st.pos !== "all" && p.pos !== st.pos) return;
      if (q && p.name.toLowerCase().indexOf(q) === -1) return;
      shown++;
      var li = el("li", "dr-row pos-" + p.pos);
      li.appendChild(el("span", "rk", String(p.rank)));
      var info = el("div"), nm = el("div", "nm", p.name);
      if (p.id === recId) nm.appendChild(el("span", "recbadge", "Recommended"));
      info.appendChild(nm);
      info.appendChild(el("div", "meta", p.pos + " · " + p.team + " · " + p.gp + " GP · " + ordinal(p.pos_rank) + " " + p.pos));
      li.appendChild(info);
      var fp = el("div", "fp"); fp.appendChild(el("strong", null, p.fpg.toFixed(1))); fp.appendChild(el("small", "muted", " pts/g"));
      li.appendChild(fp);
      var star = el("button", "star", queued[p.id] ? "★" : "☆");
      star.type = "button"; star.setAttribute("aria-pressed", queued[p.id] ? "true" : "false");
      star.setAttribute("aria-label", (queued[p.id] ? "Remove " : "Add ") + p.name + (queued[p.id] ? " from" : " to") + " queue");
      star.disabled = ME < 0;
      star.addEventListener("click", function () { toggleQueue(p.id); });
      li.appendChild(star);
      var dr = el("button", "mini add", "Draft");
      dr.type = "button"; dr.disabled = !can;
      dr.title = can ? "" : "You can draft when you're on the clock";
      dr.addEventListener("click", function () { openConfirm(p.id, null); });
      li.appendChild(dr);
      frag.appendChild(li);
    });
    list.textContent = "";
    list.appendChild(frag);
    $("pempty").hidden = shown !== 0;
  }

  function toggleQueue(id) {
    var i = st.queue.indexOf(id);
    if (i === -1) st.queue.push(id); else st.queue.splice(i, 1);
    renderPlayers(); renderQueue(); saveQueue();
  }

  function renderQueue() {
    var ol = $("queue"), d = drafted();
    st.queue = st.queue.filter(function (id) { return !d[id]; });
    ol.textContent = "";
    st.queue.forEach(function (id, i) {
      var p = byId[id]; if (!p) return;
      var li = el("li");
      li.appendChild(el("span", "rk muted", String(i + 1)));
      var info = el("div"); info.appendChild(el("div", "nm", p.name)); info.appendChild(el("div", "meta muted", p.pos + " · " + p.team));
      li.appendChild(info);
      [["▲", -1, "Move up"], ["▼", 1, "Move down"]].forEach(function (b) {
        var btn = el("button", "icon-sm", b[0]); btn.type = "button"; btn.setAttribute("aria-label", b[2] + " " + p.name);
        btn.addEventListener("click", function () {
          var j = i + b[1]; if (j < 0 || j >= st.queue.length) return;
          var t = st.queue[i]; st.queue[i] = st.queue[j]; st.queue[j] = t; renderQueue(); saveQueue();
        });
        li.appendChild(btn);
      });
      var rm = el("button", "icon-sm", "✕"); rm.type = "button"; rm.setAttribute("aria-label", "Remove " + p.name + " from queue");
      rm.addEventListener("click", function () { toggleQueue(id); });
      li.appendChild(rm);
      ol.appendChild(li);
    });
    $("qempty").hidden = st.queue.length > 0;
    $("queue-count").textContent = st.queue.length ? String(st.queue.length) : "";
  }

  function renderTeam() {
    var s = st.s, box = $("needs"), ul = $("slots");
    box.textContent = ""; ul.textContent = "";
    if (!s) return;
    if (!s.needs || !s.roster) {
      box.textContent = s.my_team == null ? "You do not have a team in this league." : "Your roster summary is not available right now.";
      return;
    }
    var n = s.needs, bits = [];
    if (n.F) bits.push(plural(n.F, "forward", "forwards"));
    if (n.D) bits.push(plural(n.D, "defender", "defenders"));
    if (n.G) bits.push(plural(n.G, "goalie", "goalies"));
    if (bits.length) box.appendChild(document.createTextNode("Still need to start: " + bits.join(", ") + ". "));
    else box.appendChild(el("span", "ok", "✓ Your starting lineup is full. "));
    box.appendChild(document.createTextNode(plural(n.picks_left, "pick", "picks") + " left."));
    [["F", cfg.slots.F], ["D", cfg.slots.D], ["UTIL", cfg.slots.UTIL], ["G", cfg.slots.G], ["BN", cfg.slots.BN]].forEach(function (r) {
      var ids = s.roster[r[0]] || [];
      for (var i = 0; i < r[1]; i++) {
        var li = el("li", ids[i] == null ? "empty" : "");
        li.appendChild(el("span", "slot-tag", r[0]));
        if (ids[i] != null && byId[ids[i]]) {
          var p = byId[ids[i]], info = el("div");
          info.appendChild(el("div", "nm", p.name));
          info.appendChild(el("div", "meta muted", p.pos + " · " + p.team + " · " + p.fpg.toFixed(1) + " pts/game"));
          li.appendChild(info);
        } else { li.appendChild(el("span", null, "Empty")); }
        ul.appendChild(li);
      }
    });
  }

  /* ---------- confirm sheet ---------- */
  function openConfirm(id, why) {
    if (!onClock()) return;
    var p = byId[id];
    st.pending = id;
    $("sheet-pos").textContent = "Round " + st.s.round + ", Pick " + st.s.pick_no;
    $("sheet-title").textContent = "Select " + p.name + " (" + p.pos + ")?";
    $("sheet-why").textContent = why || (p.pos + " · " + p.team + " · " + p.fpg.toFixed(1) + " pts/game last season");
    $("sheet").hidden = false;
    $("sheet-ok").focus();
  }
  function closeConfirm() { st.pending = null; $("sheet").hidden = true; }
  $("sheet-cancel").addEventListener("click", closeConfirm);
  $("sheet").addEventListener("click", function (e) { if (e.target === $("sheet")) closeConfirm(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !$("sheet").hidden) closeConfirm(); });
  $("sheet-ok").addEventListener("click", function () { var id = st.pending; closeConfirm(); if (id != null) makePick(id); });

  /* ---------- controls ---------- */
  function setTab(tab) {
    st.tab = tab;
    root.dataset.tab = tab;
    Array.prototype.forEach.call($("tabs").querySelectorAll("button"), function (b) { b.setAttribute("aria-pressed", b.dataset.tab === tab ? "true" : "false"); });
  }
  Array.prototype.forEach.call($("tabs").querySelectorAll("button"), function (b) { b.addEventListener("click", function () { setTab(b.dataset.tab); }); });
  Array.prototype.forEach.call(document.querySelectorAll("[data-pos]"), function (c) {
    c.addEventListener("click", function () {
      st.pos = c.dataset.pos;
      Array.prototype.forEach.call(document.querySelectorAll("[data-pos]"), function (o) { o.setAttribute("aria-pressed", o === c ? "true" : "false"); });
      renderPlayers();
    });
  });
  $("q").addEventListener("input", function () { st.q = $("q").value; renderPlayers(); });

  /* The clock text counts down locally between polls, so it does not jump once a second. */
  setInterval(function () { if (st.s && st.s.status !== "complete") renderClock(); }, 250);
  // A closed laptop or a background tab can stop the loop; catch up the moment we are visible.
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { api("GET", "/state").then(function (r) { if (r.ok) apply(r.json); }); } });

  $("basis").textContent = cfg.ranking_basis
    ? "Ratings use " + cfg.ranking_basis + " results. Players with no history are rated as average."
    : "No past-season stats are loaded, so every player is rated the same. Don't rely on the ratings.";
  setTab(st.tab);
  buildBoard();
  renderClock();
  poll();
})();
