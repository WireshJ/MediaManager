# 🎬 MediaManager

Een selfhosted webapplicatie voor het beheren van IPTV content via de Xtream Codes API. Maak `.strm` bestanden aan voor Jellyfin, download content via yt-dlp, en laat de wishlist automatisch bijhouden wat er beschikbaar komt bij je provider.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.2.3-blue)

---

## Inhoudsopgave

- [Functies](#-functies)
- [Vereisten](#-vereisten)
- [Installatie](#-installatie)
  - [Docker (aanbevolen)](#docker-aanbevolen)
  - [Handmatig (Python)](#handmatig-python)
  - [Linux service (systemd)](#linux-service-systemd)
- [Configuratie](#-configuratie)
  - [Xtream API](#xtream-api)
  - [Storage modes](#storage-modes)
  - [Omgevingsvariabelen](#omgevingsvariabelen)
  - [Beveiliging](#beveiliging)
- [Integraties](#-integraties)
- [Pagina's](#-paginas)
  - [Browse](#browse)
  - [Discover](#discover)
  - [Bibliotheek](#bibliotheek)
  - [Queue](#queue)
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
| 🔍 **Discover** | Trending en populaire titels via TMDB, doorzoekbaar in je IPTV provider *(vereist TMDB)* |
| 📚 **Bibliotheek** | Bekijk alle content op je media share, voeg toe aan de download queue |
| ⏳ **Queue** | Download volledige bestanden via yt-dlp met voortgangsbalk en retry |
| 🎯 **Wishlist** | Automatisch `.strm` aanmaken zodra een film/serie beschikbaar komt bij je provider |
| ⚙️ **Instellingen** | Beheer verbinding, opslag, integraties en beveiliging |

> **`.strm` vs downloaden:** Een `.strm` bestand is een tekstbestand met alleen de stream-URL. Jellyfin speelt de content direct via de provider. De Queue downloadt het volledige videobestand naar je opslag. Beide opties zijn beschikbaar; de Wishlist werkt altijd via `.strm`.

**Onder de motorkap:**
- **TMDB naamgeving** — Films en series krijgen bij toevoegen direct de officiële TMDB naam. Fallback op providernaam als TMDB uitstaat
- **Postprocessing** — Prefix/suffix cleaning en automatisch samenvoegen van dubbele serie-mappen
- **Jellyfin push** — Automatische library scan na aanmaken `.strm` of voltooide download
- **Ondertitels** — Automatisch downloaden via OpenSubtitles na een download
- **Provider cache** — Streams worden lokaal gecached (standaard 6 uur) zodat de provider niet bij elke actie wordt aangesproken

---

## 📋 Vereisten

| Vereiste | Minimale versie | Opmerking |
|----------|----------------|-----------|
| Python | 3.11+ | Alleen bij handmatige installatie |
| yt-dlp | Actueel | Voor downloaden via de Queue |
| mediainfo | Actueel | Voor kwaliteitscheck in de Wishlist |
| Xtream Codes provider | — | Verplicht — basis van de app |
| TMDB API key | — | Optioneel, vereist voor Discover |

> Bij gebruik van de **Docker image** zijn yt-dlp en mediainfo al inbegrepen.

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

# Vereist voor Wishlist kwaliteitscheck
apt install mediainfo -y

# Vereist voor downloaden via Queue
pip install yt-dlp
# of: apt install yt-dlp

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

### Xtream API

Stel in via **Instellingen → Xtream API** — dit is de basisverbinding met je IPTV provider.

| Veld | Omschrijving |
|------|-------------|
| **Server** | URL van je provider, bijv. `http://provider.com` |
| **Gebruikersnaam** | Xtream Codes gebruikersnaam |
| **Wachtwoord** | Xtream Codes wachtwoord |
| **Poort** | Optioneel, laat leeg als poort al in de server-URL zit |
| **Cache TTL (uren)** | Hoe lang de provider-catalogus lokaal wordt bewaard (standaard: 6 uur) |

> **Cache TTL** bepaalt ook hoe vaak de Wishlist-worker nieuwe streams controleert. Bij een TTL van 6 uur checkt de worker maximaal eens per 6 uur of er nieuwe matches zijn.

Na opslaan kun je via **Cache vernieuwen** de provider-catalogus direct ophalen.

---

### Storage modes

Stel in via **Instellingen → Storage & Paden**:

| Mode | Wanneer gebruiken |
|------|-------------------|
| 📁 **Mount** | Server heeft directe toegang tot de media map (bijv. `/mnt/media`) |
| 🌐 **SMB** | App draait in een LXC container of Docker zonder mount rechten |
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
| 🎞️ **Jellyfin** | Automatische library scan na toevoegen of downloaden | URL + API key |
| 🎭 **TMDB** | Metadata, posters, Discover pagina, officiële titelnamen | Gratis API key |
| 💬 **OpenSubtitles** | Automatisch ondertitels downloaden na een download | Account + API key |
| 🎯 **Wishlist / mediainfo** | Kwaliteitscheck van streams | `mediainfo` (inbegrepen in Docker) |

- **TMDB API key** — Gratis aan te vragen op [themoviedb.org](https://www.themoviedb.org/settings/api)
- **Jellyfin API key** — Te vinden in Jellyfin → Dashboard → API Keys
- **OpenSubtitles API key** — Aan te vragen op [opensubtitles.com](https://www.opensubtitles.com/consumers)

---

## 📺 Pagina's

### Browse

Doorzoek de volledige provider-catalogus (Live, Films, Series) en maak direct `.strm` bestanden aan. Werkt op basis van de lokale cache — gebruik **Cache vernieuwen** om de nieuwste content te zien.

- **Live** — Maak `.strm` bestanden aan voor live kanalen
- **Films** — Zoek films op naam, maak `.strm` aan of voeg toe aan de download queue
- **Series** — Blader per serie, maak `.strm` bestanden aan per seizoen of aflevering

---

### Discover

Bekijk trending en populaire titels via TMDB. Vanuit Discover kun je direct zoeken of een titel beschikbaar is bij je provider.

> **Vereist TMDB** — zonder API key toont Discover een lege pagina. Stel de TMDB API key in via Instellingen → TMDB.

- Klik op een poster → detailpaneel met beschrijving, cast en beschikbare streams bij je provider
- **Maak .strm** — direct aanmaken als de stream beschikbaar is
- **Wishlist** — toevoegen aan de wishlist als de film nog niet beschikbaar is of de kwaliteit nog niet goed is

---

### Bibliotheek

Overzicht van alle content die al op je media share staat. Ondersteunt Mount, SMB en FTP.

- Bladeren door je bestaande Films, Series en Live mappen
- Bestanden toevoegen aan de download queue om opnieuw of in hogere kwaliteit te downloaden
- Schijfruimte indicator per opslag-locatie

---

### Queue

Download volledige videobestanden via yt-dlp. Geschikt voor het permanent opslaan van content op je NAS.

- Voortgangsbalk met snelheid, ETA en totale bestandsgrootte
- Automatische retry bij een mislukte download
- Schijfruimte controle vóór de download start
- Download automatisch gestopt na 4 uur als het process vastloopt
- Ondertitels automatisch gedownload via OpenSubtitles na voltooiing *(indien ingesteld)*

---

## 🎯 Wishlist

Voeg films en series toe vanuit **Discover**. De worker controleert automatisch of de titel beschikbaar is bij je provider en maakt een `.strm` aan zodra de criteria matchen.

### Hoe werkt het

1. Klik op een film/serie in **Discover** → knop **Wishlist**
2. Kies optioneel een minimale kwaliteit (bijv. `1080p`) en/of gewenste taal (bijv. `NL`) — *alleen van toepassing op films*
3. De achtergrond-worker controleert periodiek de lokale provider-cache op nieuwe matches
4. Zodra een match gevonden wordt die aan de criteria voldoet → automatisch `.strm` aangemaakt in je bibliotheek
5. Je ontvangt een notificatie op de wishlist-pagina

> **Films** worden gecheckt op kwaliteit én taal via mediainfo.
> **Series** worden alleen gecheckt op aanwezigheid — kwaliteit is niet controleerbaar via de series-URL. Bij een match worden direct alle seizoenen en afleveringen als `.strm` aangemaakt.

Als de film gevonden is maar de criteria niet matchen, klik je op de poster. Je ziet alle beschikbare versies bij de provider en kunt er één handmatig aanmaken.

---

### Statussen

| Status | Betekenis |
|--------|-----------|
| ⏳ **Wachtend** | Nog niet gevonden bij de provider |
| 🔍 **Gevonden** | Beschikbaar maar criteria matchen niet, of serie die handmatige selectie vereist |
| ✅ **In bibliotheek** | `.strm` aangemaakt en klaar |

---

### Instellingen

Stel in via **Instellingen → Wishlist**:

| Instelling | Beschrijving |
|------------|-------------|
| **Ingeschakeld** | Zet de wishlist-worker aan of uit |
| **Cache TTL** | Bepaalt hoe vaak de worker controleert — ingesteld via Instellingen → Xtream API → Cache TTL |
| **Beschikbare taalfilters** | Welke taalopties zichtbaar zijn bij het toevoegen van een item (bijv. alleen EN en NL) |
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
        └── Season 02/
            └── Breaking Bad S02E01.strm
```

---

Zie [Releases](https://github.com/WireshJ/MediaManager/releases) voor de volledige changelog.
