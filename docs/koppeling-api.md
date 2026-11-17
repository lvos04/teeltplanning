# Bedrijfskoppeling API v1

> Kopie van het gedeelde contract uit `lvos04/bedrijfsdashboard/docs/koppeling-api.md` (BOUWPLAN §11). Het
> app-specifieke deel voor Teeltplanning (slug `teeltplanning`) staat onderaan. De rij voor `teeltplanning` in de
> tabel met schakelaarplekken staat (nog) niet in het dashboard; die komt pas met de PR uit §11.3 na akkoord van Leon.


Doel: elke bedrijfsapp (teeltregistratie/milieu, gewichtsverwachting, scouten, tuinwerk-os)
deelt een kleine, **alleen-lezen** set gegevens in een vast formaat, zodat het nieuwe *Bedrijfsdashboard*
(en andere apps) die server-naar-server kan ophalen. Alle teksten Nederlands.

## Basis

* Pad-prefix: `/api/koppeling/v1` (als de app al een andere API-prefix heeft, bijv. `/api/v1`, toch exact
  `/api/koppeling/v1` aanbieden zodat het dashboard één vast pad kent).
* Alleen `GET`. JSON, UTF-8. Tijden ISO 8601 met tijdzone (UTC `Z` of offset). Datums `YYYY-MM-DD`.
* Getallen: JSON-getal; als de app intern Decimal gebruikt mag het als string-getal (`"12.50"`) — het dashboard
  accepteert beide. Ontbrekende waarde = `null`, nooit een verzonnen 0.

## Authenticatie: koppelsleutels

* Header `Authorization: Bearer <sleutel>`. Geen sleutel / onbekend / ingetrokken → `401`
  `{"fout": "ongeldige_koppelsleutel"}`. Geen sessiecookie-auth op deze routes en geen CSRF nodig (alleen GET).
* Sleutel: `bk_` + minimaal 32 bytes cryptografisch willekeurig (urlsafe base64/hex). Alleen **SHA-256-hash**
  opslaan + eerste 8 tekens als herkenbaar `voorvoegsel`. Vergelijken in constante tijd (of via hash-lookup).
* Velden per sleutel: id, naam (bijv. "Bedrijfsdashboard"), voorvoegsel, hash, aangemaakt_op, aangemaakt_door,
  laatst_gebruikt_op, ingetrokken_op.
* Beheer alleen door beheerders, via de bestaande beheer-API van de app (sessie-auth + bestaande CSRF/oorsprong-
  controles), bijvoorbeeld:
  * `GET    <bestaande-admin-prefix>/koppelsleutels` — lijst (nooit de hash/sleutel tonen)
  * `POST   <bestaande-admin-prefix>/koppelsleutels` `{naam}` → geeft **eenmalig** de volle sleutel terug
  * `DELETE <bestaande-admin-prefix>/koppelsleutels/{id}` — intrekken
  Als de app een auditlog heeft: aanmaken/intrekken auditen. Een eenvoudige UI-sectie in Beheer is gewenst als de
  frontend dat zonder grote ingreep toelaat; anders minstens API + CLI-commando + documentatie.
* Rate limiting: als de app al een limiter heeft, toepassen (bijv. 120 verzoeken/min per sleutel).

## Koppeling in- of uitschakelen (`403 koppeling_uitgeschakeld`)

* Elke app heeft een instelling **"Bedrijfskoppeling ingeschakeld"**, **standaard uit**. Het sleutelbeheer blijft
  zichtbaar, met de schakelaar erboven.
* Staat de koppeling uit, dan geven **alle** `/api/koppeling/v1/*`-routes
  `403 {"fout": "koppeling_uitgeschakeld"}` — ook met een geldige sleutel, zonder sleutel en bij ongeldige
  parameters (de controle gebeurt vóór authenticatie en validatie).
* Volgorde van controles: koppeling uit → `403`; geen/onbekende/ingetrokken sleutel → `401`; te veel verzoeken →
  `429`; onbekende dataset → `404`; ongeldige parameters → `422`.
