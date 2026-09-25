// Navigation: one definition for the desktop sidebar, the mobile bottom bar and the "Altro" sheet.
import { t } from "./i18n.js";
import { h, icon } from "./ui.js";

// Sections grouped by what the user is doing: find signals, browse markets, read news, check results
export const NAV = [
  { group: t("Segnali"), items: [
    { id: "opportunita", href: "#/opportunita", label: t("Opportunità"), icon: "up" },
    { id: "allerte", href: "#/allerte", label: t("Allerte"), icon: "bell" },
  ] },
  { group: t("Mercati"), items: [
    { id: "mercati", href: "#/mercati", label: t("Sì / No"), icon: "toggle", short: t("Mercati") },
    { id: "multi", href: "#/multi", label: t("Più esiti"), icon: "list" },
  ] },
  { group: t("Notizie"), items: [
    { id: "notizie", href: "#/notizie", label: t("Notizie"), icon: "news" },
  ] },
  { group: t("Risultati"), items: [
    { id: "portafoglio", href: "#/portafoglio", label: t("Portafoglio"), icon: "wallet" },
    { id: "backtest", href: "#/backtest", label: t("Backtest"), icon: "history" },
    { id: "calibrazione", href: "#/calibrazione", label: t("Calibrazione"), icon: "target" },
  ] },
];

export const FOOTER_NAV = [
  { id: "impostazioni", href: "#/impostazioni", label: t("Impostazioni"), icon: "settings" },
  { id: "uso", href: "#/uso", label: t("Uso e costi"), icon: "gauge" },
  { id: "metodo", href: "#/metodo", label: t("Come funziona"), icon: "help" },
];

// Mobile bottom bar: the destinations used most (at most 4, plus "Altro")
const BOTTOM = ["opportunita", "mercati", "notizie", "allerte"];

const allItems = () => [...NAV.flatMap((g) => g.items), ...FOOTER_NAV];

function link(item, cls) {
  return h("a", { class: cls, href: item.href, "data-nav": item.id, title: item.label },
    icon(item.icon), h("span", { class: "nav-label" }, item.short && cls === "bottom-item" ? item.short : item.label),
    h("span", { class: "nav-badge", "data-badge": item.id, hidden: true }));
}

/** Renders the three navigation areas; `user` gets the account link and logout. */
export function renderNav({ user, onLogout }) {
  const side = document.getElementById("side-nav");
  side.replaceChildren(...NAV.map((g) => h("div", { class: "nav-group" },
    h("p", { class: "nav-group-label", id: `ng-${g.group}` }, g.group),
    h("ul", { role: "list", "aria-labelledby": `ng-${g.group}` }, g.items.map((it) => h("li", {}, link(it, "nav-item")))),
  )));

  const userItem = { id: "account", href: "#/account", label: user.username, icon: "user" };
  const logoutBtn = (cls) => {
    const b = h("button", { class: cls, type: "button", title: t("Esci") }, icon("logout"), h("span", { class: "nav-label" }, t("Esci")));
    b.addEventListener("click", onLogout);
    return b;
  };
  document.getElementById("side-foot").replaceChildren(
    h("ul", { role: "list" },
      FOOTER_NAV.map((it) => h("li", {}, link(it, "nav-item"))),
      h("li", {}, link(userItem, "nav-item")),
      h("li", {}, logoutBtn("nav-item")),
    ),
  );

  const bottom = document.getElementById("bottom-nav");
  const items = allItems();
  const moreBtn = h("button", { class: "bottom-item", type: "button", id: "btn-more", "aria-haspopup": "dialog", "aria-expanded": "false" },
    icon("more"), h("span", { class: "nav-label" }, t("Altro")));
  bottom.replaceChildren(...BOTTOM.map((id) => link(items.find((i) => i.id === id), "bottom-item")), moreBtn);

  document.getElementById("sheet-nav").replaceChildren(
    ...NAV.map((g) => h("div", { class: "nav-group" },
      h("p", { class: "nav-group-label" }, g.group),
      h("ul", { role: "list" }, g.items.map((it) => h("li", {}, link(it, "nav-item")))))),
    h("div", { class: "nav-group" },
      h("p", { class: "nav-group-label" }, t("Altro")),
      h("ul", { role: "list" }, FOOTER_NAV.map((it) => h("li", {}, link(it, "nav-item"))),
        h("li", {}, link(userItem, "nav-item")), h("li", {}, logoutBtn("nav-item")))),
  );
  setupSheet(moreBtn);
}

