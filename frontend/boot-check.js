// Shows a message instead of a blank page if the app does not start (e.g. stale cached
// modules after an update, or a server too busy to answer). Plain script: runs even when
// the app module fails to load.
(function () {
  var shown = false;
  function show() {
    if (window.__appStarted || shown) return;
    shown = true;
    var view = document.getElementById("view");
    if (!view) return;
    var box = document.createElement("div");
    box.className = "card empty boot-error";
    box.setAttribute("role", "alert");
    var title = document.createElement("h3");
    title.textContent = "L'interfaccia non si è avviata";
    var p1 = document.createElement("p");
    p1.textContent = "Di solito succede dopo un aggiornamento: il browser usa file vecchi dalla cache. " +
      "Ricarica la pagina forzando l'aggiornamento (Ctrl+F5, oppure Cmd+Shift+R su Mac).";
    var p2 = document.createElement("p");
    p2.className = "muted small";
    p2.textContent = "Se il problema resta, il server potrebbe essere bloccato: controlla i log del container.";
    var btn = document.createElement("button");
    btn.className = "btn btn-primary";
    btn.type = "button";
    btn.textContent = "Ricarica";
    btn.addEventListener("click", function () { window.location.reload(); });
    box.append(title, p1, p2, btn);
    view.replaceChildren(box);
  }
  window.addEventListener("load", function () { setTimeout(show, 8000); });
})();
