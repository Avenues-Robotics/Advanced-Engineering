// Remember which sidebar sections the reader expanded, across page loads.
(function () {
  var KEY = "nav-open-sections";
  var open = [];
  try { open = JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) {}

  document.querySelectorAll(".sidebar details[data-key]").forEach(function (d) {
    if (open.indexOf(d.dataset.key) !== -1) d.open = true;

    d.querySelector("summary").addEventListener("click", function () {
      setTimeout(function () {
        var set = new Set(open);
        if (d.open) set.add(d.dataset.key); else set.delete(d.dataset.key);
        open = Array.from(set);
        try { localStorage.setItem(KEY, JSON.stringify(open)); } catch (e) {}
      }, 0);
    });
  });
})();
