// Copy/share button for the "send this to a friend" card, and local-time display for draft times.
(function () {
  document.querySelectorAll("[data-share]").forEach(function (card) {
    var input = card.querySelector("input");
    var copy = card.querySelector("[data-copy]");
    var share = card.querySelector("[data-native-share]");
    var status = card.querySelector("[data-copy-status]");
    function say(text) { status.textContent = text; }
    copy.addEventListener("click", function () {
      var done = function () { say("Copied! Paste it anywhere."); };
      var fallback = function () {
        input.focus(); input.select();
        try { document.execCommand("copy") ? done() : say("Press Ctrl+C to copy the selected link."); }
        catch (e) { say("Press Ctrl+C to copy the selected link."); }
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(input.value).then(done, fallback);
      } else { fallback(); }
    });
    input.addEventListener("focus", function () { input.select(); });
    if (navigator.share) {
      share.hidden = false;
      share.addEventListener("click", function () {
        navigator.share({ title: "Undertow Fantasy", text: "PWHL fantasy leagues, built for people new to fantasy.", url: input.value }).catch(function () {});
      });
    }
  });

  // <time data-local datetime="2026-11-30T19:00:00Z">Mon, Nov 30 at 7:00 PM ET</time>
  // shows the visitor's own time zone; the Eastern text stays if this does not run.
  document.querySelectorAll("time[data-local]").forEach(function (el) {
    var d = new Date(el.getAttribute("datetime"));
    if (isNaN(d)) return;
    try {
      el.textContent = d.toLocaleString(undefined, {
        weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"
      });
    } catch (e) {}
  });
})();
