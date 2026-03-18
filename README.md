# 🎬 MediaManager

Een selfhosted webapplicatie voor het beheren van IPTV content via de Xtream Codes API.
Download films en series rechtstreeks naar je NAS, met automatische Jellyfin integratie.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.0.0-blue)

---

## ✨ Functies

| Pagina | Beschrijving |
|--------|-------------|
| 📺 **Browse** | Zoek en maak `.strm` bestanden via Xtream API (Live, Films, Series) |
| 🔍 **Discover** | Trending en populaire titels via TMDB, doorzoekbaar in je IPTV provider |
| 📚 **Bibliotheek** | Bekijk alle content op je media share, voeg toe aan de download queue |
| ⏳ **Queue** | Download via yt-dlp met voortgangsbalk, retry en schijfruimte indicator |
| ⚙️ **Instellingen** | Beheer opslag, integraties en beveiliging |

### 🔧 Onder de motorkap
- **Postprocessing** — Automatisch hernoemen op TMDB ID met prefix/suffix cleaning
- **Samenvoegen** — Dubbele serie-mappen worden automatisch samengevoegd
- **Jellyfin push** — Automatische library scan na aanmaken `.strm` of voltooide download
- **Ondertitels** — Automatisch downloaden via OpenSubtitles na een download
- **Beveiliging** — Optioneel wachtwoord (SHA-256) voor toegang tot de instellingen

---

## 📋 Vereisten

- Python 3.11+
- `smbclient` (optioneel, voor schijfruimte weergave bij SMB mode)

---

## 🚀 Installatie

```bash
# 1. Clone de repository
git clone https://github.com/WireshJ/MediaManager.git
cd MediaManager

# 2. Installeer dependencies
pip install -r requirements.txt --break-system-packages

# 3. Start de app
python app.py
```

Open vervolgens `http://localhost:8080` in je browser.

### Aangepaste poort of data map

```bash
DATA_DIR=/opt/m3ustudio/data PORT=8080 python app.py
```

---

## 🖥️ Installeren als Linux service (systemd)

### 1. Maak het service bestand aan

```bash
nano /etc/systemd/system/m3ustudio.service
```

```ini
[Unit]
Description=M3U Studio
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/m3ustudio
Environment=DATA_DIR=/opt/m3ustudio/data
Environment=PORT=8080
ExecStart=/usr/bin/python3 /opt/m3ustudio/app.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

> Gebruik `which python3` om het juiste Python pad te vinden.

### 2. Activeer en start

```bash
systemctl daemon-reload
systemctl enable m3ustudio
systemctl start m3ustudio
systemctl status m3ustudio
```

### 3. Logs bekijken

```bash
journalctl -u m3ustudio -f        # live
journalctl -u m3ustudio -n 100    # laatste 100 regels
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

- **TMDB API key** — Gratis aan te vragen op [themoviedb.org](https://www.themoviedb.org/settings/api)
- **Jellyfin API key** — Te vinden in Jellyfin → Dashboard → API Keys

---

## 📁 Output structuur

```
/mnt/media/
├── Live/
│   └── Canvas.strm
├── Films/
│   └── The Dark Knight (2008)/
│       └── The Dark Knight (2008).mkv
└── Series/
    └── Breaking Bad/
        ├── Season 1/
        │   ├── Breaking Bad S01E01.mkv
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
| `APP_SECRET` | *(automatisch)* | Flask sessie sleutel — wordt automatisch aangemaakt in `data/.secret_key` als niet ingesteld |

---

## 📦 Versie

Zie [Releases](https://github.com/WireshJ/MediaManager/releases) voor de changelog.
