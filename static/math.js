// Typesets Notion equations (inline $`...`$ and $$ blocks) with KaTeX.
// If KaTeX fails to load, the raw LaTeX stays visible instead.
(function () {
  if (!window.katex) return;
  document.querySelectorAll(".math").forEach(function (el) {
    katex.render(el.textContent, el, {
      displayMode: el.classList.contains("math-display"),
      throwOnError: false,
    });
  });
})();
