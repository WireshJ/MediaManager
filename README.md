# 🎬 MediaManager

Een selfhosted webapplicatie voor het beheren van IPTV content via de Xtream Codes API.
Download films en series rechtstreeks naar je NAS, met automatische Jellyfin integratie.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.2.2-blue)

---

## ✨ Functies

| Pagina | Beschrijving |
|--------|-------------|
| 📺 **Browse** | Zoek en maak `.strm` bestanden via Xtream API (Live, Films, Series) |
| 🔍 **Discover** | Trending en populaire titels via TMDB, doorzoekbaar in je IPTV provider |
| 📚 **Bibliotheek** | Bekijk alle content op je media share, voeg toe aan de download queue |
| ⏳ **Queue** | Download via yt-dlp met voortgangsbalk, retry en schijfruimte indicator |
| 🎯 **Wishlist** | Automatisch downloaden zodra een film/serie beschikbaar is bij je provider |
| ⚙️ **Instellingen** | Beheer opslag, integraties en beveiliging |

### 🔧 Onder de motorkap
- **TMDB naamgeving** — Films en series krijgen bij toevoegen direct de officiële TMDB naam. Fallback op providernaam als TMDB uitstaat
- **Postprocessing** — Prefix/suffix cleaning en automatisch samenvoegen van dubbele serie-mappen
- **Jellyfin push** — Automatische library scan na aanmaken `.strm` of voltooide download
- **Ondertitels** — Automatisch downloaden via OpenSubtitles na een download
- **Beveiliging** — Optioneel wachtwoord (SHA-256) voor toegang tot de instellingen

---

## 🎯 Wishlist

Voeg films en series toe aan de wishlist vanuit Discover. De app controleert automatisch of de titel beschikbaar is bij je provider.

### Hoe werkt het?

1. Klik op een film/serie in **Discover** → knop **Wishlist** in het detailvenster
2. Kies een minimale kwaliteit en/of gewenste taal *(alleen van toepassing op films)*
3. De achtergrond-worker controleert periodiek alle wishlist-items
4. Zodra een match gevonden wordt die aan de criteria voldoet → automatisch toegevoegd aan de bibliotheek

> **Films** worden gecheckt op kwaliteit (bijv. minimaal `1080p`) en taal (bijv. `NL`).
> **Series** worden alleen gecheckt op aanwezigheid — kwaliteit en taal zijn niet controleerbaar via de provider-URL.

### Statussen

| Status | Betekenis |
|--------|-----------|
| ⏳ **Wachtend** | Nog niet gevonden bij de provider |
| 🔍 **Gevonden** | Beschikbaar bij de provider — voor films: kwaliteit/taal voldoet nog niet aan criteria; voor series: klik op de poster om handmatig een versie te kiezen |
| ✅ **In bibliotheek** | Toegevoegd en klaar |

### Handmatig toevoegen

Als de film gevonden is maar de automatische criteria niet matchen, klik je op de poster in de wishlist. Je ziet alle beschikbare versies bij de provider en kunt er één handmatig als `.strm` aanmaken.

### Instellingen (via ⚙️ Instellingen → Wishlist)

| Instelling | Beschrijving |
|------------|-------------|
| **Ingeschakeld** | Zet de wishlist-worker aan of uit |
| **Beschikbare taalfilters** | Bepaalt welke taalopties je ziet bij het toevoegen van een item (bijv. alleen EN en NL) |
| **Kwaliteitsmatch** | Hoe strikt de kwaliteitscheck is (zie tabel hieronder) |
| **Blokkeer onbekende talen** | Streams zonder taalmetadata automatisch overslaan (zie hieronder) |

#### Kwaliteitsmatch modi

| Modus | 4K | 1080p | 720p | Wanneer gebruiken |
|-------|----|-------|------|-------------------|
| **Hoog** | ≥ 2160p | ≥ 1080p | ≥ 720p | Alleen exacte standaard 16:9 resoluties |
| **Medium** *(standaard)* | ≥ 1440p | ≥ 800p | ≥ 520p | Inclusief widescreen (bijv. 1600p = 4K cinemascope) |
| **Laag** | ≥ 1080p | ≥ 600p | ≥ 360p | Ruime drempel, ook lagere encodes van dezelfde kwaliteitsklasse |

> Tip: gebruik **Medium** als je films met een cinemascope-ratio (2.39:1) wilt meenemen. Een 4K film op dat formaat heeft hoogte ≈ 1600px in plaats van 2160px.

#### Blokkeer onbekende talen

De app detecteert taal op drie manieren (in volgorde):
1. **Audio-metadata** uit de stream (meest betrouwbaar)
2. **Naam-prefix** — bijv. `NL - Titel` of `EN - Titel`
3. **Onbekend** — bijv. `AMZ - Titel` geeft geen taalinformatie

