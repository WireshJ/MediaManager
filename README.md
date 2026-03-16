# MediaManager

Gecombineerde applicatie van **m3udownloader** + **xstream-studio** (m3usorter).

## Functies

- **Browse** – Zoek en maak `.strm` bestanden via Xtream API (Live, Films, Series)
- **Bibliotheek** – Beheer lokale `.strm` bestanden, voeg toe aan download queue
- **Queue** – Download bestanden via yt-dlp met voortgang
- **Postprocessing** – Automatisch hernoemen op basis van TMDB ID en prefix cleaning (vervangt de bash scripts)
- **Instellingen** – Alles configureerbaar via de UI (Xtream, Output map, Jellyfin, TMDB, OpenSubtitles)

## Installatie

```bash
pip install -r requirements.txt
```

### Vereisten
- Python 3.11+
- `yt-dlp` (voor downloads): `pip install yt-dlp`

## Starten

```bash
python app.py
```

Of met een custom data/output map:

```bash
DATA_DIR=/mnt/config/mediamanager PORT=8080 python app.py
```

## Output map

De applicatie gebruikt **één centrale output map** (de mount op de server), instelbaar via Instellingen → Output & Paden.

Standaard structuur:
```
/media/library/
├── Live/           ← Live .strm bestanden
├── Movies/         ← Film mappen (bijv. "12345 - The Dark Knight/")
└── Series/         ← Serie mappen (bijv. "Breaking Bad/")
```

## Postprocessing (vervangt bash scripts)

De postprocessing functie vervangt `remove-prefix-movies.sh` en `remove-prefix-series.sh`:

- **Films**: Extraheert TMDB ID uit mapnaam → haalt officiële titel op → hernoemt map en .strm bestanden
- **Series**: Verwijdert `|EN|`, `EN -`, `(US)` prefixen/suffixen van map- en bestandsnamen

Wordt automatisch uitgevoerd na elke download, of handmatig via de knoppen op de Bibliotheek pagina.

## Optionele integraties

| Service | Functie |
|---------|---------|
| **Jellyfin** | Automatisch library refresh na download |
| **TMDB** | Metadata/posters in bibliotheek + hernoemen op TMDB ID |
| **OpenSubtitles** | Automatisch ondertitels downloaden na verwerking |

Alle integraties zijn **optioneel** en kunnen in- of uitgeschakeld worden via Instellingen.

## Account informatie

Op de Instellingen pagina wordt de Xtream account info getoond:
- Status (Active/Inactive)
- Vervaldatum
- Actieve/max verbindingen
- Trial account

## Docker (optioneel)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y yt-dlp
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
ENV DATA_DIR=/data
VOLUME ["/data", "/media"]
EXPOSE 8080
CMD ["python", "app.py"]
```