* Een afnemer (zoals het Bedrijfsdashboard) behandelt deze 403 als "staat uit bij de bron" en niet als storing,
  en toont de gebruiker waar de schakelaar staat:

  | app (slug) | schakelaar staat onder |
  |---|---|
  | `milieu-registratie` | Instellingen → Koppelingen |
  | `gewichtsverwachting` | Beheer → Bedrijfskoppeling |
  | `scouten` | Beheer & instellingen → Koppelingen |
  | `tuinwerk-os` | Beheer → Integraties → Bedrijfskoppeling |
  | `priva-bridge` | Beheer → Bedrijfskoppeling |
  | `bedrijfsdashboard` | Beheer → Koppelsleutels van dit dashboard |
  | `teeltplanning` | Beheer → Koppelingen → Bedrijfskoppeling *(deze app; nog toe te voegen in het dashboard, §11.3)* |

  Een andere `403` (zonder deze foutcode) blijft een gewone fout ("Bron gaf HTTP 403").

## Endpoints

### `GET /api/koppeling/v1/info`
```json
{
  "app": "milieu-registratie",          // vaste slug: milieu-registratie | gewichtsverwachting | scouten | tuinwerk-os | priva-bridge | bedrijfsdashboard
  "naam": "Teeltregistratie OS",
  "versie": "0.4.2",                    // appversie
  "api_versie": "1",
  "bedrijf": "Naam uit instellingen of null",
  "datasets": ["samenvatting", "..."],  // namen die /datasets/{naam} ondersteunt
  "tijd": "2026-11-16T15:00:00Z"
}
```

### `GET /api/koppeling/v1/samenvatting`
Kern-KPI's en meldingen voor één dashboardtegel.
```json
{
  "app": "tuinwerk-os",
  "gegenereerd_op": "2026-11-16T15:00:00Z",
  "kpis": [
    {"sleutel": "open_storingen", "label": "Open storingen", "waarde": 4, "eenheid": null,
     "status": "let_op", "toelichting": "2 met hoge prioriteit"}
  ],
  "meldingen": [
    {"niveau": "waarschuwing", "tekst": "3 onderdelen onder bestelminimum", "sinds": "2026-11-15T08:00:00Z"}
  ]
}
```
* `status`: `ok` | `let_op` | `actie` | `onbekend`. `niveau`: `info` | `waarschuwing` | `kritiek`.
* 4–8 KPI's die voor de bedrijfsleiding het meest zeggen. Alleen echte, berekende waarden.

### `GET /api/koppeling/v1/datasets/{naam}?sinds=<ISO>&limiet=<1..500, standaard 100>&cursor=<opaak>`
```json
{"app": "scouten", "dataset": "waarnemingen", "gegenereerd_op": "...",
 "items": [ { ... } ], "volgende_cursor": null}
```
* Onbekende dataset → `404 {"fout": "onbekende_dataset"}`. Ongeldige parameters → `422`.
* Elke dataset: stabiele `id`, `bijgewerkt_op` waar mogelijk (voor `sinds`). Sortering stabiel.
* **Privacy**: geen wachtwoorden, tokens, e-mailadressen, telefoonnummers of vrije-tekst-notities met mogelijk
  persoonsgegevens. Medewerkers hooguit als weergavenaam als dat functioneel nodig is.

## Kwaliteit
* Tests voor: 401 zonder/ongeldige/ingetrokken sleutel, sleutel aanmaken (eenmalig tonen, hash opgeslagen),
  info/samenvatting vorm, minstens één dataset incl. `limiet`/`sinds`, 404 onbekende dataset, en dat
  niet-beheerders geen sleutels kunnen beheren.
