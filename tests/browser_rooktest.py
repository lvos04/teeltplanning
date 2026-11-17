"""Browser-rooktest: echte server + echte browser (Chromium via Playwright).

Start de app op een vrije poort met een lege datamap, maakt de beheerder aan, importeert het synthetische
werkboek, en test in het raster de toetsenbordbediening: pijltjes, typen, Enter, Esc en Ctrl+Backspace.
Maakt schermafdrukken in de opgegeven map.

    python tests/browser_rooktest.py [uitvoermap] [--chrome /pad/naar/chrome]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def vrije_poort() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    from playwright.sync_api import expect, sync_playwright

    from tests.synthetisch import PROGRAMMA_NAAM, maak_alles

    parser = argparse.ArgumentParser()
    parser.add_argument("uitvoer", nargs="?", default=tempfile.mkdtemp(prefix="browser-"))
    parser.add_argument("--chrome", default=None)
    args = parser.parse_args()
    uitvoer = Path(args.uitvoer)
    uitvoer.mkdir(parents=True, exist_ok=True)
    werkboeken = Path(tempfile.mkdtemp(prefix="werkboeken-"))
    meta = maak_alles(werkboeken)
    data = tempfile.mkdtemp(prefix="data-")
    poort = vrije_poort()
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "teeltplanning.main:app", "--port", str(poort), "--workers", "1", "--no-proxy-headers"],
        cwd=ROOT, env={**os.environ, "DATA_DIR": data}, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )  # fmt: skip
    basis = f"http://127.0.0.1:{poort}"
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(basis + "/health", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.chrome) if args.chrome else p.chromium.launch()
            pagina = browser.new_page(viewport={"width": 1600, "height": 1000})
            fouten: list[str] = []
            pagina.on("pageerror", lambda e: fouten.append(str(e)))
            pagina.goto(basis + "/")
            pagina.fill("input[name=gebruikersnaam]", "leon")
            pagina.fill("input[name=wachtwoord]", "geheim-wachtwoord-123")
            pagina.click("button[type=submit]")
            pagina.goto(basis + "/import")
            pagina.set_input_files("input[name=bestand]", str(werkboeken / PROGRAMMA_NAAM))
            pagina.click("text=Uploaden en controleren")
            for nummer, jaar in meta["programma"]["jaren"].items():
                pagina.fill(f"input[name=jaar_{nummer}]", str(jaar))
            pagina.click("text=Jaren opslaan en voorbeeld vernieuwen")
            pagina.click("text=Bevestigen en importeren")
            pagina.screenshot(path=str(uitvoer / "import.png"), full_page=False)
            pagina.goto(basis + "/ronde?nummer=2")
            pagina.screenshot(path=str(uitvoer / "ronde.png"))
            # Toetsenbord: eerste LD-cel, typ 21 + Enter → opgeslagen en gemarkeerd als handmatig.
            cel = pagina.locator("tbody tr").first.locator('td[data-veld="ld_dagen"]')
            cel.focus()
            pagina.keyboard.type("21")
            pagina.keyboard.press("Enter")
            eerste = pagina.locator("tbody tr").first.locator('td[data-veld="ld_dagen"]')
            expect(eerste).to_have_text("21")
            expect(eerste).to_have_class(__import__("re").compile("handmatig"))
            expect(pagina.locator("#raster-melding")).to_contain_text("bijgewerkt")
            # Na Enter staat de focus een rij lager in dezelfde kolom.
            assert pagina.evaluate("document.activeElement.dataset.veld") == "ld_dagen"
            # Pijl rechts, Esc annuleert een begonnen bewerking.
            pagina.keyboard.press("ArrowRight")
            veld = pagina.evaluate("document.activeElement.dataset.veld")
            oud = pagina.evaluate("document.activeElement.textContent")
            pagina.keyboard.type("9")
            pagina.keyboard.press("Escape")
            assert pagina.evaluate("document.activeElement.textContent") == oud and veld
            # Ctrl+Backspace: terug naar voorstel.
            eerste.focus()
            pagina.keyboard.press("Control+Backspace")
            expect(pagina.locator("tbody tr").first.locator('td[data-veld="ld_dagen"]')).not_to_have_class(
                __import__("re").compile("handmatig")
            )
            pagina.screenshot(path=str(uitvoer / "ronde-na-bewerken.png"))
            pagina.set_viewport_size({"width": 400, "height": 800})
            pagina.screenshot(path=str(uitvoer / "ronde-telefoon.png"))
            pagina.set_viewport_size({"width": 1600, "height": 1000})
            pagina.goto(basis + "/kap/1")
            pagina.screenshot(path=str(uitvoer / "kap.png"))
            pagina.goto(basis + "/beheer/instellingen?groep=Datumketen")
            pagina.screenshot(path=str(uitvoer / "instellingen.png"))
            browser.close()
            assert not fouten, fouten
        print(json.dumps({"ok": True, "schermafdrukken": str(uitvoer)}))
        return 0
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
