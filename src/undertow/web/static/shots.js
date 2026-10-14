/* App screenshot carousel: one slide visible at a time, via swipe, arrows or dots. */
(function () {
  var list = document.getElementById("shots");
  if (!list) return;
  var slides = Array.prototype.slice.call(list.children);
  var dotsWrap = document.getElementById("shots-dots");
  var prevBtn = document.querySelector(".shots-nav.prev");
  var nextBtn = document.querySelector(".shots-nav.next");
  var active = 0;

  function go(i) {
    i = Math.max(0, Math.min(slides.length - 1, i));
    slides[i].scrollIntoView({ behavior: "smooth", inline: "start", block: "nearest" });
  }

  var dots = slides.map(function (slide, i) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "dot";
    b.setAttribute("role", "tab");
    b.setAttribute("aria-label", "Screenshot " + (i + 1) + " of " + slides.length);
    b.addEventListener("click", function () { go(i); });
    dotsWrap.appendChild(b);
    return b;
  });

  function setActive(i) {
    active = i;
    dots.forEach(function (d, j) { d.setAttribute("aria-current", j === i ? "true" : "false"); });
    prevBtn.disabled = i === 0;
    nextBtn.disabled = i === slides.length - 1;
  }

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) setActive(slides.indexOf(e.target));
    });
  }, { root: list, threshold: 0.6 });
  slides.forEach(function (s) { io.observe(s); });
  setActive(0);

  prevBtn.addEventListener("click", function () { go(active - 1); });
  nextBtn.addEventListener("click", function () { go(active + 1); });
})();
