# Gebruik

## Eerste start

Open de app (bijv. `http://SERVER:30096`). Bij de eerste start maak je de beheerder aan; er zijn geen
standaardaccounts. Wachtwoord minimaal twaalf tekens. Daarna in **Beheer → Gebruikers** medewerkers en lezers
toevoegen.

| Rol | Mag |
|---|---|
| beheerder | alles, ook instellingen, gebruikers, back-up en export |
| medewerker | plan bewerken, importeren, kappen en rassen bekijken |
| lezer | alleen bekijken (en straks afdrukken) |

## Werkboek importeren

1. **Import** → kies `Teeltprogramma maken …xlsx` → *Uploaden en controleren*. Het origineel wordt met checksum
   bewaard onder `/data/bron/`; er worden geen formules uitgevoerd.
2. Het controlevoorbeeld toont per rondeblad het aantal regels, meldingen (zoals een afwijkende rondetitel,
   onbekende soortcode, ontbrekende plantdatum) en het aantal plantweken dat naar de vorige ronde verwijst.
3. Vul per ronde het **ISO-jaar van de eerste planting** in (het werkboek bevat geen jaartal) en kies
   *Jaren opslaan en voorbeeld vernieuwen*. Nu verschijnt de **vergelijking**: licht uit, bloei en teeltduur van
   de app tegenover de gecachte uitkomsten in het werkboek, met de klassen `gelijk`, `52_weken`, `afwijking`
   en `onvolledig`, en het rapport als CSV.
4. *Bevestigen en importeren*: alle rondes met een jaar komen in het scenario **Werkboek** (één transactie).
   Invoerwaarden worden handmatige waarden; een plantweek die naar de vorige ronde verwijst, volgt de keten
   (instelling *Import → Plantdatum bij verwijzing naar vorige ronde*). Nog een keer bevestigen of hetzelfde
   werkboek opnieuw importeren maakt niets dubbel aan, en waarden die je in de app hebt ingevuld blijven staan.

## Ronde

Het raster heeft dezelfde 40 regels en kolommen als het rondeblad (week en dag in aparte kolommen).

* Pijltjes en Tab bewegen; typen of Enter/F2 begint een bewerking; Enter of Tab slaat op; Esc annuleert.
* Een lege cel, of **Ctrl+Backspace**, zet de cel **terug naar het voorstel**.
* Geel gemarkeerd = handmatige waarde (de tooltip toont het voorstel). Schuingedrukte plantdatum = volgt de
  keten.
* Na elke wijziging wordt het hele plan doorgerekend; de gewijzigde rijen worden ververst en de melding toont
  hoeveel teelten in andere rondes zijn doorgeschoven.
* De meldingenkolom toont fouten (zoals overlap) en waarschuwingen (zoals een onbekende waarde).
* Op een telefoon scrolt het raster horizontaal met de eerste drie kolommen vast.

Klik op het kapnummer voor de **teelt**: per veld voorstel, handmatig en effectief, wie het wanneer met welke
reden heeft gewijzigd, *Terug naar voorstel*, de datums en aantallen, en het werkboekbewijs (cel, formule en
gecachte uitkomst).

## Kap

**Kap** toont de ketting van teelten van één kap over alle rondes met een tijdbalk (groen = lange dag,
paars = korte dag). Een onderbroken keten (bijv. een onbekende LD-dagen) is direct zichtbaar: de volgende
plantdatum is dan onbekend.

## Beheer

* **Instellingen** wordt gegenereerd uit het instellingenregister: per groep een pagina, zoekfunctie, huidige
  waarde naast de startwaarde, *Terug naar startwaarde*, en een ingangsdatum waar dat nodig is (zoals
  oppervlak per kap). Ongeldige waarden worden geweigerd met een melding. Elke wijziging komt in het auditlog
  en rekent het plan direct door. Exporteren en inlezen als één JSON-bestand.
* **Kappen** en **Rassen**: bekijken, en als beheerder toevoegen/wijzigen/uitzetten via CSV.
* **Gebruikers**, **Auditlog**, **Back-up en export**, **Tweestapsverificatie**: zie HERSTEL.md en TRUENAS.md.

## Afdrukken

Eerst één keer (beheerder): **Beheer → Sjabloon** → je eigen werkboek *Teeltprogramma maken* uploaden. De app
bewaart het onder `/data/sjabloon/` en controleert het: welke bladen, welk afdrukbereik, welke lettertypen (en of
die in de container staan) en welke formulecellen de app niet vult. Ontbrekende lettertypen kun je daar uploaden.
Met de **controleafdruk met veldnamen** zie je in welke cel elk veld van de teeltkaart terechtkomt; de cellen
stel je in bij *Beheer → Instellingen → Afdrukken*.

Daarna (iedereen, ook lezers): **Afdrukken** → scenario en ronde kiezen.

* **Rondeblad**: *Pdf* opent het rondeblad (A4 staand, één pagina) direct in de browser; *Excel* geeft het gevulde
  werkblad.
* **Teeltkaarten**: alle kaarten van de ronde in één bestand (twee kaarten per blad, A4 liggend), of kies een kap
  voor één blad (kap N links, kap N+1 rechts). Bij een gekozen kap staat de kaart ook als voorbeeld op het scherm
  (kop, 16 meetmomenten met LD-dag en streeflengte, begin oogst en RT). Direct opvragen kan met alleen kap en ronde:
  `/afdruk/teeltkaart.pdf?ronde=3&kap=7`.
* De app schrijft **waarden** in een kopie van je eigen rondeblad of teeltkaart; opmaak, kolombreedtes,
  samengevoegde cellen en afdrukinstellingen blijven zoals in je werkboek. Formulecellen die de app niet vult, worden
  standaard leeggemaakt (zodat er nooit een waarde van een andere kap op staat).
