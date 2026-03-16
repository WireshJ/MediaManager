# MediaManager

Gecombineerde applicatie van **m3udownloader** + **xstream-studio** voor het beheren van IPTV content via Xtream Codes API.

## Functies

- **Browse** — Zoek en maak `.strm` bestanden via Xtream API (Live, Films, Series)
- **Bibliotheek** — Bekijk alle films en series op je media share, voeg toe aan download queue
- **Queue** — Download bestanden via yt-dlp met voortgangsbalk en retry
- **Postprocessing** — Automatisch hernoemen op TMDB ID en prefix/suffix cleaning
- **Samenvoegen** — Dubbele serie-mappen worden automatisch samengevoegd
- **Instellingen** — Alles configureerbaar via de UI

## Vereisten

- Python 3.11+
- yt-dlp (systeembreed geïnstalleerd, zie hieronder)

## Installatie

```bash
# 1. Installeer yt-dlp systeembreed
pip install yt-dlp --break-system-packages
# of via pipx:
pipx install yt-dlp

# 2. Installeer Python dependencies
pip install -r requirements.txt --break-system-packages
```

## Handmatig starten

```bash
python app.py
```

Met aangepaste poort of data map:

```bash
DATA_DIR=/mnt/config/mediamanager PORT=8080 python app.py
```

---

## Automatisch starten als Linux service (systemd)

Zodat MediaManager automatisch start bij opstarten van de server/container.

### 1. Maak een service bestand aan

```bash
nano /etc/systemd/system/mediamanager.service
```

Plak de volgende inhoud (pas paden aan naar jouw situatie):

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

> **Let op:** Pas `WorkingDirectory` en het pad in `ExecStart` aan naar waar je `app.py` staat.  
> Gebruik `which python3` om het juiste Python pad te vinden.

### 2. Activeer en start de service

```bash
# Herlaad systemd zodat de nieuwe service wordt herkend
systemctl daemon-reload

# Zet de service aan bij opstarten
systemctl enable mediamanager

# Start de service nu direct
systemctl start mediamanager

# Check of alles draait
systemctl status mediamanager
```

### 3. Logs bekijken

```bash
# Live logs volgen
journalctl -u mediamanager -f

# Laatste 100 regels
journalctl -u mediamanager -n 100
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

Vul in Instellingen in:
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

## Optionele integraties

| Service | Functie |
|---------|---------|
| **Jellyfin** | Automatisch library refresh na download |
| **TMDB** | Metadata, posters en hernoemen op TMDB ID |
| **OpenSubtitles** | Automatisch ondertitels downloaden na verwerking |
