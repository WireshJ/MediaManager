# 🎬 MediaManager

Een selfhosted webapplicatie voor het beheren van IPTV content via de Xtream Codes API.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.2.2-blue)

---

## Inhoudsopgave

- [Functies](#-functies)
- [Installatie](#-installatie)
  - [Docker (aanbevolen)](#docker-aanbevolen)
  - [Handmatig (Python)](#handmatig-python)
  - [Linux service (systemd)](#linux-service-systemd)
- [Configuratie](#-configuratie)
  - [Storage modes](#storage-modes)
  - [Omgevingsvariabelen](#omgevingsvariabelen)
  - [Beveiliging](#beveiliging)
- [Integraties](#-integraties)
- [Wishlist](#-wishlist)
  - [Hoe werkt het](#hoe-werkt-het)
  - [Statussen](#statussen)
  - [Instellingen](#instellingen)
  - [Kwaliteitsmatch](#kwaliteitsmatch-modi)
  - [Taaldetectie](#taaldetectie)
- [Output structuur](#-output-structuur)

---

## ✨ Functies

| Pagina | Beschrijving |
|--------|-------------|
| 📺 **Browse** | Zoek en maak `.strm` bestanden via Xtream API (Live, Films, Series) |
| 🔍 **Discover** | Trending en populaire titels via TMDB, doorzoekbaar in je IPTV provider |
| 📚 **Bibliotheek** | Bekijk alle content op je media share, voeg toe aan de download queue |
| ⏳ **Queue** | Download via yt-dlp met voortgangsbalk, retry en schijfruimte indicator |
| 🎯 **Wishlist** | Automatisch `.strm` aanmaken zodra een film/serie beschikbaar is bij je provider |
| ⚙️ **Instellingen** | Beheer opslag, integraties en beveiliging |

**Onder de motorkap:**
- **TMDB naamgeving** — Films en series krijgen bij toevoegen direct de officiële TMDB naam
- **Postprocessing** — Prefix/suffix cleaning en automatisch samenvoegen van dubbele serie-mappen
- **Jellyfin push** — Automatische library scan na aanmaken `.strm` of voltooide download
- **Ondertitels** — Automatisch downloaden via OpenSubtitles na een download

---

## 🚀 Installatie

### Docker (aanbevolen)

```bash
mkdir -p /opt/mediamanager/data

curl -o /opt/mediamanager/docker-compose.yml \
  https://raw.githubusercontent.com/WireshJ/MediaManager/main/docker-compose.yml

cd /opt/mediamanager
docker compose up -d
```

Open `http://localhost:8080` in je browser.

> **Let op:** Pas in `docker-compose.yml` het volume `/mnt/media:/mnt/media` aan naar jouw media locatie.

| Image | Gebruik |
|-------|---------|
| `ghcr.io/wireshj/mediamanager:latest` | Alle functies inclusief Wishlist |

**Volumes:**
- `/pad/naar/data:/app/data` — instellingen en cache
- `/mnt/media:/mnt/media` — media opslag (bij mount mode)

**Poort:** `8080`

---

### Handmatig (Python)

```bash
git clone https://github.com/WireshJ/MediaManager.git
cd MediaManager

pip install -r requirements.txt --break-system-packages
apt install mediainfo -y

python app.py
```

Aangepaste poort of data map:

```bash
DATA_DIR=/opt/mediamanager/data PORT=8080 python app.py
```

---

### Linux service (systemd)

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

```bash
systemctl daemon-reload
systemctl enable mediamanager
systemctl start mediamanager
systemctl status mediamanager
```

**Logs bekijken:**
```bash
journalctl -u mediamanager -f        # live
journalctl -u mediamanager -n 100    # laatste 100 regels
```

---

## ⚙️ Configuratie

### Storage modes

Stel in via **Instellingen → Storage & Paden**:

| Mode | Wanneer gebruiken |
|------|-------------------|
| 📁 **Mount** | Server heeft directe toegang tot de media map (bijv. `/mnt/media`) |
| 🌐 **SMB** | App draait in een LXC container zonder mount rechten |
| 📡 **FTP** | Universeel alternatief als SMB niet beschikbaar is |

**SMB instellen:**

| Veld | Voorbeeld |
|------|-----------|
| Host | `192.168.1.100` |
| Share | `media` |
| Films pad | `Films` |
| Series pad | `Series` |

---

### Omgevingsvariabelen

| Variabele | Standaard | Omschrijving |
|-----------|-----------|--------------|
| `DATA_DIR` | `./data` | Map voor config, cache en tijdelijke bestanden |
| `PORT` | `8080` | Poort waarop de app luistert |
| `APP_SECRET` | *(automatisch)* | Flask sessie sleutel |
| `PYTHONUNBUFFERED` | `1` | Aanbevolen bij gebruik als service |

---

### Beveiliging

Stel in via **Instellingen → Beveiliging**:

- Wachtwoord leeg laten = geen beveiliging
- Wachtwoord wordt opgeslagen als SHA-256 hash
- De Flask sessie sleutel wordt automatisch gegenereerd in `data/.secret_key`
- Voor productie kun je ook `APP_SECRET` als omgevingsvariabele instellen

---

## 🔌 Integraties

Stel in via **Instellingen**:

| Service | Functie | Vereist |
|---------|---------|---------|
| 🎞️ **Jellyfin** | Automatische library scan na toevoegen | URL + API key |
| 🎭 **TMDB** | Metadata, posters, Discover pagina | Gratis API key |
| 💬 **OpenSubtitles** | Automatisch ondertitels downloaden | Account + API key |
| 🎯 **Wishlist** | Kwaliteitscheck van streams | `mediainfo` (inbegrepen in Docker image) |

- **TMDB API key** — Gratis aan te vragen op [themoviedb.org](https://www.themoviedb.org/settings/api)
- **Jellyfin API key** — Te vinden in Jellyfin → Dashboard → API Keys
- **OpenSubtitles API key** — Aan te vragen op [opensubtitles.com](https://www.opensubtitles.com/consumers)

---

## 🎯 Wishlist

Voeg films en series toe vanuit **Discover**. De worker controleert automatisch of de titel beschikbaar is bij je provider.

### Hoe werkt het

1. Klik op een film/serie in **Discover** → knop **Wishlist**
2. Kies een minimale kwaliteit en/of gewenste taal *(alleen van toepassing op films)*
3. De achtergrond-worker controleert periodiek alle wishlist-items op basis van je provider-cache
4. Zodra een match gevonden wordt → automatisch `.strm` aangemaakt in je bibliotheek

> **Films** worden gecheckt op kwaliteit en taal.
> **Series** worden alleen gecheckt op aanwezigheid — kwaliteit is niet te controleren via de series-URL.

Als de film gevonden is maar de criteria niet matchen, klik je op de poster. Je ziet alle beschikbare versies en kunt er één handmatig aanmaken.

---

### Statussen

| Status | Betekenis |
|--------|-----------|
| ⏳ **Wachtend** | Nog niet gevonden bij de provider |
| 🔍 **Gevonden** | Beschikbaar maar criteria matchen niet — klik de poster voor handmatige selectie |
| ✅ **In bibliotheek** | `.strm` aangemaakt en klaar |

---

### Instellingen

Stel in via **Instellingen → Wishlist**:

| Instelling | Beschrijving |
|------------|-------------|
| **Ingeschakeld** | Zet de wishlist-worker aan of uit |
| **Cache TTL** | Hoe vaak de provider-catalogus wordt ververst (zie Instellingen → Xtream API). Dit bepaalt ook hoe vaak de worker controleert |
| **Beschikbare taalfilters** | Welke taalopties beschikbaar zijn bij het toevoegen van een item (bijv. alleen EN en NL) |
| **Kwaliteitsmatch** | Hoe strikt de kwaliteitscheck is (zie hieronder) |
| **Blokkeer onbekende talen** | Streams zonder detecteerbare taal automatisch overslaan |

---

### Kwaliteitsmatch modi

| Modus | 4K | 1080p | 720p | Wanneer gebruiken |
|-------|----|-------|------|-------------------|
| **Hoog** | ≥ 2160p | ≥ 1080p | ≥ 720p | Alleen exacte standaard 16:9 resoluties |
| **Medium** *(standaard)* | ≥ 1440p | ≥ 800p | ≥ 520p | Inclusief widescreen (bijv. 1600p = 4K cinemascope) |
| **Laag** | ≥ 1080p | ≥ 600p | ≥ 360p | Ruime drempel, ook lagere encodes van dezelfde kwaliteitsklasse |

> **Tip:** Gebruik **Medium** als je films met een cinemascope-ratio (2.39:1) wilt meenemen. Een 4K film op dat formaat heeft hoogte ≈ 1600px in plaats van 2160px.

---

### Taaldetectie

De app detecteert taal in drie stappen:

1. **Audio-metadata** uit de stream *(meest betrouwbaar)*
2. **Naam-prefix** — bijv. `NL - Titel` of `EN - Titel`
3. **Onbekend** — bijv. `AMZ - Titel` geeft geen taalinformatie

| Instelling "Blokkeer onbekende talen" | Gedrag |
|---------------------------------------|--------|
| **Uit** *(standaard)* | Stream met onbekende taal wordt geaccepteerd |
| **Aan** | Stream wordt overgeslagen → status `Gevonden` → handmatig kiezen |

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

Zie [Releases](https://github.com/WireshJ/MediaManager/releases) voor de volledige changelog.
