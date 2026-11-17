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

## Beveiliging (zoals Gewichtsverwachting)

**Echt IP-adres en aanmeldbeperking.** Mislukte aanmeldingen tellen per account (*Maximale inlogpogingen per
account*, start 5) én per IP-adres (start 25) binnen een venster (start 15 minuten), met een kleine oplopende
vertraging (maximaal 1 s). IP-limiet 0 = zelfde als per account.

* **Direct (LAN, `http://SERVER:30096`)**: niets instellen.
* **Achter een reverse proxy of tunnel** (cloudflared, Traefik, Nginx Proxy Manager, Tailscale Serve): vul in
  **Beheer → Instellingen → Beveiliging → Vertrouwde proxy's** het IP-adres of netwerk van die proxy in (bijv.
  `172.16.0.0/12`, of `127.0.0.1`). Alleen van die adressen leest de app `X-Forwarded-For` (van rechts naar
  links). De container draait bewust met `--no-proxy-headers`. Vul nooit `0.0.0.0/0` in (wordt geweigerd).

**Strikte oorsprongcontrole (standaard aan).** Schrijfverzoeken moeten een `Origin` van dezelfde host hebben, of
zonder `Origin` een `Referer` van dezelfde host. Browsers doen dat altijd.

**Tweestapsverificatie (optioneel).** Elke gebruiker kan in **Beheer → Tweestapsverificatie** een
authenticator-app koppelen. *MFA verplicht voor beheerders* staat standaard uit. Telefoon kwijt:
`python -m teeltplanning.beheer_cli mfa-uit <gebruikersnaam>` in de container.

**HTTPS.** Zet `SECURE_COOKIES=true` zodra de app via HTTPS bereikbaar is (dan werkt aanmelden via gewoon HTTP
niet meer). Richt de eerste beheerder in vóórdat het adres breed bereikbaar wordt.

**Verzoekgrootte.** Maximaal 1 MB per verzoek, behalve uploads van werkboeken (instelling *Import → Maximale
uploadgrootte*, start 32 MiB) en het terugzetten van een back-up.

## Back-ups

Dagelijks een lokale back-up in `/data/backups/` en vóór elke schemamigratie. Plan daarnaast ZFS-snapshots en een
kopie op een andere locatie (Cloud Sync/Replication). Zie HERSTEL.md.

## Externe verbindingen

Alle externe bronnen (KNMI, Open-Meteo, EnergyZero) en de bedrijfskoppeling staan na een schone installatie
**uit**. De app werkt volledig zonder internet en zonder GPU.

## Bronsleutels

De KNMI-API-sleutel wordt versleuteld opgeslagen met `/data/geheim.key` (aangemaakt bij het eerste gebruik,
rechten 0600). Dat bestand gaat mee in de back-up; zonder dat bestand moeten opgeslagen sleutels opnieuw worden
ingevuld (Beheer → Bronnen).
