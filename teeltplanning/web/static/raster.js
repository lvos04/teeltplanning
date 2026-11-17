// Celbewerking en toetsenbordnavigatie voor het rondescherm (geen externe bibliotheek).
// Pijlen bewegen, Enter/F2 of typen begint te bewerken, Enter/Tab bevestigt, Esc annuleert,
// Ctrl+Backspace zet de cel terug naar het voorstel.
(function () {
  "use strict";
  function raster() { return document.getElementById("raster"); }
  function cellen(rij) { return Array.prototype.slice.call(rij.cells); }
  function verplaats(td, dr, dk) {
    var rij = td.parentElement, rijen = Array.prototype.slice.call(rij.parentElement.rows);
    var r = rijen.indexOf(rij), k = cellen(rij).indexOf(td);
    for (var stap = 0; stap < 200; stap++) {
      if (dk) { k += dk; if (k < 0 || k >= rij.cells.length) { r += dk; k = dk > 0 ? 0 : (rijen[r] ? rijen[r].cells.length - 1 : 0); } }
      else { r += dr; }
      if (r < 0 || r >= rijen.length) return;
      var doel = rijen[r].cells[k];
      if (doel && doel.classList.contains("bewerkbaar")) { doel.focus(); return; }
      if (dr && !dk) continue;
    }
  }
  function melding(tekst, fout) {
    var m = document.getElementById("raster-melding");
    if (m) m.innerHTML = tekst ? '<div class="melding' + (fout ? " fout" : "") + '">' + tekst + "</div>" : "";
  }
  function opslaan(td, waarde, daarna) {
    var data = new URLSearchParams();
    data.set("veld", td.dataset.veld); data.set("waarde", waarde);
    var rijId = td.parentElement.id, kolom = cellen(td.parentElement).indexOf(td);
    td.setAttribute("aria-busy", "true");
    fetch(td.dataset.url, { method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "text/html" } })
      .then(function (antwoord) { return antwoord.text().then(function (tekst) { return { ok: antwoord.ok, tekst: tekst }; }); })
      .then(function (a) {
        td.removeAttribute("aria-busy");
        if (!a.ok) { herstel(td); melding(a.tekst.replace(/<[^>]+>/g, ""), true); td.focus(); return; }
        var houder = document.createElement("div"); houder.innerHTML = a.tekst;
        var tpl = houder.querySelector("template");
        var aantal = 0;
        Array.prototype.forEach.call(tpl.content.querySelectorAll("tr"), function (nieuw) {
          var oud = document.getElementById(nieuw.id); if (oud) { oud.replaceWith(nieuw); aantal++; }
        });
        var elders = parseInt(tpl.dataset.elders || "0", 10);
        melding(aantal + " rij(en) bijgewerkt" + (elders ? "; " + elders + " teelt(en) in andere rondes doorgeschoven" : "") + ".", false);
        var rij = document.getElementById(rijId), nieuweTd = rij ? rij.cells[kolom] : null;
        if (nieuweTd) { nieuweTd.focus(); if (daarna) daarna(nieuweTd); }
      })
      .catch(function () { td.removeAttribute("aria-busy"); herstel(td); melding("Opslaan mislukt (geen verbinding?).", true); });
  }
  function herstel(td) { if (td.dataset.oud !== undefined) { td.textContent = td.dataset.oud; delete td.dataset.oud; td.classList.remove("bewerken"); } }
  function bewerk(td, begin) {
    if (td.classList.contains("bewerken")) return;
    td.dataset.oud = td.textContent.trim();
    var invoer = document.createElement("input");
    invoer.value = begin !== undefined ? begin : td.dataset.oud;
    td.textContent = ""; td.classList.add("bewerken"); td.appendChild(invoer); invoer.focus();
    if (begin === undefined) invoer.select();
    invoer.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); herstel(td); td.focus(); }
      else if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault(); var waarde = invoer.value, oud = td.dataset.oud;
        var richting = e.key === "Tab" ? [0, e.shiftKey ? -1 : 1] : [e.shiftKey ? -1 : 1, 0];
        if (waarde === oud) { herstel(td); td.focus(); verplaats(td, richting[0], richting[1]); return; }
        opslaan(td, waarde, function (nieuw) { verplaats(nieuw, richting[0], richting[1]); });
      }
    });
    invoer.addEventListener("blur", function () { if (td.classList.contains("bewerken") && !td.getAttribute("aria-busy")) { if (invoer.value !== td.dataset.oud) opslaan(td, invoer.value); else herstel(td); } });
  }
  document.addEventListener("keydown", function (e) {
    var td = e.target;
    if (!td.classList || !td.classList.contains("bewerkbaar") || td.classList.contains("bewerken")) return;
    var pijlen = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    if (pijlen[e.key]) { e.preventDefault(); verplaats(td, pijlen[e.key][0], pijlen[e.key][1]); }
    else if (e.key === "Tab") { e.preventDefault(); verplaats(td, 0, e.shiftKey ? -1 : 1); }
    else if (e.key === "Enter" || e.key === "F2") { e.preventDefault(); bewerk(td); }
    else if (e.key === "Backspace" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); opslaan(td, ""); }
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); bewerk(td, e.key); }
  });
  document.addEventListener("dblclick", function (e) { var td = e.target.closest && e.target.closest("td.bewerkbaar"); if (td) bewerk(td); });
  void raster;
})();
