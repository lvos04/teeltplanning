# Installeren op TrueNAS SCALE (Custom App)

Installatie via de wizard **Apps → Discover → Custom App**, zonder YAML.

1. Maak een dataset, bijvoorbeeld `/mnt/tank/apps/teeltplanning`. Geef UID/GID **568:568** lees- en
   schrijfrechten via de ACL-editor. Geen algemene wereldschrijfrechten.
2. **Custom App**, naam `teeltplanning`.
3. Image repository `ghcr.io/lvos04/teeltplanning`, tag: een **vaste** versie (bijv. `v0.2.0`), nooit `latest`
   of een `-rc`-testbuild voor productie. Geen privileged-modus.
4. Poort: containerpoort **8000/TCP** → hostpoort **30096** (voorstel; open vraag 12: controleer dat die vrij is).
5. Opslag: de dataset als **Host Path**, gemount op **`/data`**, met schrijfrecht.
6. Omgevingsvariabelen:

| Variabele | Waarde | Betekenis |
|---|---|---|
| `DATA_DIR` | `/data` | alle persistente bestanden |
| `TZ` | `Europe/Amsterdam` | tijdzone |
| `SECURE_COOKIES` | `true` zodra de app via HTTPS bereikbaar is | sessiecookie alleen via HTTPS |
| `TRUSTED_PROXIES` | optioneel, bijv. `172.16.0.0/12` | beginwaarde voor vertrouwde proxy's; de instelling in Beheer gaat voor |

7. Start de app en open `http://SERVER:30096`. Maak bij de eerste start de beheerder aan (er zijn geen
   standaardaccounts), vóórdat het adres breed bereikbaar wordt.

**GHCR-pakket.** GitHub maakt een nieuw containerpakket privé aan. Zet het pakket
`ghcr.io/lvos04/teeltplanning` eenmalig op **publiek** (GitHub → Packages → teeltplanning → Package settings →
Change visibility), of geef TrueNAS registry-inloggegevens (gebruikersnaam + een token met `read:packages`).
Als de repo na de bouw privé gaat (vraag 12), blijft een publiek pakket gewoon werken.

**Controleren:** `/health` geeft `status: ok`. Een groene healthcheck bewijst niet dat aanmelden, importeren en
back-up werken; test die apart.
