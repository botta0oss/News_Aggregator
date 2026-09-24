// Applies the saved theme before first paint (dark is the default). Kept as a file: the CSP forbids inline scripts.
try {
  if (localStorage.getItem("theme") === "light") document.documentElement.dataset.theme = "light";
} catch (e) { /* storage unavailable: keep the default */ }
