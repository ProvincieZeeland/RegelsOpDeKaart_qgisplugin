# Regels op de kaart QGIS Plug-in

## Overzicht

De voorheen **Ruimtelijke Plannen** en tegenwoordig **Regels op de kaart**, plug-in stelt gebruikers in staat om ruimtelijke plannen op te halen van de **Omgevingswet API**. Dit geeft de mogelijkheid om plannen direct binnen QGIS te bekijken en te importeren, zodat deze lagen kunnen worden gestijld en gevisualiseerd. Deze plug-in is handig voor professionals die met ruimtelijke ordening en omgevingsplannen werken.

## Functies

- Haal ruimtelijke plannen op via de **API van de Omgevingswet**.
- Ondersteunt zowel punt- als polygoontekeningen op de kaart om plannen binnen een bepaald gebied op te vragen.
- Selecteer tot maximaal 100 plannen die overlappen met het gekozen gebied.
- Voeg plannen toe aan je QGIS-project.
- Geïmporteerde plannen worden automatisch gegroepeerd en voorzien van stijlen op basis van de dataset.
- Gemakkelijke integratie binnen QGIS-projecten voor ruimtelijke analyse en rapportage.

> **Let op**: Deze plug-in ondersteunt nog **geen nieuwe plannen op basis van "regels op kaart"**, zoals omgevingsdocumenten vanuit het DSO.

## Installatie

1. Download en installeer de plug-in via de [QGIS Plug-in Manager](https://qgis.org/en/site/forusers/plugins.html).
2. Vraag een API-key aan via de volgende link:  
   [API-key aanvragen](https://aandeslagmetdeomgevingswet.nl/ontwikkelaarsportaal/formulieren/api-key-aanvragen-0/).
3. Voer de API-key in de plug-in-instellingen in.

## Gebruik

1. **API-key invoeren**: Na installatie dien je je API-key in te voeren in de plug-in-instellingen. Deze key is verplicht om verbinding te maken met de Omgevingswet API.
2. **Tekenmethode kiezen**: Selecteer een tekenmethode (punt of polygoon) en klik op de kaart om het gebied te selecteren waarvan je ruimtelijke plannen wilt opvragen.
3. **Plannen opvragen**: De plug-in haalt maximaal 100 overlappende ruimtelijke plannen op binnen het geselecteerde gebied.
4. **Plannen importeren**: Selecteer de gewenste plannen en klik op **Importeren** om deze toe te voegen aan je QGIS-project. Elke laag wordt automatisch gegroepeerd en gestijld per plan.

## Belangrijke opmerking

Deze plug-in is **uitsluitend een hulpmiddel** om gegevens van de Omgevingswet API te visualiseren binnen QGIS. Er kunnen **geen rechten worden ontleend** aan de gegevens of de werking van deze tool. Het is de verantwoordelijkheid van de gebruiker om de juistheid en relevantie van de data te controleren en na te gaan of deze geschikt is voor het beoogde gebruik.

## API Documentatie

Voor meer informatie over de API kun je de officiële documentatie raadplegen:  
[API Ruimtelijke Plannen Opvragen](https://aandeslagmetdeomgevingswet.nl/ontwikkelaarsportaal/api-register/api/rp-opvragen/).

## Vereisten

- QGIS 3.22 of hoger.
- Een geldige API-key van de Omgevingswet API.

## Licentie

Deze plug-in is beschikbaar onder de **EUPL-licentie**. Zie het LICENSE-bestand voor meer details.
