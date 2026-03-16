# MediaManager

Gecombineerde applicatie van **m3udownloader** + **xstream-studio** voor het beheren van IPTV content via Xtream Codes API.

## Functies

- **Browse** — Zoek en maak `.strm` bestanden via Xtream API (Live, Films, Series)
- **Bibliotheek** — Bekijk alle films en series op je media share, voeg toe aan download queue
- **Queue** — Download bestanden via yt-dlp met voortgangsbalk en retry
- **Postprocessing** — Automatisch hernoemen op TMDB ID en prefix/suffix cleaning (vervangt de bash scripts)
- **Samenvoegen** — Dubbele serie-mappen worden automatisch samengevoegd
- **Instellingen** — Alles configureerbaar via de UI

## Vereisten

- Python 3.11+
- pip packages (zie `requirements.txt`)

## Installatie

```bash
pip install -r requirements.txt
```

## Starten

```bash
python app.py
```

Met aangepaste poort of data map:

```bash
DATA_DIR=/mnt/config/mediamanager PORT=8080 python app.py
```

## Storage Modes

In Instellingen → Storage & Paden kies je één van drie modes:

| Mode | Wanneer gebruiken |
|------|-------------------|
| **Mount** | Server heeft directe toegang tot de media map (bijv. `/mnt/media`) |
| **SMB** | App draait in een LXC container zonder mount rechten |
| **FTP** | Universeel alternatief als SMB niet beschikbaar is |

### SMB instellen (aanbevolen voor LXC containers)

Vul in Instellingen in:
- **Host**: IP van je NAS (bijv. `192.168.1.203`)
- **Share**: naam van de share (bijv. `media`)
- **Gebruikersnaam / Wachtwoord**: NAS credentials
- **Films pad / Series pad**: submappen op de share (bijv. `Films`, `Series`)

## Output structuur

```
/mnt/media/               ← basis map (of SMB share root)
├── Live/                 ← live .strm bestanden
├── Films/
│   └── The Dark Knight/
│       └── The Dark Knight.strm
└── Series/
    └── Breaking Bad/
        ├── Breaking Bad S01E01.strm
        ├── Breaking Bad S01E01.mkv
        └── Breaking Bad S01E01.nl.srt
```

## Postprocessing

Vervangt de bash scripts `remove-prefix-movies.sh` en `remove-prefix-series.sh`.

Wat er gestript wordt van map- en bestandsnamen:
- Channel prefixen: `4K-OSN+ -`, `|EN|`, `NL -`, `beQ -`, etc.
- Jaar: `(2011)`, `(2023)`
- Landcode: `(US)`, `(NL)`, `(JP)`
- Kwaliteitslabels: `4K`, `1080p`, `HDR`, `BluRay`, etc.

Dubbele serie-mappen (bijv. `Better Call Saul` + `EN - Better Call Saul (US)`) worden automatisch samengevoegd.

Postprocessing draait automatisch na elke `.strm` aanmaak en na elke download. Handmatig via de knoppen op de Bibliotheek pagina.

## Optionele integraties

Alle integraties zijn optioneel en in/uitschakelbaar via Instellingen:

| Service | Functie |
|---------|---------|
| **Jellyfin** | Automatisch library refresh na download |
| **TMDB** | Metadata, posters en hernoemen op TMDB ID |
| **OpenSubtitles** | Automatisch ondertitels downloaden na verwerking |

## Docker / LXC

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
ENV DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8080
CMD ["python", "app.py"]
```

> **Let op:** In een Docker container of LXC gebruik je SMB of FTP mode om bestanden op de NAS op te slaan. Mount mode vereist directe toegang tot het bestandssysteem.
