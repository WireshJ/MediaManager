# MediaManager

Gecombineerde applicatie van **m3udownloader** + **xstream-studio** voor het beheren van IPTV content via Xtream Codes API.

## Functies

- **Browse** — Zoek en maak `.strm` bestanden via Xtream API (Live, Films, Series)
- **Discover** — Trending, populaire en binnenkort verschijnende titels via TMDB, direct doorzoekbaar in je IPTV provider
- **Bibliotheek** — Bekijk alle films en series op je media share, voeg toe aan download queue
- **Queue** — Download bestanden via yt-dlp met voortgangsbalk en retry
- **Postprocessing** — Automatisch hernoemen op TMDB ID en prefix/suffix cleaning
- **Samenvoegen** — Dubbele serie-mappen worden automatisch samengevoegd
- **Jellyfin push** — Automatische library scan na aanmaken `.strm` of voltooide download
- **Beveiliging** — Optioneel wachtwoord voor toegang tot de instellingen

## Vereisten

- Python 3.11+
- yt-dlp

## Installatie

```bash
# 1. Installeer yt-dlp systeembreed
pip install yt-dlp --break-system-packages

# 2. Installeer Python dependencies
pip install -r requirements.txt --break-system-packages
```

## Handmatig starten

```bash
python app.py
```

Met aangepaste poort of data map:

```bash
DATA_DIR=/opt/mediamanager/data PORT=8080 python app.py
```

---

## Automatisch starten als Linux service (systemd)

### 1. Maak een service bestand aan

```bash
nano /etc/systemd/system/mediamanager.service
```

Plak de volgende inhoud en pas de paden aan:

```ini
[Unit]
Description=MediaManager IPTV
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mediamanager
Environment=DATA_DIR=/opt/mediamanager/data
Environment=PORT=8080
ExecStart=/usr/bin/python3 /opt/mediamanager/app.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

> Gebruik `which python3` om het juiste Python pad te vinden.

### 2. Activeer en start de service

```bash
systemctl daemon-reload
systemctl enable mediamanager
systemctl start mediamanager
systemctl status mediamanager
```

### 3. Logs bekijken

```bash
journalctl -u mediamanager -f        # live
journalctl -u mediamanager -n 100    # laatste 100 regels
```

### 4. Service beheren

```bash
systemctl stop mediamanager      # stoppen
systemctl restart mediamanager   # herstarten
systemctl disable mediamanager   # niet meer automatisch starten
```

---

## Storage Modes

In Instellingen → Storage & Paden kies je één van drie modes:

| Mode | Wanneer gebruiken |
|------|-------------------|
| **Mount** | Server heeft directe toegang tot de media map (bijv. `/mnt/media`) |
| **SMB** | App draait in een LXC container zonder mount rechten |
| **FTP** | Universeel alternatief als SMB niet beschikbaar is |

### SMB instellen (aanbevolen voor LXC containers)

- **Host**: IP van je NAS (bijv. `192.168.1.203`)
- **Share**: naam van de share (bijv. `media`)
- **Gebruikersnaam / Wachtwoord**: NAS credentials
- **Films pad / Series pad**: submappen op de share (bijv. `Films`, `Series`)

## Output structuur

```
/mnt/media/
├── Live/
│   └── Canvas.strm
├── Films/
│   └── The Dark Knight/
│       └── The Dark Knight.mkv
└── Series/
    └── Breaking Bad/
        ├── Breaking Bad S01E01.mkv
        └── Breaking Bad S01E01.nl.srt
```

Na een succesvolle download wordt het `.strm` bestand automatisch verwijderd.

---

## Beveiliging

De instellingenpagina kan beveiligd worden met een wachtwoord. Dit stel je in onderaan de instellingenpagina bij **Beveiliging**.

- Wachtwoord leeg laten = geen beveiliging
- Na instellen wordt het wachtwoord gevraagd bij elke sessie
- Wachtwoord wordt opgeslagen als SHA-256 hash
- Uitloggen via de knop op de instellingenpagina

---

## Optionele integraties

| Service | Functie |
|---------|---------|
| **Jellyfin** | Automatisch library scan na aanmaken `.strm` of voltooide download |
| **TMDB** | Metadata, posters, hernoemen op TMDB ID en de Discover pagina |
| **OpenSubtitles** | Automatisch ondertitels downloaden na een download |

### Jellyfin instellen

- **URL**: bijv. `https://jelly.voorbeeld.nl`
- **API key**: te vinden in Jellyfin → Dashboard → API Keys
- **Films library ID** / **Series library ID**: optioneel, voor gerichte scans per library. Te vinden in de URL als je een library opent in Jellyfin.

### TMDB instellen

Een gratis API key aanvragen op [themoviedb.org](https://www.themoviedb.org/settings/api). Vereist voor de Discover pagina en automatisch hernoemen van films.

---

## Omgevingsvariabelen

| Variabele | Standaard | Omschrijving |
|-----------|-----------|--------------|
| `DATA_DIR` | `./data` | Map voor config, cache en tijdelijke bestanden |
| `PORT` | `8080` | Poort waarop de app luistert |
| `APP_SECRET` | *(intern)* | Flask sessie sleutel, stel in voor productie |

Voor productiegebruik altijd een eigen `APP_SECRET` instellen:

```bash
Environment=APP_SECRET=jouw-eigen-geheime-sleutel
```