* Documentatie: `docs/koppeling-api.md` in elke repo (dit contract + app-specifieke KPI's en datasets).
* Databasewijziging via het bestaande migratiesysteem van de app.

---

## Specifiek voor het Bedrijfsdashboard (slug `bedrijfsdashboard`)

Het dashboard **gebruikt** dit contract om de andere apps op te halen, en **biedt** het zelf ook aan, zodat
andere apps het geaggregeerde bedrijfsoverzicht kunnen tonen.

### Koppelsleutels van het dashboard beheren

* UI: *Beheer → Koppelsleutels van dit dashboard* (aanmaken, sleutel wordt één keer getoond, intrekken), met
  daarboven de schakelaar **Bedrijfskoppeling ingeschakeld** (standaard uit; dan `403 koppeling_uitgeschakeld`).
  Aanmaken/intrekken komt in het auditlog (zonder de sleutel zelf).
* JSON (sessie-auth als beheerder + header `X-CSRF-Token`):
  * `GET    /api/beheer/koppelsleutels`
  * `POST   /api/beheer/koppelsleutels` `{"naam": "..."}` → `201` met eenmalig `sleutel`
  * `DELETE /api/beheer/koppelsleutels/{id}` → `204`
* Rate limit: 120 verzoeken per minuut per sleutel (`429 {"fout": "te_veel_verzoeken"}`).
* Staat *MFA verplicht voor beheerders* aan en heeft de beheerder geen MFA, dan geven deze routes
  `403 {"fout": "mfa_vereist"}`.

### Hoe het dashboard een uitgeschakelde bron toont

Krijgt het dashboard bij het ophalen `403 {"fout": "koppeling_uitgeschakeld"}`, dan toont het bij die bron (overzicht,
detailpagina, verbindingstest, tv-weergave) de uitleg *"Koppeling staat uit in &lt;naam&gt;: zet hem aan onder
&lt;plek uit de tabel hierboven&gt; (schakelaar "Bedrijfskoppeling ingeschakeld")"* in plaats van "niet bereikbaar".
De datasets-proxy `/api/bronnen/{id}/datasets/{naam}` geeft dan `502` met `{"fout": "<uitleg>",
"code": "koppeling_uitgeschakeld"}`. De laatst bekende gegevens blijven zichtbaar als *verouderd*.

### KPI's in `/samenvatting`

| sleutel | betekenis | status |
|---|---|---|
| `bronnen_bereikbaar` | aantal gekoppelde apps waarvan het laatste ophalen lukte (toelichting: "van N") | `ok` alle, `let_op` deel, `actie` geen, `onbekend` nog geen bronnen |
| `kritieke_meldingen` | aantal meldingen met niveau `kritiek` over alle apps | `actie` bij > 0 |
| `waarschuwingen` | aantal meldingen met niveau `waarschuwing` | `let_op` bij > 0 |
| `kpis_actie` | aantal KPI's van alle apps met status `actie` | `actie` bij > 0 |

Zolang er nog geen gegevens zijn opgehaald zijn de waarden `null` (status `onbekend`), nooit een verzonnen 0.

`meldingen` bevat alle meldingen van de gekoppelde apps, voorafgegaan door de bronnaam (bijv.
`"Scouten: Trips boven drempel"`), met `(verouderd)` als de bron op dit moment niet bereikbaar is, plus een
`waarschuwing` per onbereikbare bron.

### Datasets

| naam | items | `bijgewerkt_op` |
|---|---|---|
| `bronnen` | `id`, `naam`, `app`, `bereikbaar`, `verouderd`, `laatste_succes`, `fout` | laatste ophaalpoging |
| `kpis` | `id` (`<bron_id>:<sleutel>`), `bron_id`, `app`, `sleutel`, `label`, `waarde`, `eenheid`, `status`, `verouderd` | laatste succesvolle ophaalactie |
| `meldingen` | `id` (stabiele hash), `bron_id`, `app`, `niveau`, `tekst`, `sinds`, `verouderd` | `sinds` |

`limiet` 1..500 (standaard 100), `cursor` opaak, `sinds` ISO 8601 met tijdzone; ongeldige waarden → `422`.

---

## Specifiek voor Teeltplanning (slug `teeltplanning`)

### Aanbieden

* **Schakelaar**: *Beheer → Koppelingen → Bedrijfskoppeling* ("Bedrijfskoppeling ingeschakeld", instelling
  `koppeling.ingeschakeld`, **standaard uit**). Uit → elke route onder `/api/koppeling/v1` geeft
  `403 {"fout": "koppeling_uitgeschakeld"}`, ook zonder sleutel en bij ongeldige parameters.
* **Sleutels**: *Beheer → Koppelingen* (aanmaken: de sleutel staat één keer op de pagina; intrekken), JSON voor
  beheerders (sessie + oorsprongcontrole):
  * `GET /api/beheer/koppelsleutels` — lijst (nooit sleutel of hash)
  * `POST /api/beheer/koppelsleutels` `{"naam": "..."}` → `201` met eenmalig `sleutel`
  * `DELETE /api/beheer/koppelsleutels/{id}` → `204`
  * CLI: `python -m teeltplanning.beheer_cli koppelsleutel <naam>` / `koppelsleutel-intrekken <id>`
  Aanmaken en intrekken komen in het auditlog (zonder de sleutel). Alleen de SHA-256-hash en de eerste 8 tekens
  worden bewaard. Niet-beheerders: `403`; niet aangemeld: `401`.
* **Limiet**: 120 verzoeken per minuut per sleutel (instelling `koppeling.verzoeken_per_minuut`) →
  `429 {"fout": "te_veel_verzoeken"}` met `Retry-After: 60`.
* **Volgorde**: uit → 403; sleutel → 401; limiet → 429; onbekende (of niet gedeelde) dataset →
  `404 {"fout": "onbekende_dataset"}`; ongeldige `sinds` (ISO 8601 **met** tijdzone), `limiet` (1..500), `cursor`
  of een onbekende parameter → `422 {"fout": "ongeldige_parameter", "veld": "..."}`.
* Alleen het **vastgestelde** scenario wordt gedeeld, nooit een concept. Zonder vastgesteld plan zijn de datasets
  leeg en alle KPI's `null`/`onbekend`. Geen vrije tekst (opmerkingen) en geen gebruikersnamen.
* Welke datasets gedeeld worden: instelling `koppeling.datasets` (start: alle vier). `info.bedrijf` = instelling
  *Bedrijfsnaam* (of `null`).

### KPI's in `/samenvatting`

| sleutel | betekenis | status |
|---|---|---|
| `plantingen_komende_14_dagen` | teelten met plantdatum vandaag t/m +14 dagen | `ok` |
| `oogsten_komende_14_dagen` | teelten met geplande oogst vandaag t/m +14 dagen | `ok` |
| `oogstprognose_deze_week` | verwacht aantal takken met de oogst in de huidige ISO-week | `ok`; `onbekend` + `null` als een oogstaantal onbekend is (bijv. oppervlak per kap) |
| `planconflicten` | lopende en geplande teelten met een fout (overlap, buiten de grenzen) of een ontbrekende plantdatum, LD-dagen, reactietijd of verlenging | `actie` bij > 0 |
| `voorstellen_indicatief` | % van de geplande teelten waarvan een effectieve waarde een voorstel is dat niet op een gevalideerd model (of een gevalideerde LBE) rust | `let_op` boven `koppeling.indicatief_grens_pct` (leeg: boven 0) |
| `kwh_komende_week` | kWh belichting van het plan, vandaag t/m +7 dagen | `onbekend` + `null` zonder lichtdata |

Meldingen: planconflicten (`waarschuwing`), verlopen weerdata van een ingeschakelde weerbron (`waarschuwing`),
teelten zonder belichtingsdata (`info`), geen of verouderde lokale back-up (`waarschuwing`/`kritiek`).

### Datasets

| naam | velden per item | `id` / sortering | `bijgewerkt_op` (voor `sinds`) |
|---|---|---|---|
| `planning` | `id, ronde, kap, vakgroep, volgnummer, soort, bedden, plantdatum, ld_dagen, licht_uit_datum, reactietijd, verlenging, oogstdatum, teeltduur, dichtheid, gepland_aantal, uitvalfractie, oogstaantal, gewicht_g, gewicht_ondergrens_g, gewicht_bovengrens_g, handmatig_velden[], bijgewerkt_op` | teelt-id | laatste wijziging in dezelfde **kap** (een wijziging schuift de hele keten) of aan het hele plan (instellingen, kappen, model, imports, bronnen, vaststellen) |
| `oogstprognose` | `id (iso_jaar-week), iso_jaar, week, aantal_takken, gemiddeld_gewicht_g, ondergrens_g, bovengrens_g, aantal_teelten, bijgewerkt_op` | jaar, week | laatste wijziging aan het plan |
| `energieprognose` | `id (iso_jaar-week-kap), iso_jaar, week, kap, dimgewogen_uren, kwh, kosten_eur, soort (gerealiseerd/gepland/gemengd), bijgewerkt_op` | jaar, week, kap | idem |
| `afzetmomenten` | `id (moment-jaar), naam, datum, venster_begin, venster_eind, gewicht, geplande_takken_in_venster` | datum, moment | idem (geen eigen veld; zie hieronder) |

* `aantal_takken`, gewichten en kWh zijn `null` zodra één onderliggende waarde onbekend is (nooit een verzonnen 0).
* `energieprognose.dimgewogen_uren` is altijd `null`: een kap heeft meerdere lampgroepen met eigen uren (B41).
* `energieprognose` telt alleen dagen waarop er een teelt in de kap staat.
* `afzetmomenten`: `venster_begin/eind` = oogst in [datum − venster, datum − 1]; `null` zolang het venster
  onbekend is (vraag 9). `geplande_takken_in_venster` is `null` als oogstaantallen onbekend zijn.

Voorbeeld:

```bash
curl -s -H "Authorization: Bearer $SLEUTEL" "https://teelt.example/api/koppeling/v1/datasets/planning?limiet=500&sinds=2026-11-01T00:00:00Z"
```

### Afnemen (Gewichtsverwachting)

* *Beheer → Koppelingen → Koppelbronnen*: naam, app, adres en koppelsleutel (versleuteld met
  `DATA_DIR/geheim.key`, zoals het Bedrijfsdashboard). Ophalen alleen als **Koppelbronnen ophalen** aan staat
  (`koppeling.bronnen_aan`, standaard uit): elk `koppeling.ophaalinterval_uren` (start 6) en met *Nu ophalen*.
* Haalt `/info` (controle: `app` = `gewichtsverwachting`) en de datasets `teelten`, `oogsten`, `metingen`,
  `prognoses` volledig op (limiet 500, `volgende_cursor`), en vervangt de vorige kopie per dataset.
* `403 {"fout": "koppeling_uitgeschakeld"}` → status *staat uit bij de bron* met de uitleg
  "Staat uit bij de bron (Gewichtsverwachting: Beheer → Bedrijfskoppeling)", geen storing. `401` → "koppelsleutel
  geweigerd".
* Koppelen op kap + plantdatum ± `import.koppel_tolerantie_dagen` (leeg = dezelfde dag) aan het vastgestelde plan.
  Geen of meer dan één kandidaat, of twee bronteelten op één planteelt: **niet gekoppeld**, met de reden in een lijst.
* Gekoppelde teelten → waarnemingen (gewicht en uitval uit `oogsten`, LD-dagen uit plant- en licht-uitdatum,
  reactietijd alleen als `koppeling.oogstdatum_realisatie` is ingesteld, B40) voor de LBE-kalibratie en de
  voorstelladder. `prognoses` (alleen status `gevalideerd`) staan op de teeltpagina naast de planprognose.