* Lukt de pdf niet (fout, te lang bezig, of meer pagina's dan verwacht), dan krijg je een melding met een knop naar
  hetzelfde bestand als **Excel**.

## Licht, energie en dichtheid (fase 3)

**Lampgroepen** (Beheer → Lampgroepen): de vijf groepen uit het bouwplan staan erin; vul de maanden in
(`geldig_vanaf`, `geldig_tot`, JJJJ-MM-DD). Zolang een geldende groep geen begindatum heeft, zijn lichtsom en kWh
"onbekend".

**Belichting importeren** (Import → Belichting winterronde): uploaden; het werkboek wordt op de achtergrond
gelezen (de pagina ververst vanzelf). Het controlevoorbeeld toont de weekuren (per celtype, buiten 0–168), de
kaptabel, de kostenvergelijking, het teeltschema en voor het tabblad data de periode, ontbrekende intervallen,
onvolledige dagen, dubbele tijdstempels, regels buiten tijdvolgorde en zomer-/wintertijddagen. Vul het ISO-jaar
van week 36 (weekuren) en van de eerste regel van het teeltschema in en bevestig. Nog een keer bevestigen of
opnieuw importeren maakt niets dubbel.

**Bronnen** (Beheer → Bronnen, alleen beheerder): alle bronnen staan uit. Zet Open-Meteo aan bij Instellingen →
Bronnen (let op: gratis = niet-commercieel), vul breedte- en lengtegraad in (Instellingen → Bedrijf) en kies
*Nu ophalen*. De app haalt de daghistorie (30 jaar) en de verwachting op en berekent de weeknormalen. KNMI: de
sleutel kan worden opgeslagen en getest; waarden inlezen wacht op vraag B23.

**Teelt**: onder *Licht, LBE en energie* staan lichtsom (zon/lamp, band, per soort zonlicht), de ontbrekende
dagen met reden, LBE (waarde, status, uitleg), dichtheidsvoorstel (afgerond en niet afgerond), gewichtsprognose
met band, kWh en kosten (per tak).

**LBE en dichtheid** (Beheer): LBE-tabel (CSV), *Kalibreren uit realisatie*, *Dichtheden afleiden uit de
historie*, en de ijkpunten uit het belichtingsblad naast de eigen LBE.

**Energie**: kWh en kosten per week voor het bedrijf en per kap per week, per jaar; *Scenariovergelijking* zet
twee lichtniveaus naast elkaar (takken, MWh, kosten per tak, meeropbrengst).

## Voorstellen met model (fase 4)

**Beheer → Model**: *Nu trainen en toetsen* (beheerder) start een training op de achtergrond; de pagina keert
meteen terug en de app blijft bruikbaar. Na afloop staan per doel (LD-dagen, verlenging, reactietijd, gewicht) de
treden met hun status (referentie, gevalideerd, indicatief, onvoldoende gegevens), het aantal toetsteelten, de
gemiddelde fout tegenover de seizoensbasis en de fout per toetsjaar. De nieuwste geslaagde versie is *actief*.
Automatisch hertrainen staat uit (Instellingen → Model → *Automatisch hertrainen*).

**Teelt**: bij elk voorstel staan in vaste zinnen de **bron** (bijv. "Seizoensbasis: mediaan van 9 eerdere
teelten van ras a met plantweek 12 ± 2 in eerdere jaren."), de **bandbreedte**, de **modelversie** met status
(gevalideerd/indicatief) en de **drie meest vergelijkbare eerdere teelten** (kap, plantdatum, waarde, bron). Bij
een rekenregel (keten, uitvaltabel) staat dat er; zonder getraind model staat er "nog geen getraind model".

Zolang er met echte gegevens geen trede de seizoensbasis verslaat (of de lichtsom onbekend is), zijn alle
voorstellen **indicatief**.

## Afzet, scenario's, genereren en zoeken (fase 5)

**Afzet**: de afzetmomenten met hun datums voor drie jaar, het venster (zodra ingevuld), terugrekenen van de
plantdatum en de officiële feestdagen van de ingestelde landen. Aanpassen in *Beheer → Afzetmomenten* (CSV):
datumregel, `venster_dagen_voor`, belang (`gewicht` 0–10), markt, actief, bevestigd.

**Scenario's**: kopiëren (concept), vaststellen (beheerder; het vorige vastgestelde plan wordt concept), en
*Twee scenario's vergelijken*: oogst per week, afzetdekking per moment en kWh per week naast elkaar.

**Genereren** (in een concept): vul een jaar in (dat jaar moet leeg zijn) of een aantal rondes. De app vult per
kap de ketting vooruit met **voorstellen**; daarna is het jaaroverzicht van dat scenario te zien. Stel eerst de
rasverdeling en eventueel terugvalwaarden in (Instellingen → Genereren) en train het model (Beheer → Model).

**Zoeken**: kies het jaar en *Zoeken*. Op de achtergrond komt een lijst met voorstellen, bijvoorbeeld "Kap 14,
ronde 9: plantdatum 1 dag(en) later (19-2 → 19-3): +3.200 takken in het venster van Moederdag (2027-05-09)".
Niets wordt vanzelf toegepast; *Toepassen* zet die ene waarde vast (handmatig, met reden en auditregel), *Alle
voorstellen toepassen* doet ze in volgorde. Een veld dat intussen handmatig is ingevuld, wordt nooit overschreven.
Stel eerst de gewichten in (Instellingen → Zoeken); zonder gewichten zijn er geen voorstellen.

**Jaaroverzicht**: kappen × weken met P/LD/KD/O, en onder de kappen de oogst per week, kWh per week (alleen dagen
met een teelt in de kap), afzetmomenten, feestdagen en weken met lage prijs.
