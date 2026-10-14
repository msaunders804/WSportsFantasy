/* Demo draft room. The browser keeps the pick list; every decision comes from the server
   (/preview/draft/step) so the rules live in one place (services/draft.py). */
(function () {
  "use strict";
  var cfg = JSON.parse(document.getElementById("draft-data").textContent);
  var N = cfg.teams.length, ME = cfg.me, ROUNDS = cfg.rounds, TOTAL = N * ROUNDS;
  var byId = {};
  cfg.pool.forEach(function (p) { byId[p.id] = p; });
  var root = document.getElementById("dr");
  var $ = function (id) { return document.getElementById(id); };
  var desktop = window.matchMedia("(min-width: 1000px)");

  var st = {
    run: 0, started: false, picks: [], queue: [], auto: false, s: null, remain: cfg.clock,
    timer: null, busy: false, seed: 1, tab: desktop.matches ? "players" : "board",
    pos: "all", q: "", pending: null, notice: ""
  };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function snake(pickNo) {
    var rnd = Math.floor((pickNo - 1) / N), i = (pickNo - 1) % N;
    return { round: rnd + 1, team: rnd % 2 === 0 ? i : N - 1 - i };
  }
  function surname(name) { var p = name.split(" "); return p[p.length - 1]; }
  function drafted() { var d = {}; st.picks.forEach(function (id) { d[id] = true; }); return d; }
  function onClock() { return st.s && !st.s.complete && st.s.on_clock === ME; }
  function fmtTime(s) { return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  /* ---------- server ---------- */
  function step() {
    return fetch("/preview/draft/step", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ picks: st.picks, seed: st.seed })
    }).then(function (r) { if (!r.ok) throw new Error("step failed"); return r.json(); });
  }

  async function loop() {
    if (st.busy || !st.started) return;
    st.busy = true;
    var run = st.run, s;
    try { s = await step(); } catch (e) { st.busy = false; st.notice = "Something went wrong on our end. Your picks are safe. Try restarting."; renderClock(); return; }
    if (run !== st.run) { st.busy = false; return; }
    st.s = s;
    renderAll();
    if (s.complete) { stopTimer(); st.busy = false; return; }
    if (s.on_clock === ME) {
      st.busy = false;
      startTimer();
      if (st.auto) { await sleep(700); if (run === st.run && st.s === s) autoPick(""); }
      return;
    }
    stopTimer();
    await sleep(cfg.pace);
    if (run !== st.run) { st.busy = false; return; }
    st.picks.push(s.cpu_pick);
    st.busy = false;
    loop();
  }

  function makePick(id) {
    if (!onClock() || st.busy) return;
    st.picks.push(id);
    st.queue = st.queue.filter(function (q) { return q !== id; });
    st.notice = "";
    stopTimer();
    loop();
  }

  function autoPick(why) {
    if (!onClock()) return;
    var d = drafted(), id = null, fromQueue = false;
    for (var i = 0; i < st.queue.length; i++) { if (!d[st.queue[i]]) { id = st.queue[i]; fromQueue = true; break; } }
    if (id == null) id = st.s.recommend[0].id;
    var p = byId[id];
    st.notice = (why === "time" ? "Time ran out, so we picked for you: " : "Auto-draft picked ") + p.name + (fromQueue ? " from your queue." : ".");
    makePick(id);
  }

  /* ---------- timer ---------- */
  function startTimer() {
    stopTimer();
    st.remain = cfg.clock;
    renderClock();
    st.timer = setInterval(function () {
      st.remain -= 1;
      if (st.remain <= 0) { stopTimer(); autoPick("time"); return; }
      renderClock();
    }, 1000);
  }
  function stopTimer() { if (st.timer) { clearInterval(st.timer); st.timer = null; } }

  /* ---------- render ---------- */
  function renderAll() { renderClock(); renderRec(); renderFeed(); renderBoard(); renderPlayers(); renderQueue(); renderTeam(); }

  function picksUntilMine(s) {
    for (var n = s.pick_no; n <= TOTAL; n++) { if (snake(n).team === ME) return n - s.pick_no; }
    return null;
  }

  function renderClock() {
    var box = $("clock"), s = st.s;
    box.textContent = "";
    box.className = "dr-clock";
    if (!s) return;
    var what = el("div"), time = el("div", "time");
    if (s.complete) {
      what.appendChild(el("div", "what", "Draft complete"));
      what.appendChild(el("div", "sub", "Check out your team in My team."));
      var again = el("button", "mini", "Start over");
      again.type = "button"; again.addEventListener("click", restart);
      time.appendChild(again);
    } else if (s.on_clock === ME) {
      box.classList.add("me");
      what.appendChild(el("div", "what", "You're on the clock"));
      what.appendChild(el("div", "sub", "Round " + s.round + " · Pick " + s.pick_no + " overall"));
      time.textContent = fmtTime(Math.max(st.remain, 0));
      if (st.remain <= 10) time.classList.add("low");
    } else {
      var t = cfg.teams[s.on_clock], until = picksUntilMine(s);
      what.appendChild(el("div", "what", t.name + " is picking"));
      what.appendChild(el("div", "sub", "Round " + s.round + " · Pick " + s.pick_no + (until != null ? " · You're up in " + plural(until, "pick", "picks") : "")));
    }
    box.appendChild(what); box.appendChild(time);
    var auto = el("label", "auto");
    var sw = el("input", "switch"); sw.type = "checkbox"; sw.checked = st.auto; sw.setAttribute("aria-label", "Auto-draft");
    sw.addEventListener("change", function () { st.auto = sw.checked; if (st.auto && onClock()) autoPick(""); });
    auto.appendChild(sw);
    auto.appendChild(document.createTextNode(" Auto-draft for me"));
    box.appendChild(auto);
    if (st.notice) box.appendChild(el("p", "notice", st.notice));
  }

  function renderRec() {
    var box = $("rec"), s = st.s;
    box.textContent = "";
    if (!s || !onClock()) { box.hidden = true; return; }
    box.hidden = false;
    var r = s.recommend[0], p = byId[r.id];
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
        var q = byId[o.id], row = el("div", "rec-alt");
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
    var recent = st.picks.slice(-6).reverse();
    recent.forEach(function (id, i) {
      var n = st.picks.length - i, sl = snake(n), p = byId[id];
      var chip = el("div", "feed-chip" + (sl.team === ME ? " mine" : ""));
      chip.appendChild(el("span", null, "#" + n + " " + p.name + " (" + p.pos + ")"));
      chip.appendChild(el("span", "who", sl.team === ME ? "You" : cfg.teams[sl.team].name));
      box.appendChild(chip);
    });
  }

  var cells = [];
  function buildBoard() {
    var t = $("board");
    t.textContent = "";
    cells = [];
    var head = el("tr"), corner = el("th", "rnd", "Rd");
    head.appendChild(corner);
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
      var tr = el("tr"), lead = el("td", "rnd", r + (r % 2 === 1 ? " →" : " ←"));
      tr.appendChild(lead);
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
    var nextNo = st.s && !st.s.complete ? st.s.pick_no : null;
    for (var n = 1; n <= TOTAL; n++) {
      var td = cells[n], id = st.picks[n - 1], sl = snake(n);
      var keep = td.querySelector(".pn");
      td.textContent = "";
      td.appendChild(keep);
      td.className = sl.team === ME ? "mine" : "";
      if (id != null) {
        var p = byId[id];
        td.classList.add("pos-" + p.pos);
        td.appendChild(el("span", "nm", surname(p.name)));
        td.appendChild(el("span", "ps", p.pos + " · " + p.team));
        td.title = p.name;
      }
      if (n === nextNo) td.classList.add("now");
    }
    var focusNo = nextNo;
    if (!focusNo && st.picks.length) {
      // Draft over: bring your own column into view.
      for (var k = TOTAL; k >= 1; k--) { if (snake(k).team === ME) { focusNo = k; break; } }
    }
    if (focusNo && cells[focusNo]) {
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
    var recIds = {};
    if (onClock()) st.s.recommend.forEach(function (r, i) { if (i === 0) recIds[r.id] = true; });
    var queued = {}; st.queue.forEach(function (id) { queued[id] = true; });
    var frag = document.createDocumentFragment();
    cfg.pool.forEach(function (p) {
      if (d[p.id]) return;
      if (st.pos !== "all" && p.pos !== st.pos) return;
      if (q && p.name.toLowerCase().indexOf(q) === -1) return;
      shown++;
      var li = el("li", "dr-row pos-" + p.pos);
      li.appendChild(el("span", "rk", String(p.rank)));
      var info = el("div"), nm = el("div", "nm", p.name);
      if (recIds[p.id]) nm.appendChild(el("span", "recbadge", "Recommended"));
      info.appendChild(nm);
      info.appendChild(el("div", "meta", p.pos + " · " + p.team + " · " + p.gp + " GP · " + p.pos_rank + (p.pos === "F" ? "th F" : p.pos === "D" ? "th D" : "th G")));
      li.appendChild(info);
      var fp = el("div", "fp"); fp.appendChild(el("strong", null, p.fpg.toFixed(1))); fp.appendChild(el("small", "muted", " pts/g"));
      li.appendChild(fp);
      var star = el("button", "star", queued[p.id] ? "★" : "☆");
      star.type = "button"; star.setAttribute("aria-pressed", queued[p.id] ? "true" : "false");
      star.setAttribute("aria-label", (queued[p.id] ? "Remove " : "Add ") + p.name + (queued[p.id] ? " from" : " to") + " queue");
      star.addEventListener("click", function () { toggleQueue(p.id); });
      li.appendChild(star);
      var dr = el("button", "mini add", "Draft");
      dr.type = "button"; dr.disabled = !onClock();
      dr.title = onClock() ? "" : "You can draft when you're on the clock";
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
    renderPlayers(); renderQueue();
  }

  function renderQueue() {
    var ol = $("queue"), d = drafted();
    st.queue = st.queue.filter(function (id) { return !d[id]; });
    ol.textContent = "";
    st.queue.forEach(function (id, i) {
      var p = byId[id], li = el("li");
      li.appendChild(el("span", "rk muted", String(i + 1)));
      var info = el("div"); info.appendChild(el("div", "nm", p.name)); info.appendChild(el("div", "meta muted", p.pos + " · " + p.team));
      li.appendChild(info);
      [["▲", -1, "Move up"], ["▼", 1, "Move down"]].forEach(function (b) {
        var btn = el("button", "icon-sm", b[0]); btn.type = "button"; btn.setAttribute("aria-label", b[2] + " " + p.name);
        btn.addEventListener("click", function () {
          var j = i + b[1]; if (j < 0 || j >= st.queue.length) return;
          var t = st.queue[i]; st.queue[i] = st.queue[j]; st.queue[j] = t; renderQueue(); });
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
    var s = st.s;
    if (!s) return;
    var n = s.needs, box = $("needs");
    box.textContent = "";
    var bits = [];
    if (n.F) bits.push(plural(n.F, "forward", "forwards"));
    if (n.D) bits.push(plural(n.D, "defender", "defenders"));
    if (n.G) bits.push(plural(n.G, "goalie", "goalies"));
    if (bits.length) {
      box.appendChild(document.createTextNode("Still need to start: " + bits.join(", ") + ". "));
    } else {
      box.appendChild(el("span", "ok", "✓ Your starting lineup is full. "));
    }
    box.appendChild(document.createTextNode(plural(n.picks_left, "pick", "picks") + " left."));
    var ul = $("slots"); ul.textContent = "";
    var rows = [["F", 3, "F"], ["D", 2, "D"], ["UTIL", 1, "UTIL"], ["G", 1, "G"], ["BN", 4, "BN"]];
    rows.forEach(function (r) {
      var ids = s.roster[r[0]];
      for (var i = 0; i < r[1]; i++) {
        var li = el("li", ids[i] == null ? "empty" : "");
        li.appendChild(el("span", "slot-tag", r[2]));
        if (ids[i] != null) {
          var p = byId[ids[i]];
          var info = el("div"); info.appendChild(el("div", "nm", p.name)); info.appendChild(el("div", "meta muted", p.pos + " · " + p.team + " · " + p.fpg.toFixed(1) + " pts/game"));
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

  function start() {
    st.run += 1; st.started = true; st.picks = []; st.queue = []; st.s = null; st.notice = "";
    st.seed = 1 + Math.floor(Math.random() * 99999);
    root.dataset.state = "live";
    $("live").hidden = false;
    setTab(st.tab);
    buildBoard();
    renderAll();
    st.busy = false;
    loop();
  }
  function restart() { stopTimer(); closeConfirm(); start(); }
  $("start").addEventListener("click", start);
  setTab(st.tab);
  if (location.search.indexOf("autostart") !== -1) start();
})();
