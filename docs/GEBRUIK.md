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
