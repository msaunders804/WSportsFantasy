/* Matchup tab: while a week is live, fetch this same page every 30 seconds and swap in the fresh
   scoreboard. The server does all the scoring; this only replaces what is shown. */
(function () {
  "use strict";
  var region = document.getElementById("live-region");
  if (!region) return;
  var every = parseInt(region.getAttribute("data-refresh") || "0", 10);
  if (!every) return;
  var busy = false;
  function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    fetch(location.href, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("refresh " + r.status); return r.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.getElementById("live-region");
        if (!fresh) return;
        region.innerHTML = fresh.innerHTML;
        // The week finished (or has not started): stop polling by reloading the settled page once.
        if (parseInt(fresh.getAttribute("data-refresh") || "0", 10) === 0) { clearInterval(timer); }
      })
      .catch(function () { /* keep the last good scoreboard; try again next time */ })
      .then(function () { busy = false; });
  }
  var timer = setInterval(refresh, every * 1000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(); });
})();
