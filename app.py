from __future__ import annotations
import os, json, re, datetime, threading, uuid, time, subprocess
from typing import Any, Dict, Optional, List
from collections import defaultdict, OrderedDict
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from pathlib import Path
import requests as _requests

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("APP_SECRET", "mediamanager-secret-2026")

# Jinja2 filter voor bestandsnaam
app.jinja_env.filters['basename'] = lambda p: Path(p).name

BASE_DIR  = Path(__file__).parent
DATA_DIR  = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
CONF_FILE = DATA_DIR / "config.json"
CACHE_DIR = DATA_DIR / "cache"
for d in [DATA_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

DEFAULT_CONF: Dict[str, Any] = {
    "xtream":  {"server":"","port":0,"user":"","pwd":"","verify_tls":True,"timeout":15},
    "output":  {"base":"/media/library","live":"Live","movies":"Movies","series":"Series"},
    "ext":     {"live":"ts","movie":None,"episode":None},
    "cache":   {"ttl_hours":6},
    # storage_mode: "mount" | "smb" | "ftp"
    "storage": {
        "mode": "mount",
        "smb":  {"host":"","share":"","user":"","password":"","domain":"","films_path":"Films","series_path":"Series"},
        "ftp":  {"host":"","port":21,"user":"","password":"","films_path":"/media/Films","series_path":"/media/Series"},
    },
    "jellyfin":{"enabled":False,"url":"","api_key":"","films_library_id":"","series_library_id":""},
    "tmdb":    {"enabled":False,"api_key":""},
    "opensubtitles":{"enabled":False,"api_key":"","username":"","password":"","langs":["nl","en"]},
}

def load_conf() -> Dict[str, Any]:
    if CONF_FILE.exists():
        try:
            with open(CONF_FILE,"r",encoding="utf-8") as f:
                saved = json.load(f)
            return {k: ({**v,**(saved.get(k) or {})} if isinstance(v,dict) else saved.get(k,v))
                    for k,v in DEFAULT_CONF.items()}
        except Exception:
            pass
    return {k:(dict(v) if isinstance(v,dict) else v) for k,v in DEFAULT_CONF.items()}

def save_conf(cfg: Dict) -> None:
    CONF_FILE.parent.mkdir(parents=True,exist_ok=True)
    with open(CONF_FILE,"w",encoding="utf-8") as f:
        json.dump(cfg,f,indent=2,ensure_ascii=False)

def out_path(cfg: Dict, key: str) -> Path:
    return Path(cfg["output"]["base"]) / cfg["output"].get(key,key)

# ── Xtream ────────────────────────────────────────────────────────
def sanitize(name: str) -> str:
    if not isinstance(name,str): name = str(name or "")
    name = name.strip().replace(":"," -").replace("/"," ").replace("\\"," ")
    name = re.sub(r'[*?<>|"]+', "", name)
    return re.sub(r"\s+", " ", name).strip()[:240]

def pick_ext(candidate: Optional[str], fallback: str) -> str:
    if candidate:
        c = str(candidate).strip(".").lower()
        if re.fullmatch(r"[a-z0-9]{2,5}", c): return c
    return fallback

def write_strm(path: Path, url: str) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(url.strip() + "\n", encoding="utf-8")

class Xtream:
    def __init__(self, server, port, user, pwd, timeout=15, verify_tls=True):
        server = server.rstrip("/")
        if not server.startswith("http"): server = "http://" + server
        self.base = server if not port or str(port) in ["0","80","443"] else f"{server}:{port}"
        self.user=user; self.pwd=pwd; self.timeout=timeout; self.verify_tls=verify_tls

    def _url(self, **p) -> str:
        q = "&".join(f"{k}={v}" for k,v in p.items())
        return f"{self.base}/player_api.php?username={self.user}&password={self.pwd}&{q}"

    def _get(self, url: str) -> Any:
        r = _requests.get(url, timeout=self.timeout, verify=self.verify_tls,
                          headers={"User-Agent":"Mozilla/5.0","Accept":"*/*"})
        r.raise_for_status()
        return r.json()

    def get_user_info(self):
        d = self._get(self._url(action="get_user_info"))
        return (d.get("user_info") or {}) if isinstance(d,dict) else {}

    def get_live_streams(self):   return self._get(self._url(action="get_live_streams")) or []
    def get_vod_streams(self):    return self._get(self._url(action="get_vod_streams")) or []
    def get_series(self):         return self._get(self._url(action="get_series")) or []
    def get_series_info(self,sid): return self._get(self._url(action="get_series_info",series_id=sid)) or {}
    def live_url(self,sid,ext="ts"):   return f"{self.base}/live/{self.user}/{self.pwd}/{sid}.{pick_ext(ext,'ts')}"
    def vod_url(self,sid,ext="mp4"):   return f"{self.base}/movie/{self.user}/{self.pwd}/{sid}.{pick_ext(ext,'mp4')}"
    def episode_url(self,eid,ext="mp4"):return f"{self.base}/series/{self.user}/{self.pwd}/{eid}.{pick_ext(ext,'mp4')}"

def make_api(cfg: Dict) -> Xtream:
    x = cfg["xtream"]
    return Xtream(x["server"],x.get("port",0),x["user"],x["pwd"],int(x.get("timeout",15)),bool(x.get("verify_tls",True)))

# ── Cache ─────────────────────────────────────────────────────────
_LOCK = threading.Lock()

def _now_utc(): return datetime.datetime.now(datetime.timezone.utc)

def _cf(name): return CACHE_DIR / re.sub(r"[^a-zA-Z0-9_.:-]+","_",name)

def _rc(name):
    p = _cf(name)
    if not p.exists(): return None
    try:
        with open(p,"r",encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def _wc(name, payload):
    p = _cf(name)
    payload = dict(payload or {}); payload["fetched_at"] = _now_utc().isoformat()
    tmp = p.with_suffix(".tmp")
    with open(tmp,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    tmp.replace(p)

def _stale(doc, ttl):
    if not doc or "fetched_at" not in doc: return True
    try:
        t = datetime.datetime.fromisoformat(doc["fetched_at"])
        if not t.tzinfo: t = t.replace(tzinfo=datetime.timezone.utc)
        return (_now_utc()-t).total_seconds() >= ttl*3600
    except Exception: return True

def _gor(name, ttl, fn):
    with _LOCK:
        doc = _rc(name)
        if _stale(doc, ttl):
            fresh = fn()
            if not isinstance(fresh,dict): fresh={"items":fresh}
            _wc(name, fresh); return _rc(name) or fresh
        return doc

# ── TMDB ──────────────────────────────────────────────────────────
_TMDB_CACHE_FILE = DATA_DIR / ".tmdb_cache.json"
_tc: Dict = {}

def _ltc():
    global _tc
    try: _tc = json.loads(_TMDB_CACHE_FILE.read_text())
    except Exception: _tc = {}

def _stc():
    try: _TMDB_CACHE_FILE.write_text(json.dumps(_tc,ensure_ascii=False))
    except Exception: pass

_ltc()

def _tget(ep, params, key):
    if not key: return {}
    p = dict(params); p["api_key"]=key; p.setdefault("language","nl")
    try:
        r = _requests.get(f"https://api.themoviedb.org/3{ep}", params=p, timeout=5)
        return r.json() if r.ok else {}
    except Exception: return {}

def _ctitle(fn):
    name = Path(fn).stem
    name = re.sub(r'[Ss]\d{1,2}[Ee]\d{1,3}.*','',name)
    name = re.sub(r'\b(1080p|720p|4K|HDR|BluRay|WEBRip|HDTV|x264|x265|HEVC|REMUX|Remastered)\b.*','',name,flags=re.IGNORECASE)
    ym = re.search(r'\((\d{4})\)',name) or re.search(r'\.(\d{4})\.',name)
    year = ym.group(1) if ym else None
    name = re.sub(r'\(\d{4}\)','',name); name = re.sub(r'\.\d{4}\.','.',name)
    name = re.sub(r'[._]',' ',name).strip()
    return name.strip(), year

def tmdb_movie(fn, key):
    if not key: return {}
    t,y = _ctitle(fn); k=f"movie:{t}:{y}"
    if k in _tc: return _tc[k]
    p={"query":t}
    if y: p["year"]=y
    res = _tget("/search/movie",p,key).get("results",[])
    info={}
    if res:
        r=res[0]
        info={"title":r.get("title",t),"year":(r.get("release_date") or "")[:4],
              "overview":r.get("overview",""),"rating":round(r.get("vote_average",0),1),
              "poster":("https://image.tmdb.org/t/p/w342"+r["poster_path"]) if r.get("poster_path") else None}
    _tc[k]=info; _stc(); return info

def tmdb_series(name, key):
    if not key: return {}
    t,_ = _ctitle(name); k=f"tv:{t}"
    if k in _tc: return _tc[k]
    res = _tget("/search/tv",{"query":t},key).get("results",[])
    info={}
    if res:
        r=res[0]
        info={"title":r.get("name",t),"year":(r.get("first_air_date") or "")[:4],
              "overview":r.get("overview",""),"rating":round(r.get("vote_average",0),1),
              "poster":("https://image.tmdb.org/t/p/w342"+r["poster_path"]) if r.get("poster_path") else None}
    _tc[k]=info; _stc(); return info

def tmdb_by_id(tmdb_id, key):
    if not key or not tmdb_id: return None
    try:
        r = _requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                          params={"api_key":key,"language":"en-US"},timeout=8)
        if r.ok:
            d=r.json()
            return {"title":d.get("original_title") or d.get("title"),
                    "year":(d.get("release_date") or "")[:4]}
    except Exception: pass
    return None

# ── Jellyfin ──────────────────────────────────────────────────────
def jf_refresh(cfg, is_film):
    j=cfg.get("jellyfin",{})
    if not j.get("enabled") or not j.get("api_key") or not j.get("url"): return
    lid=j.get("films_library_id") if is_film else j.get("series_library_id")
    hdrs={"X-Emby-Token":j["api_key"],"Content-Type":"application/json"}
    url=(f'{j["url"]}/Items/{lid}/Refresh' if lid else f'{j["url"]}/Library/Refresh')
    params={"Recursive":"true","ImageRefreshMode":"Default","MetadataRefreshMode":"Default"} if lid else {}
    try: _requests.post(url,headers=hdrs,params=params,timeout=10)
    except Exception as e: print(f"[!] Jellyfin: {e}")

# ── OpenSubtitles ─────────────────────────────────────────────────
_os_token: Optional[str] = None

def os_login(cfg):
    global _os_token
    if _os_token: return _os_token
    o=cfg.get("opensubtitles",{})
    if not o.get("enabled") or not o.get("api_key"): return None
    try:
        r=_requests.post("https://api.opensubtitles.com/api/v1/login",
            headers={"Api-Key":o["api_key"],"Content-Type":"application/json","User-Agent":"MediaManager v1"},
            json={"username":o.get("username",""),"password":o.get("password","")},timeout=10)
        _os_token=r.json().get("token")
    except Exception: pass
    return _os_token

def os_download(title, lang, dest, cfg, season=None, episode=None):
    o=cfg.get("opensubtitles",{}); token=os_login(cfg)
    if not token: return False
    params={"query":title,"languages":lang,"order_by":"download_count","order_direction":"desc"}
    if season is not None: params["season_number"]=season
    if episode is not None: params["episode_number"]=episode
    try:
        r=_requests.get("https://api.opensubtitles.com/api/v1/subtitles",
            headers={"Api-Key":o["api_key"],"Authorization":f"Bearer {token}","User-Agent":"MediaManager v1"},
            params=params,timeout=10)
        results=r.json().get("data",[])
        if not results: return False
        fid=results[0].get("attributes",{}).get("files",[{}])[0].get("file_id")
        if not fid: return False
        dl=_requests.post("https://api.opensubtitles.com/api/v1/download",
            headers={"Api-Key":o["api_key"],"Authorization":f"Bearer {token}",
                     "Content-Type":"application/json","User-Agent":"MediaManager v1"},
            json={"file_id":fid},timeout=15)
        dl_url=dl.json().get("link")
        if not dl_url: return False
        Path(dest).parent.mkdir(parents=True,exist_ok=True)
        Path(dest).write_bytes(_requests.get(dl_url,timeout=30).content)
        return True
    except Exception as e:
        print(f"[!] OS sub ({lang}): {e}"); return False

# ── Postprocessing (rename/sort logica uit de bash scripts) ───────
_PFX_RE  = re.compile(
    r'^(?:'
    r'\|[A-Za-z]{2,4}\|\s*'           # |EN|, |NLD|
    r'|[A-Za-z]{2,4}\s*-\s*'          # EN -, NL -
    r'|[A-Za-z0-9+\-_]{2,12}\s*-\s*'  # 4K-, OSN+-, beQ-, etc.
    r'|4K\s*[-–]\s*'                   # 4K -
    r')*'
)
_SFX_RE  = re.compile(r'\s*\([A-Za-z]{2}\)$')

def strip_prefix(name: str) -> str:
    c = _PFX_RE.sub("", name).strip()
    c = _SFX_RE.sub("", c).strip()
    return c or name

def extract_tmdb_id(folder_name: str) -> Optional[str]:
    name = re.sub(r'[–—]','-', folder_name)
    for seg in re.split(r'\s*-\s*', name):
        seg = seg.strip()
        if re.fullmatch(r'\d{3,9}', seg):
            num = int(seg)
            if 1900 <= num <= 2050: continue
            if num in (2160,1080,720,480): continue
            return str(num)
    return None

def safe_fn(s: str) -> str:
    s = s.replace("/","-").replace(":"," -")
    s = re.sub(r'[<>|"?*\\]',"",s)
    s = re.sub(r'\s+'," ",s).strip()
    return re.sub(r'[\s.]+$',"",s)

def postprocess_movies(cfg: Dict) -> dict:
    results = {"renamed":[],"skipped":[],"errors":[]}
    tmdb_key = cfg["tmdb"]["api_key"] if cfg["tmdb"].get("enabled") else ""
    movies_dir = out_path(cfg,"movies")
    if not movies_dir.exists():
        results["errors"].append(f"Map niet gevonden: {movies_dir}"); return results

    for folder in sorted(movies_dir.iterdir()):
        if not folder.is_dir(): continue
        folder_name = folder.name
        new_title   = None

        tmdb_id = extract_tmdb_id(folder_name)
        if tmdb_id and tmdb_key:
            info = tmdb_by_id(tmdb_id, tmdb_key)
            if info and info.get("title"):
                new_title = safe_fn(info["title"])

        if not new_title:
            new_title = safe_fn(strip_prefix(folder_name))

        new_folder = movies_dir / new_title
        if folder != new_folder:
            if new_folder.exists():
                results["errors"].append(f"Doelmap bestaat al: {new_title}")
            else:
                try:
                    folder.rename(new_folder)
                    results["renamed"].append(f"{folder_name} → {new_title}")
                    folder = new_folder
                except Exception as e:
                    results["errors"].append(str(e)); continue
        else:
            results["skipped"].append(folder_name)

        # Hernoem .strm bestanden in de map
        strms = list(folder.glob("*.strm"))
        for i,strm in enumerate(strms):
            tname = f"{new_title}.strm" if len(strms)==1 else f"{new_title} ({i+1}).strm"
            tgt = folder / tname
            if strm != tgt and not tgt.exists():
                try: strm.rename(tgt)
                except Exception: pass

        # Hernoem overige bestanden (subs etc.)
        for f in folder.iterdir():
            if not f.is_file() or f.suffix == ".strm": continue
            cleaned = safe_fn(strip_prefix(f.name))
            if cleaned != f.name:
                tgt = folder / cleaned
                if not tgt.exists():
                    try: f.rename(tgt)
                    except Exception: pass

    return results

def postprocess_series(cfg: Dict) -> dict:
    results = {"renamed":[],"skipped":[],"errors":[]}
    series_dir = out_path(cfg,"series")
    if not series_dir.exists():
        results["errors"].append(f"Map niet gevonden: {series_dir}"); return results

    for folder in sorted(series_dir.iterdir()):
        if not folder.is_dir(): continue
        folder_name = folder.name
        # Mapnaam: strip prefix/suffix maar bewaar de seriename
        new_name   = safe_fn(strip_prefix(folder_name))
        # Verwijder jaar en landcode uit mapnaam
        new_name   = re.sub(r'\s*\(\d{4}\)', '', new_name).strip()
        new_name   = re.sub(r'\s*\([A-Z]{2}\)$', '', new_name).strip()
        new_name   = safe_fn(new_name) or folder_name
        new_folder = series_dir / new_name

        if folder != new_folder:
            if new_folder.exists():
                # Verplaats inhoud naar bestaande map ipv fout
                try:
                    for f in folder.iterdir():
                        tgt = new_folder / f.name
                        if not tgt.exists():
                            f.rename(tgt)
                    folder.rmdir()
                    results["renamed"].append(f"{folder_name} → {new_name} (samengevoegd)")
                except Exception as e:
                    results["errors"].append(f"Samenvoegen {folder_name}: {e}")
                folder = new_folder
            else:
                try:
                    folder.rename(new_folder)
                    results["renamed"].append(f"{folder_name} → {new_name}")
                    folder = new_folder
                except Exception as e:
                    results["errors"].append(str(e)); continue
        else:
            results["skipped"].append(folder_name)

        # Hernoem bestanden met volledige cleaning (_clean_strm_name)
        serie_clean = folder.name  # schone seriename na map-rename
        for f in sorted(folder.iterdir()):
            if not f.is_file(): continue
            if f.suffix == ".strm":
                # Gebruik _clean_strm_name voor volledige reiniging
                cleaned = _clean_strm_name(f.name)
                # Zorg dat bestandsnaam begint met seriename
                ep_match = re.search(r'[Ss]\d{2}[Ee]\d{2,3}', cleaned)
                if ep_match:
                    ep_code = ep_match.group(0).upper()
                    cleaned_name = f"{serie_clean} {ep_code}.strm"
                else:
                    cleaned_name = cleaned
            else:
                # Subs en andere bestanden: alleen prefix cleaning
                stem = f.stem; ext = f.suffix
                cleaned_stem = safe_fn(strip_prefix(stem))
                cleaned_stem = re.sub(r'\s*\(\d{4}\)', '', cleaned_stem).strip()
                cleaned_stem = re.sub(r'\s*\([A-Za-z]{2}\)$', '', cleaned_stem).strip()
                cleaned_name = safe_fn(cleaned_stem) + ext

            if cleaned_name != f.name:
                tgt = folder / cleaned_name
                if not tgt.exists():
                    try:
                        f.rename(tgt)
                    except Exception: pass

    return results

# ── Lokale scanner ────────────────────────────────────────────────
_S_FOLDER = re.compile(r'/(?:Season|Seizoen)[ _-]?(\d{1,2})/',re.IGNORECASE)
_S_CODE   = re.compile(r'[Ss](\d{1,2})[Ee]\d{1,3}')

def detect_season(path):
    for pat in [_S_FOLDER, _S_CODE]:
        m = pat.search(path)
        if m:
            try: return int(m.group(1))
            except ValueError: pass
    return "Specials"

def group_seasons(eps):
    b = defaultdict(list)
    for ep in eps: b[detect_season(ep)].append(ep)
    for k in b: b[k].sort()
    nums = sorted(k for k in b if isinstance(k,int))
    od = OrderedDict([(f"Seizoen {n}",b[n]) for n in nums])
    if "Specials" in b: od["Specials"] = b["Specials"]
    return od

def _extract_serie_name(filename: str) -> str:
    """Extraheer seriename uit een bestandsnaam zoals 'Game of Thrones S01E01.strm'."""
    stem = Path(filename).stem
    # Verwijder SxxExx en alles erna
    name = re.sub(r'\s*[Ss]\d{1,2}[Ee]\d{1,3}.*', '', stem).strip()
    return name or stem

def scan_local(cfg):
    base = Path(cfg["output"]["base"])
    mdir = base / cfg["output"]["movies"]
    sdir = base / cfg["output"]["series"]
    films: List[str] = []
    seen_films: set[str] = set()
    if mdir.exists():
        for root,_,files in os.walk(mdir):
            for f in files:
                if f.endswith(".strm"):
                    stem = Path(f).stem.lower()
                    if stem not in seen_films:
                        seen_films.add(stem)
                        films.append(os.path.join(root,f))
    series_dict: Dict[str,List] = {}
    if sdir.exists():
        for root,_,files in os.walk(sdir):
            for f in files:
                if f.endswith(".strm"):
                    rel = Path(root).relative_to(sdir).parts
                    if rel:
                        # Bestanden zitten in een submap → mapnaam is de seriename
                        sname = rel[0]
                    else:
                        # Bestanden zitten plat in de series map → extraheer naam uit bestandsnaam
                        sname = _extract_serie_name(f)
                    series_dict.setdefault(sname,[]).append(os.path.join(root,f))
    return films, series_dict

# ── Storage backend (mount / SMB / FTP) ──────────────────────────

def storage_mode(cfg: Dict) -> str:
    return cfg.get("storage", {}).get("mode", "mount")

def _smb_cfg(cfg): return cfg.get("storage",{}).get("smb",{})
def _ftp_cfg(cfg): return cfg.get("storage",{}).get("ftp",{})

def storage_write_file(local_path: str, remote_subpath: str, cfg: Dict) -> bool:
    """Schrijf een lokaal bestand naar de geconfigureerde storage backend.
       remote_subpath bijv. 'Films/Titelnaam/Titelnaam.mkv'
    """
    mode = storage_mode(cfg)
    if mode == "mount":
        # Al op de juiste plek, niets te doen
        return True
    elif mode == "smb":
        return _smb_put(local_path, remote_subpath, cfg)
    elif mode == "ftp":
        return _ftp_put(local_path, remote_subpath, cfg)
    return False

def storage_list_strm(cfg: Dict) -> tuple[List[str], Dict[str,List[str]]]:
    """Haal .strm bestanden op uit de geconfigureerde storage backend."""
    mode = storage_mode(cfg)
    if mode == "mount":
        return scan_local(cfg)
    elif mode == "smb":
        return _smb_list_strm(cfg)
    elif mode == "ftp":
        return _ftp_list_strm(cfg)
    return [], {}

def _clean_strm_name(filename: str) -> str:
    """
    Maak een bestandsnaam schoon:
    - Strip prefixen zoals '4K-OSN+ - ', '|EN| ', 'EN - '
    - Strip jaar  bijv. ' (2011)'
    - Strip land  bijv. ' (US)'
    - Behoudt SxxExx patroon
    - Behoudt extensie
    Voorbeeld: '4K-OSN+ - Game of Thrones (2011) (US) S01E01.strm'
            → 'Game of Thrones S01E01.strm'
    """
    stem = Path(filename).stem
    ext  = Path(filename).suffix

    # Extraheer SxxExx code als die er in zit
    ep_match = re.search(r'[Ss](\d{1,2})[Ee](\d{1,3})', stem)
    ep_code  = ep_match.group(0).upper() if ep_match else None

    # Verwijder alles vóór het eerste echte titelwoord (prefix cleaning)
    cleaned = strip_prefix(stem)

    # Verwijder jaar (2011), (2023) etc.
    cleaned = re.sub(r'\s*\(\d{4}\)', '', cleaned).strip()

    # Verwijder landcode (US), (NL), (JP) etc. aan het einde of voor SxxExx
    cleaned = re.sub(r'\s*\([A-Z]{2}\)', '', cleaned).strip()

    # Verwijder kwaliteitslabels
    cleaned = re.sub(
        r'\s*\b(4K|UHD|1080p|720p|HDR|SDR|BluRay|WEBRip|HDTV|x264|x265|HEVC|REMUX)\b.*',
        '', cleaned, flags=re.IGNORECASE
    ).strip()

    # Als er een episode code was, zorg dat die correct achteraan staat
    if ep_code:
        # Haal eventuele bestaande SxxExx uit de naam
        cleaned = re.sub(r'\s*[Ss]\d{1,2}[Ee]\d{1,3}.*', '', cleaned).strip()
        cleaned = f"{cleaned} {ep_code}"

    cleaned = safe_fn(cleaned)
    return cleaned + ext if cleaned else filename


def storage_write_strm(remote_subpath: str, url: str, cfg: Dict,
                       auto_postprocess: bool = True) -> str:
    """
    Schrijf een .strm bestand naar de geconfigureerde storage backend.
    Voert automatisch postprocessing uit na het schrijven.
    """
    mode    = storage_mode(cfg)
    is_film = cfg["output"]["movies"].lower() in remote_subpath.lower()

    if mode == "mount":
        dest = Path(cfg["output"]["base"]) / remote_subpath
        write_strm(dest, url)
        result = str(dest)
    else:
        # SMB / FTP: schrijf tijdelijk lokaal, upload dan
        tmp = Path(DATA_DIR) / "tmp_strm" / remote_subpath.replace("/","_").replace("\\","_")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        write_strm(tmp, url)
        try:
            ok = storage_write_file(str(tmp), remote_subpath, cfg)
            if not ok:
                raise RuntimeError(f"Upload mislukt naar {mode.upper()}: {remote_subpath}")
        finally:
            try: tmp.unlink()
            except Exception: pass
        result = remote_subpath

    # Automatisch postprocessen na aanmaken
    if auto_postprocess:
        try:
            if is_film: postprocess_movies(cfg)
            else:       postprocess_series(cfg)
        except Exception as e:
            print(f"[!] Auto-postprocess fout: {e}")

    return result

def storage_ensure_dirs(cfg: Dict):
    """Zorg dat output mappen bestaan (alleen relevant voor mount mode)."""
    if storage_mode(cfg) == "mount":
        for key in ("live","movies","series"):
            out_path(cfg, key).mkdir(parents=True, exist_ok=True)

# ── SMB helpers ───────────────────────────────────────────────────
def _smb_connect(cfg: Dict):
    """Maak SMB verbinding. Geeft (SMBConnection, server_name) terug."""
    try:
        from smb.SMBConnection import SMBConnection
    except ImportError:
        raise RuntimeError("pysmb niet geïnstalleerd. Voer uit: pip install pysmb")
    s = _smb_cfg(cfg)
    conn = SMBConnection(
        s.get("user",""), s.get("password",""),
        "mediamanager", s.get("host",""),
        domain=s.get("domain",""),
        use_ntlm_v2=True, is_direct_tcp=True,
    )
    connected = conn.connect(s["host"], 445)
    if not connected:
        raise RuntimeError(f"SMB verbinding mislukt naar {s['host']}")
    return conn, s.get("share","")

def _smb_put(local_path: str, remote_subpath: str, cfg: Dict) -> bool:
    try:
        conn, share = _smb_connect(cfg)
        remote = remote_subpath.replace("\\","/")
        # Maak mappen aan
        parts = remote.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            try: conn.createDirectory(share, d)
            except Exception: pass
        with open(local_path, "rb") as f:
            conn.storeFile(share, remote, f)
        conn.close()
        return True
    except Exception as e:
        print(f"[!] SMB upload fout: {e}"); return False

def _smb_list_strm(cfg: Dict) -> tuple[List[str], Dict[str,List[str]]]:
    """Haal .strm bestanden op via SMB — wist cache eerst zodat verwijderde items verdwijnen."""
    try:
        conn, share = _smb_connect(cfg)
        s        = _smb_cfg(cfg)
        tmp_base = Path(DATA_DIR) / "smb_cache"

        # ── Wis de cache volledig voor een frisse sync ──
        import shutil
        if tmp_base.exists():
            shutil.rmtree(tmp_base)
        tmp_base.mkdir(parents=True, exist_ok=True)

        for kind in ("films", "series"):
            remote_base = s.get(f"{kind}_path", kind.capitalize())
            local_base  = tmp_base / kind.capitalize()
            local_base.mkdir(parents=True, exist_ok=True)
            _smb_mirror(conn, share, remote_base, local_base)

        conn.close()

    except Exception as e:
        print(f"[!] SMB list fout: {e}")
        # Val terug op bestaande cache als verbinding mislukt
        tmp_base = Path(DATA_DIR) / "smb_cache"

    films: List[str] = []
    series_dict: Dict[str, List[str]] = {}

    # Films: dedupliceer op bestandsnaam (stem) zodat dubbelen niet getoond worden
    seen_films: set[str] = set()
    for root, _, files in os.walk(tmp_base / "Films"):
        for f in files:
            if f.endswith(".strm"):
                stem = Path(f).stem.lower()
                if stem not in seen_films:
                    seen_films.add(stem)
                    films.append(os.path.join(root, f))

    # Series
    for root, _, files in os.walk(tmp_base / "Series"):
        for f in files:
            if f.endswith(".strm"):
                rel   = Path(root).relative_to(tmp_base / "Series").parts
                sname = rel[0] if rel else _extract_serie_name(f)
                series_dict.setdefault(sname, []).append(os.path.join(root, f))

    return films, series_dict

def _smb_mirror(conn, share, remote_dir, local_dir):
    """Spiegel remote SMB map naar lokale map (alleen .strm), met cleaning van namen."""
    try:
        items = conn.listPath(share, remote_dir)
        for item in items:
            if item.filename in (".",".."): continue
            remote_path = f"{remote_dir}/{item.filename}"

            if item.isDirectory:
                # Clean de mapnaam direct bij het spiegelen
                clean_dir = safe_fn(strip_prefix(item.filename))
                local_path = local_dir / clean_dir
                local_path.mkdir(parents=True, exist_ok=True)
                _smb_mirror(conn, share, remote_path, local_path)
            elif item.filename.endswith(".strm"):
                # Clean de bestandsnaam
                clean_name = _clean_strm_name(item.filename)
                local_path = local_dir / clean_name
                with open(local_path, "wb") as f:
                    conn.retrieveFile(share, remote_path, f)
    except Exception as e:
        print(f"[!] SMB mirror fout ({remote_dir}): {e}")

def smb_test(cfg: Dict) -> dict:
    try:
        conn, share = _smb_connect(cfg)
        shares = [s.name for s in conn.listShares()]
        conn.close()
        return {"ok": True, "shares": shares}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── FTP helpers ───────────────────────────────────────────────────
def _ftp_connect(cfg: Dict):
    import ftplib
    f = _ftp_cfg(cfg)
    ftp = ftplib.FTP()
    ftp.connect(f.get("host",""), int(f.get("port",21)), timeout=15)
    ftp.login(f.get("user",""), f.get("password",""))
    ftp.set_pasv(True)
    return ftp

def _ftp_mkdirs(ftp, remote_path: str):
    import ftplib
    parts = [p for p in remote_path.replace("\\","/").split("/") if p]
    current = ""
    for part in parts:
        current = f"{current}/{part}"
        try: ftp.mkd(current)
        except ftplib.error_perm: pass

def _ftp_put(local_path: str, remote_subpath: str, cfg: Dict) -> bool:
    try:
        ftp = _ftp_connect(cfg)
        remote = remote_subpath.replace("\\","/")
        remote_dir = "/".join(remote.split("/")[:-1])
        if remote_dir: _ftp_mkdirs(ftp, remote_dir)
        with open(local_path, "rb") as f:
            ftp.storbinary(f"STOR {remote}", f)
        ftp.quit()
        return True
    except Exception as e:
        print(f"[!] FTP upload fout: {e}"); return False

def _ftp_list_strm(cfg: Dict) -> tuple[List[str], Dict[str,List[str]]]:
    import shutil
    tmp = Path(DATA_DIR) / "ftp_cache"
    try:
        ftp = _ftp_connect(cfg)
        fc  = _ftp_cfg(cfg)
        # Wis cache voor frisse sync
        if tmp.exists(): shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)
        _ftp_mirror(ftp, fc.get("films_path","/media/Films"),  tmp / "Films")
        _ftp_mirror(ftp, fc.get("series_path","/media/Series"), tmp / "Series")
        ftp.quit()
    except Exception as e:
        print(f"[!] FTP list fout: {e}")
        tmp.mkdir(parents=True, exist_ok=True)

    films: List[str] = []
    series_dict: Dict[str, List[str]] = {}

    seen_films: set[str] = set()
    for root,_,files in os.walk(tmp/"Films"):
        for f in files:
            if f.endswith(".strm"):
                stem = Path(f).stem.lower()
                if stem not in seen_films:
                    seen_films.add(stem)
                    films.append(os.path.join(root,f))

    for root,_,files in os.walk(tmp/"Series"):
        for f in files:
            if f.endswith(".strm"):
                rel   = Path(root).relative_to(tmp/"Series").parts
                sname = rel[0] if rel else _extract_serie_name(f)
                series_dict.setdefault(sname,[]).append(os.path.join(root,f))
    return films, series_dict

def _ftp_mirror(ftp, remote_dir: str, local_dir: Path):
    """Spiegel remote FTP map naar lokale map (alleen .strm)."""
    import ftplib
    local_dir.mkdir(parents=True, exist_ok=True)
    try:
        items = []
        ftp.retrlines(f"LIST {remote_dir}", items.append)
        for line in items:
            parts = line.split(None, 8)
            if len(parts) < 9: continue
            name      = parts[8]
            is_dir    = line.startswith("d")
            remote_fp = f"{remote_dir}/{name}"
            local_fp  = local_dir / name
            if is_dir:
                _ftp_mirror(ftp, remote_fp, local_fp)
            elif name.endswith(".strm"):
                with open(local_fp, "wb") as f:
                    ftp.retrbinary(f"RETR {remote_fp}", f.write)
    except Exception as e:
        print(f"[!] FTP mirror fout ({remote_dir}): {e}")

def ftp_test(cfg: Dict) -> dict:
    try:
        ftp = _ftp_connect(cfg)
        welcome = ftp.getwelcome()
        ftp.quit()
        return {"ok": True, "welcome": welcome}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Download Queue ────────────────────────────────────────────────
class DownloadQueue:
    MAX_RETRIES = 3

    def __init__(self):
        self._lock=threading.Lock(); self.queue=[]; self.history=[]; self.current=None
        self._wt: Optional[threading.Thread]=None

    def _entry(self, fp):
        return {"id":str(uuid.uuid4()),"file_path":fp,"name":Path(fp).stem,"status":"queued",
                "progress":0,"speed":"","eta":"","attempt":0,
                "added_at":datetime.datetime.now().isoformat(timespec="seconds"),
                "finished_at":None,"error":None}

    def add(self, fps):
        with self._lock:
            for fp in fps: self.queue.append(self._entry(fp))
        self._ew()

    def get_state(self):
        with self._lock:
            return {"current":dict(self.current) if self.current else None,
                    "queue":[dict(e) for e in self.queue],
                    "history":[dict(e) for e in self.history]}

    def delete_history(self,iid):
        with self._lock: self.history=[e for e in self.history if e["id"]!=iid]

    def clear_history(self):
        with self._lock: self.history.clear()

    def retry(self,iid):
        with self._lock:
            it=next((e for e in self.history if e["id"]==iid),None)
            if it and it["status"]=="failed":
                self.history.remove(it)
                it.update({"status":"queued","progress":0,"attempt":0,"error":None,"finished_at":None})
                self.queue.append(it)
        self._ew()

    def _ew(self):
        if self._wt is None or not self._wt.is_alive():
            self._wt=threading.Thread(target=self._worker,daemon=True)
            self._wt.start()

    def _worker(self):
        while True:
            with self._lock:
                if not self.queue: self.current=None; return
                e=self.queue.pop(0); self.current=e
            ok=self._process(e)
            with self._lock:
                e["finished_at"]=datetime.datetime.now().isoformat(timespec="seconds")
                e["status"]="done" if ok else "failed"
                if ok: e["progress"]=100
                self.history.insert(0,e); self.current=None

    def _process(self, e):
        cfg=load_conf()
        fp=Path(e["file_path"])
        is_film = "Movies" in str(fp) or "Films" in str(fp)
        mode = storage_mode(cfg)

        # Tijdelijke lokale werkmap
        tmp_dir = Path(DATA_DIR) / "tmp_work"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out_file = str(tmp_dir / (fp.stem + ".mkv"))

        # Bij mount mode: schrijf direct naar output map
        if mode == "mount":
            od = out_path(cfg,"movies") if is_film else out_path(cfg,"series")
            od.mkdir(parents=True,exist_ok=True)
            out_file = str(od / (fp.stem + ".mkv"))

        try:
            try: url=fp.read_text().strip().splitlines()[0].strip()
            except Exception as ex: e["error"]=f"Kan .strm niet lezen: {ex}"; return False
            if not url.startswith("http"): e["error"]="Ongeldige URL"; return False

            ok=False
            for attempt in range(1,self.MAX_RETRIES+1):
                e.update({"attempt":attempt,"status":"downloading","progress":0,"speed":"","eta":"","error":None})
                ok=self._ytdlp(url,out_file,e)
                if ok: break
                e["error"]=f"Poging {attempt} mislukt"
                if attempt<self.MAX_RETRIES: time.sleep(3)
            if not ok: return False

            # Upload naar SMB/FTP indien niet mount mode
            if mode in ("smb","ftp"):
                e["status"] = f"uploaden via {mode.upper()}..."
                subdir = cfg["output"]["movies"] if is_film else cfg["output"]["series"]
                remote_subpath = f"{subdir}/{fp.stem}/{fp.stem}.mkv"
                upload_ok = storage_write_file(out_file, remote_subpath, cfg)
                if not upload_ok:
                    e["error"] = f"{mode.upper()} upload mislukt"; return False

            # Ondertitels
            oc=cfg.get("opensubtitles",{})
            if oc.get("enabled") and oc.get("api_key"):
                e["status"]="ondertitels ophalen..."
                tk=cfg["tmdb"]["api_key"] if cfg["tmdb"].get("enabled") else ""
                if is_film:
                    info=tmdb_movie(fp.name,tk); title=info.get("title") or fp.stem
                    season=episode=None
                else:
                    sn=fp.parent.name; info=tmdb_series(sn,tk); title=info.get("title") or sn
                    m=re.search(r'[Ss](\d+)[Ee](\d+)',fp.stem)
                    season=int(m.group(1)) if m else None; episode=int(m.group(2)) if m else None
                for lang in (oc.get("langs") or ["nl","en"]):
                    time.sleep(1)
                    dest=out_file.replace(".mkv",f".{lang}.srt")
                    if os_download(title,lang,dest,cfg,season,episode) and mode in ("smb","ftp"):
                        subdir = cfg["output"]["movies"] if is_film else cfg["output"]["series"]
                        storage_write_file(dest, f"{subdir}/{fp.stem}/{fp.stem}.{lang}.srt", cfg)

            jf_refresh(cfg,is_film)

            # Postprocessing (alleen bij mount mode)
            if mode == "mount":
                e["status"]="postprocessing..."
                if is_film: postprocess_movies(cfg)
                else: postprocess_series(cfg)

            # Ruim tijdelijke bestanden op bij SMB/FTP mode
            if mode in ("smb","ftp"):
                for f_tmp in tmp_dir.glob(fp.stem + ".*"):
                    try: f_tmp.unlink()
                    except Exception: pass

            return True
        except Exception as ex:
            e["error"]=str(ex); return False

    def _ytdlp(self, url, output, e):
        cmd=["yt-dlp","--no-playlist","--no-warnings","--newline","--progress",
             "--progress-template","%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
             "-o",output,url]
        try:
            proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
            for line in proc.stdout:
                line=line.strip()
                if "|" in line:
                    parts=line.split("|")
                    if len(parts)>=3:
                        try: e["progress"]=float(parts[0].strip().replace("%",""))
                        except ValueError: pass
                        e["speed"]=parts[1].strip(); e["eta"]=parts[2].strip()
            proc.wait(); return proc.returncode==0
        except Exception as ex:
            e["error"]=str(ex); return False

download_queue = DownloadQueue()

# ══════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════

@app.get("/")
def index():
    cfg=load_conf()
    x=cfg.get("xtream",{})
    if not x.get("user") or not x.get("server"):
        return redirect(url_for("settings"))
    return redirect(url_for("browse"))

@app.route("/settings", methods=["GET","POST"])
def settings():
    cfg=load_conf()
    if request.method=="POST":
        sec=request.form.get("section","")
        if sec=="xtream":
            rp=request.form.get("port","").strip()
            cfg["xtream"].update({"server":request.form.get("server","").strip(),
                "port":int(rp) if rp and rp.isdigit() else 0,
                "user":request.form.get("user","").strip(),
                "pwd":request.form.get("pwd","").strip(),
                "verify_tls":request.form.get("verify_tls")=="on",
                "timeout":int(request.form.get("timeout") or 15)})
        elif sec=="output":
            cfg["output"].update({"base":request.form.get("base","").strip() or "/media/library",
                "live":request.form.get("live","").strip() or "Live",
                "movies":request.form.get("movies","").strip() or "Movies",
                "series":request.form.get("series","").strip() or "Series"})
            cfg["ext"].update({"live":(request.form.get("ext_live") or "ts").strip(),
                "movie":(request.form.get("ext_movie") or "").strip() or None,
                "episode":(request.form.get("ext_episode") or "").strip() or None})
            cfg["cache"]["ttl_hours"]=max(0,int(request.form.get("ttl_hours") or 6))
        elif sec=="storage":
            mode = request.form.get("storage_mode","mount")
            cfg["storage"]["mode"] = mode
            # Mount paden opslaan
            if request.form.get("base","").strip():
                cfg["output"]["base"]   = request.form.get("base","").strip()
            if request.form.get("live","").strip():
                cfg["output"]["live"]   = request.form.get("live","").strip()
            if request.form.get("movies","").strip():
                cfg["output"]["movies"] = request.form.get("movies","").strip()
            if request.form.get("series","").strip():
                cfg["output"]["series"] = request.form.get("series","").strip()
            # Extensies & cache
            cfg["ext"].update({
                "live":    (request.form.get("ext_live") or "ts").strip(),
                "movie":   (request.form.get("ext_movie") or "").strip() or None,
                "episode": (request.form.get("ext_episode") or "").strip() or None,
            })
            cfg["cache"]["ttl_hours"] = max(0, int(request.form.get("ttl_hours") or 6))
            # SMB
            cfg["storage"]["smb"].update({
                "host":        request.form.get("smb_host","").strip(),
                "share":       request.form.get("smb_share","").strip(),
                "user":        request.form.get("smb_user","").strip(),
                "password":    request.form.get("smb_password","").strip(),
                "domain":      request.form.get("smb_domain","").strip(),
                "films_path":  request.form.get("smb_films_path","Films").strip(),
                "series_path": request.form.get("smb_series_path","Series").strip(),
            })
            # FTP
            rp = request.form.get("ftp_port","21").strip()
            cfg["storage"]["ftp"].update({
                "host":         request.form.get("ftp_host","").strip(),
                "port":         int(rp) if rp.isdigit() else 21,
                "user":         request.form.get("ftp_user","").strip(),
                "password":     request.form.get("ftp_password","").strip(),
                "films_path":   request.form.get("ftp_films_path","/media/Films").strip(),
                "series_path":  request.form.get("ftp_series_path","/media/Series").strip(),
            })
        elif sec=="jellyfin":
            cfg["jellyfin"].update({"enabled":request.form.get("jf_enabled")=="on",
                "url":request.form.get("jf_url","").strip(),
                "api_key":request.form.get("jf_api_key","").strip(),
                "films_library_id":request.form.get("jf_films_lib","").strip(),
                "series_library_id":request.form.get("jf_series_lib","").strip()})
        elif sec=="tmdb":
            cfg["tmdb"].update({"enabled":request.form.get("tmdb_enabled")=="on",
                "api_key":request.form.get("tmdb_api_key","").strip()})
            _ltc()
        elif sec=="opensubtitles":
            langs=[l.strip() for l in re.split(r'[,;]',request.form.get("os_langs","nl,en")) if l.strip()]
            cfg["opensubtitles"].update({"enabled":request.form.get("os_enabled")=="on",
                "api_key":request.form.get("os_api_key","").strip(),
                "username":request.form.get("os_username","").strip(),
                "password":request.form.get("os_password","").strip(),
                "langs":langs})
            global _os_token; _os_token=None
        save_conf(cfg)
        flash("Instellingen opgeslagen.","success")
        return redirect(url_for("settings"))

    account_info=None
    x=cfg.get("xtream",{})
    if x.get("server") and x.get("user") and x.get("pwd"):
        try: account_info=make_api(cfg).get_user_info()
        except Exception: pass
    return render_template("settings.html",cfg=cfg,account_info=account_info)

@app.get("/browse")
def browse():
    return render_template("browse.html",cfg=load_conf())

@app.route("/library", methods=["GET","POST"])
def library():
    cfg = load_conf()
    if request.method == "POST":
        sel = request.form.getlist("selected_files")
        if sel:
            download_queue.add(sel)
            flash(f"⏳ {len(sel)} bestand(en) toegevoegd aan de download queue.", "success")
        else:
            flash("⚠️ Geen bestanden geselecteerd.", "warning")
        return redirect(url_for("library"))

    films_raw, series_raw = storage_list_strm(cfg)
    tk = cfg["tmdb"]["api_key"] if cfg["tmdb"].get("enabled") else ""

    films  = [{"path": f, "info": tmdb_movie(f, tk) if tk else {}} for f in films_raw]
    series = {sn: {"info": tmdb_series(sn, tk) if tk else {}, "seasons": group_seasons(eps)}
              for sn, eps in series_raw.items()}

    return render_template("library.html", films=films, series=series)

@app.get("/queue")
def queue_page():
    return render_template("queue.html")

# ── API: Xtream ───────────────────────────────────────────────────
def _sf(q):
    s=(q or "").strip().lower()
    return s in ("*","all") or len(s)>=2

@app.get("/api/live")
def api_live():
    cfg=load_conf(); q=(request.args.get("q") or "").strip()
    try:
        if not _sf(q): return jsonify({"ok":True,"items":[],"cached_at":None,"hint":"Geef ≥2 tekens of '*'."})
        doc=_gor("live.json",int(cfg["cache"]["ttl_hours"]),lambda:{"items":make_api(cfg).get_live_streams()})
        items=doc.get("items",[]); qn=q.lower()
        if qn not in ("*","all"): items=[x for x in items if qn in (x.get("name") or "").lower()]
        return jsonify({"ok":True,"items":items,"cached_at":doc.get("fetched_at")})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/movies")
def api_movies():
    cfg=load_conf(); q=(request.args.get("q") or "").strip()
    try:
        if not _sf(q): return jsonify({"ok":True,"items":[],"cached_at":None,"hint":"Geef ≥2 tekens of '*'."})
        doc=_gor("movies.json",int(cfg["cache"]["ttl_hours"]),lambda:{"items":make_api(cfg).get_vod_streams()})
        items=doc.get("items",[]); qn=q.lower()
        if qn not in ("*","all"): items=[x for x in items if qn in (x.get("name") or "").lower()]
        return jsonify({"ok":True,"items":items,"cached_at":doc.get("fetched_at")})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/series")
def api_series():
    cfg=load_conf(); q=(request.args.get("q") or "").strip()
    try:
        if not _sf(q): return jsonify({"ok":True,"items":[],"cached_at":None,"hint":"Geef ≥2 tekens of '*'."})
        doc=_gor("series.json",int(cfg["cache"]["ttl_hours"]),lambda:{"items":make_api(cfg).get_series()})
        items=doc.get("items",[]); qn=q.lower()
        if qn not in ("*","all"): items=[x for x in items if qn in (x.get("name") or "").lower()]
        return jsonify({"ok":True,"items":items,"cached_at":doc.get("fetched_at")})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/series/<series_id>")
def api_series_detail(series_id):
    cfg=load_conf()
    try:
        doc=_gor(f"series_{series_id}.json",int(cfg["cache"]["ttl_hours"]),
                 lambda:{"data":make_api(cfg).get_series_info(series_id)})
        return jsonify({"ok":True,"data":doc.get("data"),"cached_at":doc.get("fetched_at")})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.post("/api/refresh")
def api_refresh():
    cfg=load_conf(); scope=request.args.get("scope","all")
    try:
        api=make_api(cfg)
        with _LOCK:
            if scope in ("all","live"):   _wc("live.json",{"items":api.get_live_streams()})
            if scope in ("all","movies"): _wc("movies.json",{"items":api.get_vod_streams()})
            if scope in ("all","series"): _wc("series.json",{"items":api.get_series()})
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.post("/api/strm/live")
def api_strm_live():
    cfg   = load_conf()
    p     = request.get_json(force=True) or {}
    sid   = p.get("stream_id")
    title = p.get("title") or f"Live {sid}"
    ext   = pick_ext(p.get("ext"), cfg["ext"]["live"] or "ts")
    url   = make_api(cfg).live_url(sid, ext)
    fname = sanitize(title) + ".strm"
    remote = f"{cfg['output']['live']}/{fname}"
    try:
        path = storage_write_strm(remote, url, cfg)
        return jsonify({"ok": True, "path": path, "url": url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/strm/movie")
def api_strm_movie():
    cfg = load_conf()
    p   = request.get_json(force=True) or {}
    sid = p.get("stream_id")
    if not sid:
        return jsonify({"ok": False, "error": "Geen stream_id"}), 400

    # Lookup in cache voor tmdb_id en naam
    tmdb_id = title_raw = None
    cf = _cf("movies.json")
    if cf.exists():
        try:
            for it in (json.loads(cf.read_text()).get("items") or []):
                if str(it.get("stream_id")) == str(sid):
                    tmdb_id   = (it.get("tmdb") or "").strip()
                    title_raw = it.get("name")
                    break
        except Exception:
            pass
    if not title_raw:
        title_raw = p.get("title") or f"Movie {sid}"

    ext  = pick_ext(p.get("ext"), cfg["ext"]["movie"] or "mp4")
    url  = make_api(cfg).vod_url(sid, ext)
    sb   = sanitize(f"{tmdb_id} - {sanitize(title_raw)}") if tmdb_id else sanitize(title_raw)
    remote = f"{cfg['output']['movies']}/{sb}/{sb}.strm"
    try:
        path = storage_write_strm(remote, url, cfg)
        return jsonify({"ok": True, "path": path, "tmdb": tmdb_id or None, "url": url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/strm/series")
def api_strm_series():
    cfg       = load_conf()
    p         = request.get_json(force=True) or {}
    sid       = p.get("series_id")
    forced_ext= p.get("ext") or cfg["ext"]["episode"]
    try:
        api   = make_api(cfg)
        data  = api.get_series_info(sid)
        sname = sanitize(
            (data.get("info") or {}).get("name") or
            (data.get("info") or {}).get("title") or f"Series {sid}"
        )
        eps = data.get("episodes") or {}
        if not eps:
            return jsonify({"ok": False, "error": "Geen afleveringen"}), 404

        created = 0
        errors  = []
        for sk, eplist in sorted(eps.items(),
                                  key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
            try: sn = int(sk)
            except ValueError: sn = 0
            for ep in (eplist or []):
                en  = ep.get("episode_num") or ep.get("episode") or 0
                try: en = int(en)
                except Exception: en = 0
                eid    = ep.get("id") or ep.get("episode_id") or ep.get("stream_id")
                ext    = pick_ext(ep.get("container_extension") or forced_ext, "mp4")
                url    = api.episode_url(eid, ext)
                fname  = f"{sname} S{sn:02d}E{en:02d}.strm"
                remote = f"{cfg['output']['series']}/{sname}/{fname}"
                try:
                    storage_write_strm(remote, url, cfg)
                    created += 1
                except Exception as e:
                    errors.append(str(e))

        return jsonify({"ok": True, "count": created, "errors": errors,
                        "path": f"{cfg['output']['series']}/{sname}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/strm/batch")
def api_strm_batch():
    """Maak .strm bestanden voor een lijst van stream IDs (films of series)."""
    cfg     = load_conf()
    payload = request.get_json(force=True) or {}
    kind    = payload.get("kind", "movie")   # "movie" | "series"
    ids     = payload.get("ids", [])
    if not ids:
        return jsonify({"ok": False, "error": "Geen IDs opgegeven"}), 400

    created = 0
    errors  = []
    api     = make_api(cfg)

    # Laad cache voor tmdb lookups
    cf = _cf("movies.json")
    movie_cache = {}
    if cf.exists():
        try:
            for it in (json.loads(cf.read_text()).get("items") or []):
                movie_cache[str(it.get("stream_id"))] = it
        except Exception:
            pass

    for sid in ids:
        try:
            sid = str(sid)
            if kind == "movie":
                it        = movie_cache.get(sid, {})
                title_raw = it.get("name") or f"Movie {sid}"
                tmdb_id   = (it.get("tmdb") or "").strip()
                ext       = pick_ext(it.get("container_extension"), cfg["ext"]["movie"] or "mp4")
                url       = api.vod_url(sid, ext)
                sb        = sanitize(f"{tmdb_id} - {sanitize(title_raw)}") if tmdb_id else sanitize(title_raw)
                remote    = f"{cfg['output']['movies']}/{sb}/{sb}.strm"
                storage_write_strm(remote, url, cfg)
                created += 1
            elif kind == "series":
                data  = api.get_series_info(sid)
                sname = sanitize(
                    (data.get("info") or {}).get("name") or
                    (data.get("info") or {}).get("title") or f"Series {sid}"
                )
                eps_by_season = data.get("episodes") or {}
                if not eps_by_season:
                    errors.append(f"Geen afleveringen voor serie {sid}")
                    continue
                for sk, eplist in sorted(eps_by_season.items(),
                                         key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
                    try: sn = int(sk)
                    except ValueError: sn = 0
                    for ep in (eplist or []):
                        en  = ep.get("episode_num") or ep.get("episode") or 0
                        try: en = int(en)
                        except Exception: en = 0
                        eid    = ep.get("id") or ep.get("episode_id") or ep.get("stream_id")
                        ext    = pick_ext(ep.get("container_extension") or cfg["ext"]["episode"], "mp4")
                        url    = api.episode_url(eid, ext)
                        fname  = f"{sname} S{sn:02d}E{en:02d}.strm"
                        remote = f"{cfg['output']['series']}/{sname}/{fname}"
                        storage_write_strm(remote, url, cfg)
                        created += 1
        except Exception as e:
            errors.append(f"{sid}: {e}")

    return jsonify({"ok": True, "created": created, "errors": errors})

@app.post("/api/library/refresh")
def api_library_refresh():
    """Wis de SMB/FTP cache zodat de bibliotheek bij de volgende pageload vers ophaalt."""
    import shutil
    cleared = []
    for name in ("smb_cache", "ftp_cache"):
        p = Path(DATA_DIR) / name
        if p.exists():
            shutil.rmtree(p)
            cleared.append(name)
    return jsonify({"ok": True, "cleared": cleared})

# ── API: Queue ────────────────────────────────────────────────────
@app.get("/api/queue")
def api_queue(): return jsonify(download_queue.get_state())

@app.delete("/api/queue/history/<iid>")
def api_del_hist(iid): download_queue.delete_history(iid); return jsonify({"ok":True})

@app.delete("/api/queue/history")
def api_clr_hist(): download_queue.clear_history(); return jsonify({"ok":True})

@app.post("/api/queue/retry/<iid>")
def api_retry(iid): download_queue.retry(iid); return jsonify({"ok":True})

# ── API: Postprocessing ───────────────────────────────────────────
@app.post("/api/postprocess")
def api_postprocess():
    cfg=load_conf(); scope=(request.get_json(force=True) or {}).get("scope","all")
    res={}
    if scope in ("all","movies"): res["movies"]=postprocess_movies(cfg)
    if scope in ("all","series"): res["series"]=postprocess_series(cfg)
    return jsonify({"ok":True,"results":res})

# ── API: Test verbindingen ────────────────────────────────────────
@app.post("/api/test/xtream")
def api_test_xtream():
    try:
        info=make_api(load_conf()).get_user_info()
        return jsonify({"ok":True,"info":info})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.post("/api/test/jellyfin")
def api_test_jellyfin():
    j=load_conf().get("jellyfin",{})
    if not j.get("url") or not j.get("api_key"):
        return jsonify({"ok":False,"error":"Niet geconfigureerd"})
    try:
        r=_requests.get(f'{j["url"]}/System/Info/Public',timeout=5)
        if r.ok: return jsonify({"ok":True,"version":r.json().get("Version","?")})
        return jsonify({"ok":False,"error":f"HTTP {r.status_code}"})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.post("/api/test/smb")
def api_test_smb():
    return jsonify(smb_test(load_conf()))

@app.post("/api/test/ftp")
def api_test_ftp():
    return jsonify(ftp_test(load_conf()))

@app.post("/api/test/tmdb")
def api_test_tmdb():
    cfg=load_conf()
    key=cfg.get("tmdb",{}).get("api_key","")
    if not key: return jsonify({"ok":False,"error":"Geen API key ingevuld"})
    try:
        r=_requests.get(f"https://api.themoviedb.org/3/configuration",
                        params={"api_key":key},timeout=8)
        if r.ok: return jsonify({"ok":True,"message":"TMDB verbinding geslaagd"})
        return jsonify({"ok":False,"error":f"HTTP {r.status_code} – ongeldige API key?"})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.post("/api/test/opensubtitles")
def api_test_opensubtitles():
    cfg=load_conf()
    o=cfg.get("opensubtitles",{})
    if not o.get("api_key"): return jsonify({"ok":False,"error":"Geen API key ingevuld"})
    global _os_token; _os_token=None  # reset zodat we echt testen
    token=os_login(cfg)
    if token: return jsonify({"ok":True,"message":"OpenSubtitles login geslaagd"})
    return jsonify({"ok":False,"error":"Login mislukt – controleer API key, gebruikersnaam en wachtwoord"})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","8080")),debug=False)
