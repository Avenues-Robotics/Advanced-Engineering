// The </> button beside the breadcrumbs: shows an <iframe> snippet for
// embedding this page's content (its /embed/ version) in Canvas etc.
(function () {
  var btn = document.querySelector(".embed-btn");
  var dialog = document.querySelector(".embed-dialog");
  if (!btn || !dialog) return;

  var code = dialog.querySelector("textarea");
  var height = dialog.querySelector("input[type=number]");
  var copy = dialog.querySelector(".embed-copy");
  var src = new URL(btn.dataset.embed, location.href).href;
  var title = btn.dataset.title.replace(/&/g, "&amp;").replace(/"/g, "&quot;");

  function update() {
    var h = parseInt(height.value, 10) || 800;
    code.value = '<iframe src="' + src + '" title="' + title + '" width="100%" height="' + h +
      '" style="border: 0;" loading="lazy"></iframe>';
  }

  btn.addEventListener("click", function () {
    update();
    copy.textContent = "Copy code";
    dialog.showModal();
    code.select();
  });
  height.addEventListener("input", update);
  code.addEventListener("focus", function () { code.select(); });

  copy.addEventListener("click", function () {
    function done() { copy.textContent = "Copied!"; }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(code.value).then(done, fallback);
    } else {
      fallback();
    }
    function fallback() {
      code.select();
      try { if (document.execCommand("copy")) done(); } catch (e) {}
    }
  });

  // Clicking the dimmed backdrop closes it too.
  dialog.addEventListener("click", function (e) {
    if (e.target === dialog) dialog.close();
  });
})();