/** Marks the current section everywhere (aria-current) and the "Altro" button if the section is inside it. */
export function markCurrent(sectionId) {
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === sectionId) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const more = document.getElementById("btn-more");
  if (more) more.classList.toggle("current", sectionId != null && !BOTTOM.includes(sectionId));
}

export function setBadge(sectionId, count) {
  document.querySelectorAll(`[data-badge="${sectionId}"]`).forEach((el) => {
    el.hidden = !count;
    el.textContent = count > 99 ? "99+" : String(count || "");
    el.setAttribute("aria-label", count ? t("{0} nuove", count) : "");
  });
}

// ---------- "Altro" sheet (mobile) ----------
function setupSheet(openBtn) {
  const backdrop = document.getElementById("more-sheet");
  const sheet = backdrop.querySelector(".sheet");
  const close = () => {
    if (backdrop.hidden) return;
    backdrop.hidden = true;
    openBtn.setAttribute("aria-expanded", "false");
    document.body.classList.remove("no-scroll");
    openBtn.focus();
  };
  const open = () => {
    backdrop.hidden = false;
    openBtn.setAttribute("aria-expanded", "true");
    document.body.classList.add("no-scroll");
    sheet.querySelector("a, button")?.focus();
  };
  openBtn.addEventListener("click", open);
  if (backdrop.dataset.ready) return;
  backdrop.dataset.ready = "1";
  document.getElementById("btn-more-close").addEventListener("click", close);
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop || e.target.closest("a[data-nav]")) close();
  });
  backdrop.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
    if (e.key === "Tab") { // keep focus inside the dialog
      const focusable = [...sheet.querySelectorAll("a, button")];
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  window.addEventListener("hashchange", close);
}

// ---------- Sidebar collapse (desktop) ----------
export function setupCollapse() {
  const btn = document.getElementById("btn-collapse");
  const apply = (collapsed) => {
    document.documentElement.classList.toggle("nav-collapsed", collapsed);
    btn.setAttribute("aria-expanded", String(!collapsed));
    btn.setAttribute("aria-label", collapsed ? t("Espandi il menu") : t("Riduci il menu"));
    btn.title = btn.getAttribute("aria-label");
  };
  let collapsed = false;
  try { collapsed = localStorage.getItem("nav-collapsed") === "1"; } catch {}
  apply(collapsed);
  btn.addEventListener("click", () => {
    collapsed = !collapsed;
    apply(collapsed);
    try { localStorage.setItem("nav-collapsed", collapsed ? "1" : "0"); } catch {}
  });
}

// ---------- Dropdown menu (update jobs) ----------
export function setupMenu(button, menu) {
  const items = () => [...menu.querySelectorAll('[role="menuitem"]:not([disabled])')];
  const close = (focusButton = false) => {
    if (menu.hidden) return;
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (focusButton) button.focus();
  };
  const open = () => {
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    items()[0]?.focus();
  };
  button.addEventListener("click", () => (menu.hidden ? open() : close()));
  button.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); open(); }
  });
  menu.addEventListener("keydown", (e) => {
    const list = items();
    const i = list.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); close(true); }
    else if (e.key === "ArrowDown") { e.preventDefault(); list[(i + 1) % list.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); list[(i - 1 + list.length) % list.length]?.focus(); }
    else if (e.key === "Tab") close();
  });
  menu.addEventListener("click", (e) => { if (e.target.closest('[role="menuitem"]')) close(); });
  document.addEventListener("click", (e) => { if (!e.target.closest(".menu-wrap")) close(); });
  return { close };
}