| Instelling | Gedrag bij onbekende taal |
|------------|--------------------------|
| **Uit** *(standaard)* | Stream wordt altijd geaccepteerd |
| **Aan** | Stream wordt overgeslagen → status `Gevonden` → handmatig kiezen |

---

## 🐳 Docker installatie

### Snel starten

```bash
# 1. Maak een data map aan
mkdir -p /opt/mediamanager/data

# 2. Download de docker-compose.yml
curl -o /opt/mediamanager/docker-compose.yml \
  https://raw.githubusercontent.com/WireshJ/MediaManager/main/docker-compose.yml

# 3. Start de container
cd /opt/mediamanager
docker compose up -d
```

Open vervolgens `http://localhost:8080` in je browser.

> **Let op:** Pas in `docker-compose.yml` het volume `/mnt/media:/mnt/media` aan naar jouw media locatie.

### Docker image

| Image | Gebruik |
|-------|---------|
| `ghcr.io/wireshj/mediamanager:latest` | Alle functies inclusief Wishlist |

#### Docker management UI (Arcane / Portainer)

Gebruik het image: `ghcr.io/wireshj/mediamanager:latest`

Volumes:
- `/pad/naar/data:/app/data` — instellingen en cache
- `/mnt/media:/mnt/media` — media opslag (bij mount mode)

Poort: `8080`

---

### 🐍 Handmatig (Python)

```bash
# 1. Clone de repository
git clone https://github.com/WireshJ/MediaManager.git
cd MediaManager

# 2. Installeer dependencies
pip install -r requirements.txt --break-system-packages

# 3. Installeer mediainfo voor Wishlist kwaliteitscheck
apt install mediainfo -y

# 4. Start de app
python app.py
```

### Aangepaste poort of data map

```bash
DATA_DIR=/opt/mediamanager/data PORT=8080 python app.py
```

---

## 🖥️ Installeren als Linux service (systemd)

### 1. Maak het service bestand aan

```bash
nano /etc/systemd/system/mediamanager.service
```

```ini
[Unit]
Description=MediaManager
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mediamanager
Environment=DATA_DIR=/opt/mediamanager/data
Environment=PORT=8080
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /opt/mediamanager/app.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

> Gebruik `which python3` om het juiste Python pad te vinden.

### 2. Activeer en start

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

---

## 💾 Storage modes

Stel je opslaglocatie in via **Instellingen → Storage & Paden**:

| Mode | Wanneer gebruiken |
|------|-------------------|
| 📁 **Mount** | Server heeft directe toegang tot de media map (bijv. `/mnt/media`) |
| 🌐 **SMB** | App draait in een LXC container zonder mount rechten |
| 📡 **FTP** | Universeel alternatief als SMB niet beschikbaar is |

### SMB instellen (aanbevolen voor LXC containers)

| Veld | Voorbeeld |
|------|-----------|
| Host | `192.168.1.100` |
| Share | `media` |
| Films pad | `Films` |
| Series pad | `Series` |

---

## 🔌 Optionele integraties

| Service | Functie | Vereist |
|---------|---------|---------|
| 🎞️ **Jellyfin** | Automatische library scan | URL + API key |
| 🎭 **TMDB** | Metadata, posters, Discover pagina | Gratis API key |
| 💬 **OpenSubtitles** | Automatisch ondertitels downloaden | Account + API key |
| 🎯 **Wishlist** | Automatisch toevoegen op kwaliteit/taal | mediainfo |

- **TMDB API key** — Gratis aan te vragen op [themoviedb.org](https://www.themoviedb.org/settings/api)
- **Jellyfin API key** — Te vinden in Jellyfin → Dashboard → API Keys

---

## 📁 Output structuur

```
/mnt/media/
├── Live/
│   └── Canvas.strm
├── Films/
│   └── The Dark Knight/
│       └── The Dark Knight.strm
└── Series/
    └── Breaking Bad/
        ├── Season 01/
        │   ├── Breaking Bad S01E01.strm
        │   └── Breaking Bad S01E01.nl.srt
```

---

## 🔐 Beveiliging

- Wachtwoord instellen via **Instellingen → Beveiliging**
- Wachtwoord leeg laten = geen beveiliging
- Wachtwoord wordt opgeslagen als SHA-256 hash
- De Flask sessie sleutel wordt automatisch gegenereerd en opgeslagen in `data/.secret_key`
- Voor productie kun je ook `APP_SECRET` als omgevingsvariabele instellen

---

## 🌍 Omgevingsvariabelen

| Variabele | Standaard | Omschrijving |
|-----------|-----------|--------------|
| `DATA_DIR` | `./data` | Map voor config, cache en tijdelijke bestanden |
| `PORT` | `8080` | Poort waarop de app luistert |
| `APP_SECRET` | *(automatisch)* | Flask sessie sleutel |
| `PYTHONUNBUFFERED` | `1` | Aanbevolen bij gebruik als service |

---

Zie [Releases](https://github.com/WireshJ/MediaManager/releases) voor de volledige changelog.
