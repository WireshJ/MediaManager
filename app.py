from __future__ import annotations
import os, json, re, datetime, threading, uuid, time, subprocess, shutil, tempfile, io, hashlib, secrets
from typing import Any, Dict, Optional, List
from collections import defaultdict, OrderedDict
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
from pathlib import Path
import requests as _requests

__version__ = "1.0.1"

app = Flask(__name__)

def _load_secret_key() -> str:
    if key := os.environ.get("APP_SECRET"):
        return key
    secret_file = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data")) / ".secret_key"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    if secret_file.exists():
        return secret_file.read_text().strip()
    key = secrets.token_hex(32)
    secret_file.write_text(key)
    return key

app.config["SECRET_KEY"] = _load_secret_key()

# ── Jinja2 globals ────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    return {"app_version": __version__}

# ── Jinja2 filters ────────────────────────────────────────────────
app.jinja_env.filters['basename'] = lambda p: Path(p).name
app.jinja_env.filters['display_name'] = lambda p: (
    Path(p.split("|")[1]).stem if "|" in p and p.startswith(("__smb__:", "__ftp__:"))
    else Path(p).name
)

# ── Paden ─────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
DATA_DIR  = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
CONF_FILE = DATA_DIR / "config.json"
CACHE_DIR = DATA_DIR / "cache"
for d in [DATA_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Schoonmaak bij opstarten ──────────────────────────────────────
def _startup_cleanup():
    """
    Ruimt alle tijdelijke bestanden op bij opstarten.
    Voorkomt dat een herstart rommel achterlaat (bijv. halverwege gedownloade films).
    """
    cleaned = 0

    # 1. Vaste tijdelijke mappen volledig weggooien
    for name in ("tmp_work", "tmp_strm", "smb_cache", "ftp_cache"):
        p = DATA_DIR / name
        if p.exists():
            try:
                shutil.rmtree(p)
                print(f"[🧹] Map verwijderd: {p}")
                cleaned += 1
            except Exception as e:
                print(f"[!] Cleanup fout {p}: {e}")

    # 2. Losse tijdelijke bestanden in DATA_DIR (mm_*, *.tmp, *.part, *.ytdl)
    for pattern in ("mm_*", "*.tmp", "*.part", "*.ytdl"):
        for f in DATA_DIR.glob(pattern):
            try:
                if f.is_file():
                    f.unlink(); cleaned += 1
                elif f.is_dir():
                    shutil.rmtree(f); cleaned += 1
                print(f"[🧹] Verwijderd: {f.name}")
            except Exception as e:
                print(f"[!] Cleanup fout {f}: {e}")

    # 3. Incomplete yt-dlp downloads (*.part, *.ytdl dieper genest)
    for pattern in ("*.part", "*.ytdl"):
        for f in DATA_DIR.rglob(pattern):
            try: f.unlink(); cleaned += 1
            except Exception: pass

    # 4. Xtream API cache opschonen
    if CACHE_DIR.exists():
        now = datetime.datetime.now(datetime.timezone.utc)
        for f in CACHE_DIR.glob("*.json"):
            try:
                age_days = (now - datetime.datetime.fromtimestamp(
                    f.stat().st_mtime, tz=datetime.timezone.utc)).days
                # Serie-detail caches (series_12345.json) na 3 dagen weg
                # Hoofd-caches (live.json, movies.json, series.json) na 7 dagen weg
                max_age = 3 if f.stem.startswith("series_") else 7
                if age_days > max_age:
                    f.unlink()
                    print(f"[🧹] Cache verlopen: {f.name} ({age_days}d oud)")
                    cleaned += 1
            except Exception: pass

    # 5. TMDB cache beperken tot max 2000 entries (LRU: verwijder oudste)
    tmdb_file = DATA_DIR / ".tmdb_cache.json"
    if tmdb_file.exists():
        try:
            data = json.loads(tmdb_file.read_text())
            if len(data) > 2000:
                # Bewaar de laatste 1500 entries (geen timestamp, dus op volgorde)
                trimmed = dict(list(data.items())[-1500:])
                tmdb_file.write_text(json.dumps(trimmed, ensure_ascii=False))
                removed = len(data) - len(trimmed)
                print(f"[🧹] TMDB cache ingekort: {removed} entries verwijderd ({len(trimmed)} bewaard)")
                cleaned += 1
        except Exception as e:
            print(f"[!] TMDB cache cleanup fout: {e}")

    if cleaned:
        print(f"[🧹] Startup cleanup klaar: {cleaned} item(s) verwijderd")
    else:
        print("[🧹] Startup cleanup: niets te verwijderen")

_startup_cleanup()

# ── Config ────────────────────────────────────────────────────────
DEFAULT_CONF: Dict[str, Any] = {
    "xtream":  {"server":"","port":0,"user":"","pwd":"","verify_tls":True,"timeout":15},
    "output":  {"base":"/media/library","live":"Live","movies":"Movies","series":"Series"},
    "ext":     {"live":"ts","movie":None,"episode":None},
    "cache":   {"ttl_hours":6},
    "storage": {
        "mode": "mount",
        "show_gauge": True,
        "smb":  {"host":"","share":"","user":"","password":"","domain":"","films_path":"Films","series_path":"Series"},
        "ftp":  {"host":"","port":21,"user":"","password":"","films_path":"/media/Films","series_path":"/media/Series"},
    },
    "jellyfin":{"enabled":False,"url":"","api_key":"","films_library_id":"","series_library_id":""},
    "tmdb":    {"enabled":False,"api_key":""},
    "opensubtitles":{"enabled":False,"api_key":"","username":"","password":"","langs":["nl","en"]},
    "app":     {"settings_password":""},  # leeg = geen beveiliging
}

def _deep_merge(default: dict, saved: dict) -> dict:
    """Deep merge: saved overschrijft default, ook voor geneste dicts."""
    result = dict(default)
    for k, v in saved.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result

def load_conf() -> Dict[str, Any]:
    if CONF_FILE.exists():
        try:
            with open(CONF_FILE,"r",encoding="utf-8") as f:
                saved = json.load(f)
            return _deep_merge(DEFAULT_CONF, saved)
        except Exception:
            pass
    return _deep_merge(DEFAULT_CONF, {})

def save_conf(cfg: Dict) -> None:
    CONF_FILE.parent.mkdir(parents=True,exist_ok=True)
    with open(CONF_FILE,"w",encoding="utf-8") as f:
        json.dump(cfg,f,indent=2,ensure_ascii=False)

def out_path(cfg: Dict, key: str) -> Path:
    return Path(cfg["output"]["base"]) / cfg["output"].get(key,key)

# ── Instellingen beveiliging ──────────────────────────────────────
def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def _settings_locked(cfg: Dict) -> bool:
    """Geeft True als instellingen beveiligd zijn met een wachtwoord."""
    return bool((cfg.get("app") or {}).get("settings_password","").strip())

def _settings_authenticated() -> bool:
    """Geeft True als de huidige sessie geauthenticeerd is."""
    return session.get("settings_auth") is True

def _require_settings_auth(cfg: Dict):
    """Redirect naar login als instellingen beveiligd zijn en sessie niet geauthenticeerd."""
    if _settings_locked(cfg) and not _settings_authenticated():
        return redirect(url_for("settings_login"))

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

    def get_live_streams(self):    return self._get(self._url(action="get_live_streams")) or []
    def get_vod_streams(self):     return self._get(self._url(action="get_vod_streams")) or []
    def get_series(self):          return self._get(self._url(action="get_series")) or []
    def get_series_info(self,sid): return self._get(self._url(action="get_series_info",series_id=sid)) or {}
    def live_url(self,sid,ext="ts"):    return f"{self.base}/live/{self.user}/{self.pwd}/{sid}.{pick_ext(ext,'ts')}"
    def vod_url(self,sid,ext="mp4"):    return f"{self.base}/movie/{self.user}/{self.pwd}/{sid}.{pick_ext(ext,'mp4')}"
    def episode_url(self,eid,ext="mp4"):return f"{self.base}/series/{self.user}/{self.pwd}/{eid}.{pick_ext(ext,'mp4')}"

def make_api(cfg: Dict) -> Xtream:
    x = cfg["xtream"]
    return Xtream(x["server"],x.get("port",0),x["user"],x["pwd"],
                  int(x.get("timeout",15)),bool(x.get("verify_tls",True)))

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

def _ctitle(fn: str):
    """Extraheer zoektitel en jaar uit een bestandsnaam of display naam."""
    # Fix Bug 5: virtuele paden correct afhandelen
    if "|" in fn and fn.startswith(("__smb__:", "__ftp__:")):
        fn = fn.split("|")[1]  # neem display naam na de pipe
    name = Path(fn).stem
    name = re.sub(r'[Ss]\d{1,2}[Ee]\d{1,3}.*','',name)
    name = re.sub(r'\b(1080p|720p|4K|HDR|BluRay|WEBRip|HDTV|x264|x265|HEVC|REMUX|Remastered)\b.*',
                  '',name,flags=re.IGNORECASE)
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

def tmdb_series_by_id(tmdb_id, key):
    if not key or not tmdb_id: return None
    try:
        r = _requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}",
                          params={"api_key":key,"language":"en-US"},timeout=8)
        if r.ok:
            d=r.json()
            return {"title":d.get("original_name") or d.get("name")}
    except Exception: pass
    return None

def tmdb_discover(key: str) -> dict:
    """Haal trending/populaire films en series op via TMDB."""
    if not key: return {}
    cache_key = "tmdb_discover"
    if cache_key in _tc:
        cached = _tc[cache_key]
        # Cache 6 uur geldig
        try:
            age = (datetime.datetime.now(datetime.timezone.utc) -
                   datetime.datetime.fromisoformat(cached.get("fetched_at","2000-01-01")
                   ).replace(tzinfo=datetime.timezone.utc)).total_seconds()
            if age < 6 * 3600:
                return cached
        except Exception: pass

    def _fetch(endpoint, params=None):
        p = {"api_key": key, "language": "nl", **(params or {})}
        try:
            r = _requests.get(f"https://api.themoviedb.org/3{endpoint}", params=p, timeout=8)
            return r.json().get("results", []) if r.ok else []
        except Exception: return []

    def _fmt_movies(items):
        return [{"id": x.get("id"), "title": x.get("title") or x.get("name",""),
                 "year": (x.get("release_date") or x.get("first_air_date",""))[:4],
                 "rating": round(x.get("vote_average",0),1),
                 "overview": (x.get("overview") or "")[:200],
                 "poster": f"https://image.tmdb.org/t/p/w342{x['poster_path']}" if x.get("poster_path") else None,
                 "backdrop": f"https://image.tmdb.org/t/p/w780{x['backdrop_path']}" if x.get("backdrop_path") else None,
                 "media_type": x.get("media_type","movie")}
                for x in items if x.get("poster_path")]

    def _fmt_series(items):
        return [{"id": x.get("id"), "title": x.get("name") or x.get("title",""),
                 "year": (x.get("first_air_date") or "")[:4],
                 "rating": round(x.get("vote_average",0),1),
                 "overview": (x.get("overview") or "")[:200],
                 "poster": f"https://image.tmdb.org/t/p/w342{x['poster_path']}" if x.get("poster_path") else None,
                 "backdrop": f"https://image.tmdb.org/t/p/w780{x['backdrop_path']}" if x.get("backdrop_path") else None}
                for x in items if x.get("poster_path")]

    data = {
        "trending_all":    _fmt_movies(_fetch("/trending/all/week")),
        "trending_movies": _fmt_movies(_fetch("/trending/movie/week")),
        "trending_series": _fmt_series(_fetch("/trending/tv/week")),
        "top_movies":      _fmt_movies(_fetch("/movie/top_rated", {"region":"NL"})),
        "popular_movies":  _fmt_movies(_fetch("/movie/popular",   {"region":"NL"})),
        "popular_series":  _fmt_series(_fetch("/tv/popular")),
        "now_playing":     _fmt_movies(_fetch("/movie/now_playing",{"region":"NL"})),
        "fetched_at":      datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _tc[cache_key] = data
    _stc()
    return data

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

# ── Postprocessing ────────────────────────────────────────────────
_PFX_RE = re.compile(
    r'^(?:'
    r'\|[A-Za-z]{2,4}\|\s*'
    r'|[A-Za-z]{2,4}\s*-\s*'
    r'|[A-Za-z0-9+\-_]{2,12}\s*-\s*'
    r'|4K\s*[-–]\s*'
    r')*'
)
_SFX_RE = re.compile(r'\s*\([A-Za-z]{2}\)$')

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

def _clean_strm_name(filename: str) -> str:
    stem = Path(filename).stem
    ext  = Path(filename).suffix
    ep_match = re.search(r'[Ss](\d{1,2})[Ee](\d{1,3})', stem)
    ep_code  = ep_match.group(0).upper() if ep_match else None
    cleaned  = strip_prefix(stem)
    cleaned  = re.sub(r'\s*\(\d{4}\)', '', cleaned).strip()
    cleaned  = re.sub(r'\s*\([A-Z]{2}\)', '', cleaned).strip()
    cleaned  = re.sub(r'\s*\b(4K|UHD|1080p|720p|HDR|SDR|BluRay|WEBRip|HDTV|x264|x265|HEVC|REMUX)\b.*',
                      '', cleaned, flags=re.IGNORECASE).strip()
    if ep_code:
        cleaned = re.sub(r'\s*[Ss]\d{1,2}[Ee]\d{1,3}.*', '', cleaned).strip()
        cleaned = f"{cleaned} {ep_code}"
    cleaned = safe_fn(cleaned)
    return cleaned + ext if cleaned else filename

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
        strms = list(folder.glob("*.strm"))
        for i,strm in enumerate(strms):
            tname = f"{new_title}.strm" if len(strms)==1 else f"{new_title} ({i+1}).strm"
            tgt = folder / tname
            if strm != tgt and not tgt.exists():
                try: strm.rename(tgt)
                except Exception: pass
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

    def _clean_folder_name(name: str) -> str:
        n = safe_fn(strip_prefix(name))
        n = re.sub(r'\s*\(\d{4}\)', '', n).strip()
        n = re.sub(r'\s*\([A-Z]{2}\)$', '', n).strip()
        return safe_fn(n) or name

    def _rename_files_in(folder: Path, serie_clean: str):
        for f in sorted(folder.iterdir()):
            if not f.is_file(): continue
            if f.suffix == ".strm":
                cleaned = _clean_strm_name(f.name)
                ep_match = re.search(r'[Ss]\d{1,2}[Ee]\d{1,3}', cleaned)
                cleaned_name = (f"{serie_clean} {ep_match.group(0).upper()}.strm"
                                if ep_match else cleaned)
            else:
                stem = safe_fn(strip_prefix(f.stem))
                stem = re.sub(r'\s*\(\d{4}\)', '', stem).strip()
                stem = re.sub(r'\s*\([A-Za-z]{2}\)(\.|$)', r'\1', stem).strip()
                cleaned_name = safe_fn(stem) + f.suffix
            if cleaned_name and cleaned_name != f.name:
                tgt = folder / cleaned_name
                if not tgt.exists():
                    try: f.rename(tgt)
                    except Exception: pass

    plan: Dict[str, List[Path]] = {}
    for folder in sorted(series_dir.iterdir()):
        if not folder.is_dir(): continue
        plan.setdefault(_clean_folder_name(folder.name), []).append(folder)

    for new_name, folders in plan.items():
        new_folder = series_dir / new_name
        folders.sort(key=lambda p: 0 if p.name == new_name else 1)
        if not new_folder.exists():
            try:
                folders[0].rename(new_folder)
                results["renamed"].append(f"{folders[0].name} → {new_name}")
                folders = folders[1:]
            except Exception as e:
                results["errors"].append(str(e)); continue
        else:
            folders = [f for f in folders if f != new_folder]
        for src in folders:
            if not src.exists(): continue
            try:
                for f in list(src.iterdir()):
                    if not f.is_file(): continue
                    tgt = new_folder / f.name
                    if not tgt.exists(): f.rename(tgt)
                    elif len(f.name) < len(tgt.name): tgt.unlink(); f.rename(tgt)
                    else: f.unlink()
                src.rmdir()
                results["renamed"].append(f"{src.name} → {new_name} (samengevoegd)")
            except Exception as e:
                results["errors"].append(f"Samenvoegen {src.name}: {e}")
        if new_folder.exists():
            _rename_files_in(new_folder, new_name)
    return results

# ── Lokale scanner ────────────────────────────────────────────────
_S_CODE = re.compile(r'[Ss](\d{1,2})[Ee]\d{1,3}')

def detect_season(path):
    m = re.search(r'/(?:Season|Seizoen)[ _-]?(\d{1,2})/', path, re.IGNORECASE)
    if m:
        try: return int(m.group(1))
        except ValueError: pass
    m = _S_CODE.search(path)
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
    stem = Path(filename).stem
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
                        seen_films.add(stem); films.append(os.path.join(root,f))
    series_dict: Dict[str,List] = {}
    if sdir.exists():
        for root,_,files in os.walk(sdir):
            for f in files:
                if f.endswith(".strm"):
                    rel = Path(root).relative_to(sdir).parts
                    sname = rel[0] if rel else _extract_serie_name(f)
                    series_dict.setdefault(sname,[]).append(os.path.join(root,f))
    return films, series_dict

# ── Storage backend ───────────────────────────────────────────────
def storage_mode(cfg: Dict) -> str:
    return cfg.get("storage", {}).get("mode", "mount")

def _smb_cfg(cfg): return cfg.get("storage",{}).get("smb",{})
def _ftp_cfg(cfg): return cfg.get("storage",{}).get("ftp",{})

def storage_subdir(cfg: Dict, kind: str) -> str:
    """
    Geeft het juiste subpad terug voor films of series, afhankelijk van storage mode.
    kind: 'movies'/'films' voor films, 'series' voor series.
    Bij mount: gebruik cfg['output']['movies'] / cfg['output']['series']
    Bij SMB:   gebruik smb films_path / series_path
    Bij FTP:   gebruik ftp films_path / series_path
    """
    mode = storage_mode(cfg)
    is_film = kind in ("movies", "films")
    if mode == "smb":
        s = _smb_cfg(cfg)
        return s.get("films_path", "Films") if is_film else s.get("series_path", "Series")
    elif mode == "ftp":
        f = _ftp_cfg(cfg)
        return f.get("films_path", "/media/Films") if is_film else f.get("series_path", "/media/Series")
    else:  # mount
        return cfg["output"]["movies"] if is_film else cfg["output"]["series"]

def storage_write_file(local_path: str, remote_subpath: str, cfg: Dict) -> bool:
    mode = storage_mode(cfg)
    if mode == "mount": return True
    elif mode == "smb": return _smb_put(local_path, remote_subpath, cfg)
    elif mode == "ftp": return _ftp_put(local_path, remote_subpath, cfg)
    return False

def storage_list_strm(cfg: Dict) -> tuple[List[str], Dict[str,List[str]]]:
    mode = storage_mode(cfg)
    if mode == "mount": return scan_local(cfg)
    elif mode == "smb": return _smb_list_strm(cfg)
    elif mode == "ftp": return _ftp_list_strm(cfg)
    return [], {}

def _clean_remote_subpath(remote_subpath: str) -> str:
    """Reinig pad vóór upload naar SMB/FTP."""
    parts = remote_subpath.replace("\\", "/").split("/")
    if len(parts) < 2: return remote_subpath
    cleaned = [parts[0]]
    for i, part in enumerate(parts[1:], 1):
        is_last = (i == len(parts) - 1)
        if is_last and part.endswith(".strm"):
            cleaned.append(_clean_strm_name(part))
        else:
            n = safe_fn(strip_prefix(part))
            n = re.sub(r'\s*\(\d{4}\)', '', n).strip()
            n = re.sub(r'\s*\([A-Z]{2}\)$', '', n).strip()
            cleaned.append(safe_fn(n) or part)
    return "/".join(cleaned)

def storage_write_strm(remote_subpath: str, url: str, cfg: Dict,
                       auto_postprocess: bool = True,
                       jellyfin_push: bool = True) -> str:
    mode    = storage_mode(cfg)
    films_sub  = storage_subdir(cfg, "movies")
    series_sub = storage_subdir(cfg, "series")
    rp = remote_subpath.replace("\\", "/").lstrip("/")
    is_film = (rp.lower().startswith(films_sub.lower().lstrip("/") + "/") or
               rp.lower() == films_sub.lower().lstrip("/"))
    if mode == "mount":
        dest = Path(cfg["output"]["base"]) / remote_subpath
        write_strm(dest, url)
        result = str(dest)
        if auto_postprocess:
            try:
                if is_film: postprocess_movies(cfg)
                else:       postprocess_series(cfg)
            except Exception as e:
                print(f"[!] Auto-postprocess fout: {e}")
    else:
        clean_subpath = _clean_remote_subpath(remote_subpath)
        with tempfile.NamedTemporaryFile(suffix=".strm", delete=False, dir=DATA_DIR) as tf:
            tmp_path = Path(tf.name)
        try:
            write_strm(tmp_path, url)
            if not storage_write_file(str(tmp_path), clean_subpath, cfg):
                raise RuntimeError(f"Upload mislukt naar {mode.upper()}: {clean_subpath}")
        finally:
            try: tmp_path.unlink()
            except Exception: pass
        result = clean_subpath

    # Jellyfin push na aanmaken .strm (in achtergrond, blokkeert niet)
    if jellyfin_push:
        try: jf_refresh(cfg, is_film)
        except Exception as e: print(f"[!] Jellyfin push fout: {e}")

    return result

# ── SMB ───────────────────────────────────────────────────────────
def _smb_connect(cfg: Dict):
    try:
        from smb.SMBConnection import SMBConnection
    except ImportError:
        raise RuntimeError("pysmb niet geïnstalleerd: pip install pysmb")
    s = _smb_cfg(cfg)
    conn = SMBConnection(s.get("user",""), s.get("password",""), "mediamanager",
                         s.get("host",""), domain=s.get("domain",""),
                         use_ntlm_v2=True, is_direct_tcp=True)
    if not conn.connect(s["host"], 445):
        raise RuntimeError(f"SMB verbinding mislukt naar {s['host']}")
    return conn, s.get("share","")

def _smb_put(local_path: str, remote_subpath: str, cfg: Dict) -> bool:
    try:
        conn, share = _smb_connect(cfg)
        remote = remote_subpath.replace("\\", "/")
        parts  = remote.split("/")
        # Maak alle tussenliggende mappen aan
        for i in range(1, len(parts)):
            try: conn.createDirectory(share, "/".join(parts[:i]))
            except Exception: pass
        # Verwijder bestand als het al bestaat
        try: conn.deleteFiles(share, remote)
        except Exception: pass
        # Upload via storeFileFromOffset met offset=0 zodat pysmb
        # niet het hele bestand in geheugen laadt maar chunked werkt
        with open(local_path, "rb") as f:
            conn.storeFileFromOffset(share, remote, f, offset=0, truncate=True)
        conn.close()
        return True
    except Exception as e:
        print(f"[!] SMB upload fout ({remote_subpath}): {e}")
        return False

def _smb_list_strm(cfg: Dict) -> tuple[List[str], Dict[str,List[str]]]:
    films: List[str] = []
    series_dict: Dict[str,List[str]] = {}
    seen_films: set[str] = set()
    try:
        conn, share = _smb_connect(cfg)
        s = _smb_cfg(cfg)

        def _walk(remote_dir: str, kind: str, serie_name: Optional[str] = None, depth: int = 0):
            if depth > 4: return  # max recursie diepte
            try: items = conn.listPath(share, remote_dir)
            except Exception as e: print(f"[!] SMB walk ({remote_dir}): {e}"); return
            for item in items:
                if item.filename in (".",".."): continue
                rpath = f"{remote_dir}/{item.filename}"
                if item.isDirectory:
                    n = safe_fn(strip_prefix(item.filename))
                    n = re.sub(r'\s*\(\d{4}\)','',n).strip()
                    n = re.sub(r'\s*\([A-Z]{2}\)$','',n).strip()
                    _walk(rpath, kind, n or item.filename, depth+1)
                elif item.filename.endswith(".strm"):
                    cn = _clean_strm_name(item.filename)
                    vp = f"__smb__:{kind}:{rpath}|{cn}"
                    if kind == "films":
                        stem = Path(cn).stem.lower()
                        if stem not in seen_films:
                            seen_films.add(stem); films.append(vp)
                    else:
                        sname = serie_name or _extract_serie_name(cn)
                        series_dict.setdefault(sname,[]).append(vp)

        _walk(s.get("films_path","Films"),  "films")
        _walk(s.get("series_path","Series"), "series")
        conn.close()
    except Exception as e:
        print(f"[!] SMB list fout: {e}")
    return films, series_dict

def smb_test(cfg: Dict) -> dict:
    try:
        conn, share = _smb_connect(cfg)
        shares = [s.name for s in conn.listShares()]
        conn.close()
        return {"ok":True,"shares":shares}
    except Exception as e:
        return {"ok":False,"error":str(e)}

# ── FTP ───────────────────────────────────────────────────────────
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
    current = ""
    for part in [p for p in remote_path.replace("\\","/").split("/") if p]:
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
        ftp.quit(); return True
    except Exception as e:
        print(f"[!] FTP upload fout: {e}"); return False

def _ftp_list_strm(cfg: Dict) -> tuple[List[str], Dict[str,List[str]]]:
    films: List[str] = []
    series_dict: Dict[str,List[str]] = {}
    seen_films: set[str] = set()
    try:
        import ftplib
        ftp = _ftp_connect(cfg)
        fc  = _ftp_cfg(cfg)

        def _walk(remote_dir: str, kind: str, serie_name: Optional[str] = None, depth: int = 0):
            if depth > 4: return
            try:
                items: List[str] = []
                ftp.retrlines(f"LIST {remote_dir}", items.append)
            except Exception as e:
                print(f"[!] FTP walk ({remote_dir}): {e}"); return
            for line in items:
                parts = line.split(None, 8)
                if len(parts) < 9: continue
                name = parts[8]; is_dir = line.startswith("d")
                rfp  = f"{remote_dir}/{name}"
                if is_dir:
                    n = safe_fn(strip_prefix(name))
                    n = re.sub(r'\s*\(\d{4}\)','',n).strip()
                    n = re.sub(r'\s*\([A-Z]{2}\)$','',n).strip()
                    _walk(rfp, kind, n or name, depth+1)
                elif name.endswith(".strm"):
                    cn = _clean_strm_name(name)
                    vp = f"__ftp__:{kind}:{rfp}|{cn}"
                    if kind == "films":
                        stem = Path(cn).stem.lower()
                        if stem not in seen_films:
                            seen_films.add(stem); films.append(vp)
                    else:
                        sname = serie_name or _extract_serie_name(cn)
                        series_dict.setdefault(sname,[]).append(vp)

        _walk(fc.get("films_path","/media/Films"),  "films")
        _walk(fc.get("series_path","/media/Series"), "series")
        ftp.quit()
    except Exception as e:
        print(f"[!] FTP list fout: {e}")
    return films, series_dict

def ftp_test(cfg: Dict) -> dict:
    try:
        ftp = _ftp_connect(cfg)
        welcome = ftp.getwelcome(); ftp.quit()
        return {"ok":True,"welcome":welcome}
    except Exception as e:
        return {"ok":False,"error":str(e)}

def storage_free_space(cfg: Dict) -> Optional[Dict]:
    """Geeft {"free": bytes, "total": bytes} terug, of None als niet beschikbaar."""
    mode = storage_mode(cfg)
    if mode == "mount":
        base = Path(cfg["output"]["base"])
        try:
            if not base.exists():
                base = Path("/")
            u = shutil.disk_usage(base)
            return {"free": u.free, "total": u.total}
        except Exception:
            return None
    elif mode == "smb":
        s = _smb_cfg(cfg)
        if not s.get("host") or not s.get("share"):
            return None
        if not shutil.which("smbclient"):
            return None
        cred_file = None
        try:
            # Credentials bestand voorkomt problemen met speciale tekens in wachtwoord
            with tempfile.NamedTemporaryFile(mode='w', suffix='.cred',
                                             dir=DATA_DIR, delete=False) as tf:
                tf.write(f"username={s.get('user','')}\n")
                tf.write(f"password={s.get('password','')}\n")
                if s.get("domain"):
                    tf.write(f"domain={s['domain']}\n")
                cred_file = tf.name
            cmd = [
                "smbclient", f"//{s['host']}/{s['share']}",
                "-A", cred_file,
                "--option=client min protocol=SMB2",
                "-c", "du",
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            m = re.search(r'(\d+)\s+blocks of size\s+(\d+)\.\s+(\d+)\s+blocks available',
                          r.stdout + r.stderr)
            if m:
                bs = int(m.group(2))
                return {"free": int(m.group(3)) * bs, "total": int(m.group(1)) * bs}
            print(f"[storage] smbclient output: {r.stdout.strip() or r.stderr.strip()}")
        except Exception as e:
            print(f"[storage] SMB fout: {e}")
        finally:
            if cred_file:
                try: Path(cred_file).unlink()
                except Exception: pass
        return None
    return None  # FTP: niet ondersteund

# ── Download Queue ────────────────────────────────────────────────
class DownloadQueue:
    MAX_RETRIES = 3

    def __init__(self):
        self._lock = threading.Lock()
        self.queue:   List[dict] = []
        self.history: List[dict] = []
        self.current: Optional[dict] = None
        self._wt:     Optional[threading.Thread] = None
        self._proc:   Optional[subprocess.Popen] = None
        self._cancel_flag: bool = False

    def _entry(self, fp: str) -> dict:
        if fp.startswith(("__smb__:", "__ftp__:")):
            display = Path(fp.split("|")[1]).stem if "|" in fp else fp.split(":")[-1]
        else:
            display = Path(fp).stem
        return {"id":str(uuid.uuid4()),"file_path":fp,"name":display,"status":"queued",
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

    def delete_history(self, iid):
        with self._lock: self.history=[e for e in self.history if e["id"]!=iid]

    def clear_history(self):
        with self._lock: self.history.clear()

    def cancel(self) -> bool:
        with self._lock:
            if not self.current:
                return False
            self._cancel_flag = True
        proc = self._proc
        if proc and proc.poll() is None:
            try: proc.kill()
            except Exception: pass
        return True

    def retry(self, iid):
        with self._lock:
            it=next((e for e in self.history if e["id"]==iid),None)
            if it and it["status"]=="failed":
                self.history.remove(it)
                it.update({"status":"queued","progress":0,"attempt":0,"error":None,"finished_at":None})
                self.queue.append(it)
        self._ew()

    def _ew(self):
        if self._wt is None or not self._wt.is_alive():
            self._wt = threading.Thread(target=self._worker, daemon=True)
            self._wt.start()

    def _worker(self):
        while True:
            with self._lock:
                if not self.queue: self.current=None; return
                e = self.queue.pop(0); self.current = e
                self._cancel_flag = False
            ok = self._process(e)
            with self._lock:
                e["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                if self._cancel_flag:
                    e["status"] = "cancelled"
                else:
                    e["status"] = "done" if ok else "failed"
                    if ok: e["progress"] = 100
                self.history.insert(0, e); self.current = None
                self._cancel_flag = False

    def _process(self, e):
        cfg      = load_conf()
        raw_path = e["file_path"]
        mode     = storage_mode(cfg)
        is_virtual = raw_path.startswith(("__smb__:", "__ftp__:"))

        if is_virtual:
            parts       = raw_path.split(":", 2)
            proto       = parts[0]
            kind        = parts[1] if len(parts) > 2 else "films"
            rest        = parts[2] if len(parts) > 2 else parts[-1]
            remote_path = rest.split("|")[0]
            display     = rest.split("|")[1] if "|" in rest else Path(remote_path).name
            clean_name  = Path(display).stem
            is_film     = (kind == "films")
            url = self._read_virtual_strm(remote_path, proto, cfg)
            if not url:
                e["error"] = f"Kan stream URL niet ophalen: {remote_path}"; return False
        else:
            fp         = Path(raw_path)
            is_film    = "Movies" in raw_path or "Films" in raw_path
            clean_name = fp.stem
            url        = None

        # Output locatie
        # Voor series: bepaal seriename uit clean_name (bijv. "The Simpsons" uit "The Simpsons S01E01")
        if is_film:
            folder_name = clean_name   # filmmap = "The Dark Knight"
            file_name   = clean_name   # bestand = "The Dark Knight.mkv"
        else:
            folder_name = _extract_serie_name(clean_name)  # "The Simpsons"
            file_name   = clean_name                        # "The Simpsons S01E01.mkv"

        if mode == "mount":
            od = out_path(cfg,"movies") if is_film else out_path(cfg,"series")
            # Altijd in submap opslaan: Movies/Titel/Titel.mkv of Series/Naam/Naam S01E01.mkv
            item_dir  = od / folder_name
            item_dir.mkdir(parents=True, exist_ok=True)
            out_file  = str(item_dir / (file_name + ".mkv"))
            tmp_dir   = None
        else:
            tmp_dir  = Path(tempfile.mkdtemp(prefix="mm_", dir=DATA_DIR))
            out_file = str(tmp_dir / (file_name + ".mkv"))

        def _cleanup():
            if tmp_dir and tmp_dir.exists():
                try: shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception: pass

        try:
            if url is None:
                try: url = Path(raw_path).read_text().strip().splitlines()[0].strip()
                except Exception as ex: e["error"]=f"Kan .strm niet lezen: {ex}"; return False

            if not url or not url.startswith("http"):
                e["error"] = "Ongeldige of lege stream URL"; return False

            ok = False
            for attempt in range(1, self.MAX_RETRIES + 1):
                if self._cancel_flag: return False
                e.update({"attempt":attempt,"status":"downloading","progress":0,
                          "speed":"","eta":"","total_size":"","error":None})
                ok = self._ytdlp(url, out_file, e)
                if self._cancel_flag: return False
                if ok: break
                e["error"] = f"Poging {attempt} mislukt"
                if attempt < self.MAX_RETRIES: time.sleep(3)
            if not ok: return False

            if mode in ("smb","ftp"):
                e["status"] = f"uploaden via {mode.upper()}..."
                subdir = storage_subdir(cfg, "movies" if is_film else "series")
                # Serie: Series/The Simpsons/The Simpsons S01E01.mkv
                # Film:  Films/The Dark Knight/The Dark Knight.mkv
                remote_mkv = f"{subdir}/{folder_name}/{file_name}.mkv"
                if not storage_write_file(out_file, remote_mkv, cfg):
                    e["error"] = f"{mode.upper()} upload mislukt"; return False

            # Ondertitels
            oc = cfg.get("opensubtitles",{})
            if oc.get("enabled") and oc.get("api_key"):
                e["status"] = "ondertitels ophalen..."
                tk = cfg["tmdb"]["api_key"] if cfg["tmdb"].get("enabled") else ""
                if is_film:
                    info  = tmdb_movie(clean_name, tk)
                    title = info.get("title") or clean_name
                    season = episode = None
                else:
                    m       = re.search(r'[Ss](\d+)[Ee](\d+)', clean_name)
                    info    = tmdb_series(folder_name, tk)
                    title   = info.get("title") or folder_name
                    season  = int(m.group(1)) if m else None
                    episode = int(m.group(2)) if m else None
                for lang in (oc.get("langs") or ["nl","en"]):
                    time.sleep(1)
                    dest = out_file.replace(".mkv", f".{lang}.srt")
                    if os_download(title, lang, dest, cfg, season, episode) and mode in ("smb","ftp"):
                        subdir = storage_subdir(cfg, "movies" if is_film else "series")
                        storage_write_file(dest, f"{subdir}/{folder_name}/{file_name}.{lang}.srt", cfg)

            jf_refresh(cfg, is_film)

            # Verwijder het .strm bestand nu het .mkv beschikbaar is
            e["status"] = "strm opruimen..."
            if mode == "mount":
                # Lokaal .strm bestand verwijderen
                strm_path = Path(raw_path)
                if strm_path.exists() and strm_path.suffix == ".strm":
                    try: strm_path.unlink()
                    except Exception as ex: print(f"[!] Kon .strm niet verwijderen: {ex}")
            elif is_virtual and mode == "smb":
                # .strm op de SMB share verwijderen
                try:
                    conn, share = _smb_connect(cfg)
                    conn.deleteFiles(share, remote_path)
                    conn.close()
                except Exception as ex:
                    print(f"[!] Kon .strm niet verwijderen van SMB: {ex}")
            elif is_virtual and mode == "ftp":
                try:
                    ftp = _ftp_connect(cfg)
                    ftp.delete(remote_path)
                    ftp.quit()
                except Exception as ex:
                    print(f"[!] Kon .strm niet verwijderen van FTP: {ex}")

            if mode == "mount":
                e["status"] = "postprocessing..."
                if is_film: postprocess_movies(cfg)
                else:       postprocess_series(cfg)

            return True
        except Exception as ex:
            e["error"] = str(ex); return False
        finally:
            _cleanup()

    def _read_virtual_strm(self, remote_path: str, proto: str, cfg: Dict) -> Optional[str]:
        try:
            if proto == "__smb__":
                conn, share = _smb_connect(cfg)
                buf = io.BytesIO()
                conn.retrieveFile(share, remote_path, buf)
                conn.close()
                return buf.getvalue().decode("utf-8").strip().splitlines()[0].strip()
            elif proto == "__ftp__":
                ftp = _ftp_connect(cfg)
                buf = io.BytesIO()
                ftp.retrbinary(f"RETR {remote_path}", buf.write)
                ftp.quit()
                return buf.getvalue().decode("utf-8").strip().splitlines()[0].strip()
        except Exception as ex:
            print(f"[!] Kan .strm niet lezen via {proto}: {ex}")
        return None

    def _ytdlp(self, url, output, e):
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--newline",
            "--progress",
            "--progress-template", "%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s|%(progress._total_bytes_str)s",
            "--buffer-size", "16K",
            "--no-part",
            "-o", output,
            url,
        ]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=8192)
            self._proc = proc
            for line in proc.stdout:
                line = line.strip()
                if "|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        try: e["progress"] = float(parts[0].strip().replace("%",""))
                        except ValueError: pass
                        e["speed"] = parts[1].strip(); e["eta"] = parts[2].strip()
                        if len(parts) >= 4:
                            size = parts[3].strip()
                            if size and size not in ("N/A", "Unknown"):
                                e["total_size"] = size
            proc.wait()
            self._proc = None
            return proc.returncode == 0
        except Exception as ex:
            self._proc = None
            e["error"] = str(ex); return False

download_queue = DownloadQueue()

# ══════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════

@app.get("/")
def index():
    cfg = load_conf()
    x   = cfg.get("xtream",{})
    if not x.get("user") or not x.get("server"):
        return redirect(url_for("settings"))
    return redirect(url_for("browse"))

@app.route("/settings/login", methods=["GET","POST"])
def settings_login():
    cfg = load_conf()
    if not _settings_locked(cfg):
        return redirect(url_for("settings"))
    if _settings_authenticated():
        return redirect(url_for("settings"))
    error = None
    if request.method == "POST":
        pw   = request.form.get("password","")
        stored = cfg.get("app",{}).get("settings_password","")
        if _hash_pw(pw) == stored or pw == stored:  # vergelijk gehashed of plaintext
            session["settings_auth"] = True
            session.permanent = True
            return redirect(url_for("settings"))
        error = "Ongeldig wachtwoord."
    return render_template("settings_login.html", error=error)

@app.get("/settings/logout")
def settings_logout():
    session.pop("settings_auth", None)
    return redirect(url_for("settings_login"))

@app.route("/settings", methods=["GET","POST"])
def settings():
    cfg = load_conf()
    # Beveiliging check
    auth_redirect = _require_settings_auth(cfg)
    if auth_redirect: return auth_redirect

    if request.method == "POST":
        sec = request.form.get("section","")
        if sec == "xtream":
            rp = request.form.get("port","").strip()
            cfg["xtream"].update({
                "server":     request.form.get("server","").strip(),
                "port":       int(rp) if rp and rp.isdigit() else 0,
                "user":       request.form.get("user","").strip(),
                "pwd":        request.form.get("pwd","").strip(),
                "verify_tls": request.form.get("verify_tls") == "on",
                "timeout":    int(request.form.get("timeout") or 15),
            })
        elif sec == "storage":
            mode = request.form.get("storage_mode","mount")
            cfg["storage"]["mode"] = mode
            cfg["storage"]["show_gauge"] = request.form.get("show_gauge") == "on"
            for field in ("base","live","movies","series"):
                val = request.form.get(field,"").strip()
                if val: cfg["output"][field] = val
            cfg["ext"].update({
                "live":    (request.form.get("ext_live") or "ts").strip(),
                "movie":   (request.form.get("ext_movie") or "").strip() or None,
                "episode": (request.form.get("ext_episode") or "").strip() or None,
            })
            cfg["cache"]["ttl_hours"] = max(0, int(request.form.get("ttl_hours") or 6))
            cfg["storage"]["smb"].update({
                "host":        request.form.get("smb_host","").strip(),
                "share":       request.form.get("smb_share","").strip(),
                "user":        request.form.get("smb_user","").strip(),
                "password":    request.form.get("smb_password","").strip(),
                "domain":      request.form.get("smb_domain","").strip(),
                "films_path":  request.form.get("smb_films_path","Films").strip(),
                "series_path": request.form.get("smb_series_path","Series").strip(),
            })
            rp = request.form.get("ftp_port","21").strip()
            cfg["storage"]["ftp"].update({
                "host":         request.form.get("ftp_host","").strip(),
                "port":         int(rp) if rp.isdigit() else 21,
                "user":         request.form.get("ftp_user","").strip(),
                "password":     request.form.get("ftp_password","").strip(),
                "films_path":   request.form.get("ftp_films_path","/media/Films").strip(),
                "series_path":  request.form.get("ftp_series_path","/media/Series").strip(),
            })
        elif sec == "jellyfin":
            cfg["jellyfin"].update({
                "enabled":           request.form.get("jf_enabled") == "on",
                "url":               request.form.get("jf_url","").strip(),
                "api_key":           request.form.get("jf_api_key","").strip(),
                "films_library_id":  request.form.get("jf_films_lib","").strip(),
                "series_library_id": request.form.get("jf_series_lib","").strip(),
            })
        elif sec == "tmdb":
            cfg["tmdb"].update({"enabled": request.form.get("tmdb_enabled") == "on",
                                "api_key": request.form.get("tmdb_api_key","").strip()})
            _ltc()
        elif sec == "opensubtitles":
            langs = [l.strip() for l in re.split(r'[,;]', request.form.get("os_langs","nl,en")) if l.strip()]
            cfg["opensubtitles"].update({
                "enabled":  request.form.get("os_enabled") == "on",
                "api_key":  request.form.get("os_api_key","").strip(),
                "username": request.form.get("os_username","").strip(),
                "password": request.form.get("os_password","").strip(),
                "langs":    langs,
            })
            global _os_token; _os_token = None
        elif sec == "app":
            new_pw      = request.form.get("new_password","").strip()
            confirm_pw  = request.form.get("confirm_password","").strip()
            current_pw  = request.form.get("current_password","").strip()
            stored_hash = cfg.get("app",{}).get("settings_password","")
            # Verifieer huidig wachtwoord als er al een is ingesteld
            if stored_hash and _hash_pw(current_pw) != stored_hash:
                flash("Huidig wachtwoord klopt niet.","danger")
                return redirect(url_for("settings"))
            if new_pw:
                if new_pw != confirm_pw:
                    flash("Wachtwoorden komen niet overeen.","danger")
                    return redirect(url_for("settings"))
                cfg.setdefault("app",{})["settings_password"] = _hash_pw(new_pw)
                flash("Wachtwoord ingesteld.","success")
                session["settings_auth"] = True  # direct ingelogd na instellen
            else:
                # Leeg wachtwoord = beveiliging uitzetten
                cfg.setdefault("app",{})["settings_password"] = ""
                session.pop("settings_auth", None)
                flash("Wachtwoordbeveiliging uitgeschakeld.","success")
        save_conf(cfg)
        if sec != "app":
            flash("Instellingen opgeslagen.","success")
        return redirect(url_for("settings"))

    account_info = None
    x = cfg.get("xtream",{})
    if x.get("server") and x.get("user") and x.get("pwd"):
        try: account_info = make_api(cfg).get_user_info()
        except Exception: pass
    return render_template("settings.html", cfg=cfg, account_info=account_info,
                           settings_locked=_settings_locked(cfg))

@app.get("/browse")
def browse():
    return render_template("browse.html", cfg=load_conf())

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
    films  = [{"path":f, "info":tmdb_movie(f,tk) if tk else {}} for f in films_raw]
    series = {sn: {"info":tmdb_series(sn,tk) if tk else {}, "seasons":group_seasons(eps)}
              for sn,eps in series_raw.items()}
    return render_template("library.html", films=films, series=series)

@app.get("/queue")
def queue_page():
    return render_template("queue.html")

# ── API: Xtream ───────────────────────────────────────────────────
def _sf(q):
    s = (q or "").strip().lower()
    return s in ("*","all") or len(s) >= 2

@app.get("/api/live")
def api_live():
    cfg=load_conf(); q=(request.args.get("q") or "").strip()
    try:
        if not _sf(q): return jsonify({"ok":True,"items":[],"cached_at":None,"hint":"Geef ≥2 tekens of '*'."})
        doc   = _gor("live.json",int(cfg["cache"]["ttl_hours"]),lambda:{"items":make_api(cfg).get_live_streams()})
        items = doc.get("items",[]); qn=q.lower()
        if qn not in ("*","all"): items=[x for x in items if qn in (x.get("name") or "").lower()]
        return jsonify({"ok":True,"items":items,"cached_at":doc.get("fetched_at")})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/movies")
def api_movies():
    cfg=load_conf(); q=(request.args.get("q") or "").strip()
    try:
        if not _sf(q): return jsonify({"ok":True,"items":[],"cached_at":None,"hint":"Geef ≥2 tekens of '*'."})
        doc   = _gor("movies.json",int(cfg["cache"]["ttl_hours"]),lambda:{"items":make_api(cfg).get_vod_streams()})
        items = doc.get("items",[]); qn=q.lower()
        if qn not in ("*","all"): items=[x for x in items if qn in (x.get("name") or "").lower()]
        return jsonify({"ok":True,"items":items,"cached_at":doc.get("fetched_at")})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/series")
def api_series():
    cfg=load_conf(); q=(request.args.get("q") or "").strip()
    try:
        if not _sf(q): return jsonify({"ok":True,"items":[],"cached_at":None,"hint":"Geef ≥2 tekens of '*'."})
        doc   = _gor("series.json",int(cfg["cache"]["ttl_hours"]),lambda:{"items":make_api(cfg).get_series()})
        items = doc.get("items",[]); qn=q.lower()
        if qn not in ("*","all"): items=[x for x in items if qn in (x.get("name") or "").lower()]
        return jsonify({"ok":True,"items":items,"cached_at":doc.get("fetched_at")})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/series/<series_id>")
def api_series_detail(series_id):
    cfg=load_conf()
    try:
        doc = _gor(f"series_{series_id}.json",int(cfg["cache"]["ttl_hours"]),
                   lambda:{"data":make_api(cfg).get_series_info(series_id)})
        return jsonify({"ok":True,"data":doc.get("data"),"cached_at":doc.get("fetched_at")})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.post("/api/refresh")
def api_refresh():
    cfg=load_conf(); scope=request.args.get("scope","all")
    try:
        api = make_api(cfg)
        with _LOCK:
            if scope in ("all","live"):   _wc("live.json",{"items":api.get_live_streams()})
            if scope in ("all","movies"): _wc("movies.json",{"items":api.get_vod_streams()})
            if scope in ("all","series"): _wc("series.json",{"items":api.get_series()})
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.post("/api/strm/live")
def api_strm_live():
    cfg=load_conf(); p=request.get_json(force=True) or {}
    sid=p.get("stream_id"); title=p.get("title") or f"Live {sid}"
    ext=pick_ext(p.get("ext"), cfg["ext"]["live"] or "ts")
    url=make_api(cfg).live_url(sid, ext)
    # Live heeft geen aparte SMB/FTP path — gebruik output live submap
    live_sub = cfg["output"]["live"]
    try:
        path = storage_write_strm(f"{live_sub}/{sanitize(title)}.strm", url, cfg)
        return jsonify({"ok":True,"path":path,"url":url})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.post("/api/strm/movie")
def api_strm_movie():
    cfg=load_conf(); p=request.get_json(force=True) or {}
    sid=p.get("stream_id")
    if not sid: return jsonify({"ok":False,"error":"Geen stream_id"}),400
    tmdb_id=title_raw=None
    cf=_cf("movies.json")
    if cf.exists():
        try:
            for it in (json.loads(cf.read_text()).get("items") or []):
                if str(it.get("stream_id"))==str(sid):
                    tmdb_id=(it.get("tmdb") or "").strip(); title_raw=it.get("name"); break
        except Exception: pass
    if not title_raw: title_raw=p.get("title") or f"Movie {sid}"
    ext=pick_ext(p.get("ext"), cfg["ext"]["movie"] or "mp4")
    url=make_api(cfg).vod_url(sid, ext)
    tmdb_key = cfg["tmdb"]["api_key"] if cfg["tmdb"].get("enabled") else ""
    tmdb_info = tmdb_by_id(tmdb_id, tmdb_key) if tmdb_id and tmdb_key else None
    sb = safe_fn(tmdb_info["title"]) if tmdb_info and tmdb_info.get("title") else sanitize(strip_prefix(title_raw))
    sub=storage_subdir(cfg,"movies")
    try:
        path=storage_write_strm(f"{sub}/{sb}/{sb}.strm", url, cfg)
        return jsonify({"ok":True,"path":path,"tmdb":tmdb_id or None,"url":url})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.post("/api/strm/series")
def api_strm_series():
    cfg=load_conf(); p=request.get_json(force=True) or {}
    sid=p.get("series_id"); forced_ext=p.get("ext") or cfg["ext"]["episode"]
    sub=storage_subdir(cfg,"series")
    try:
        api  = make_api(cfg); data=api.get_series_info(sid)
        sinfo= data.get("info") or {}
        stmdb= (sinfo.get("tmdb_id") or sinfo.get("tmdb") or "").strip()
        if not stmdb:
            sc = _cf("series.json")
            if sc.exists():
                try:
                    for it in (json.loads(sc.read_text()).get("items") or []):
                        if str(it.get("series_id"))==str(sid):
                            stmdb=(it.get("tmdb") or "").strip(); break
                except Exception: pass
        tmdb_key = cfg["tmdb"]["api_key"] if cfg["tmdb"].get("enabled") else ""
        tmdb_info = tmdb_series_by_id(stmdb, tmdb_key) if stmdb and tmdb_key else None
        sname = safe_fn(tmdb_info["title"]) if tmdb_info and tmdb_info.get("title") \
                else sanitize(strip_prefix(sinfo.get("name") or sinfo.get("title") or f"Series {sid}"))
        eps  = data.get("episodes") or {}
        if not eps: return jsonify({"ok":False,"error":"Geen afleveringen"}),404
        created=0; errors=[]
        for sk,eplist in sorted(eps.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
            try: sn=int(sk)
            except ValueError: sn=0
            for ep in (eplist or []):
                en=ep.get("episode_num") or ep.get("episode") or 0
                try: en=int(en)
                except Exception: en=0
                eid=ep.get("id") or ep.get("episode_id") or ep.get("stream_id")
                ext=pick_ext(ep.get("container_extension") or forced_ext,"mp4")
                try:
                    storage_write_strm(
                        f"{sub}/{sname}/{sname} S{sn:02d}E{en:02d}.strm",
                        api.episode_url(eid, ext), cfg, auto_postprocess=False, jellyfin_push=False)
                    created += 1
                except Exception as ex: errors.append(str(ex))
        if storage_mode(cfg) == "mount":
            postprocess_series(cfg)
        # Jellyfin eenmalig na alle afleveringen
        try: jf_refresh(cfg, False)
        except Exception: pass
        return jsonify({"ok":True,"count":created,"errors":errors,"path":f"{sub}/{sname}"})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.post("/api/strm/batch")
def api_strm_batch():
    cfg=load_conf(); payload=request.get_json(force=True) or {}
    kind=payload.get("kind","movie"); ids=payload.get("ids",[])
    if not ids: return jsonify({"ok":False,"error":"Geen IDs opgegeven"}),400
    created=0; errors=[]; api=make_api(cfg)
    movies_sub=storage_subdir(cfg,"movies"); series_sub=storage_subdir(cfg,"series")
    cf=_cf("movies.json"); movie_cache={}
    if cf.exists():
        try:
            for it in (json.loads(cf.read_text()).get("items") or []):
                movie_cache[str(it.get("stream_id"))]=it
        except Exception: pass
    tmdb_key = cfg["tmdb"]["api_key"] if cfg["tmdb"].get("enabled") else ""
    for sid in ids:
        try:
            sid=str(sid)
            if kind=="movie":
                it=movie_cache.get(sid,{}); title_raw=it.get("name") or f"Movie {sid}"
                tmdb_id=(it.get("tmdb") or "").strip()
                ext=pick_ext(it.get("container_extension"),cfg["ext"]["movie"] or "mp4")
                url=api.vod_url(sid,ext)
                tmdb_info = tmdb_by_id(tmdb_id, tmdb_key) if tmdb_id and tmdb_key else None
                sb = safe_fn(tmdb_info["title"]) if tmdb_info and tmdb_info.get("title") \
                     else sanitize(strip_prefix(title_raw))
                storage_write_strm(f"{movies_sub}/{sb}/{sb}.strm", url, cfg, auto_postprocess=False, jellyfin_push=False)
                created+=1
            elif kind=="series":
                data=api.get_series_info(sid)
                sinfo=(data.get("info") or {})
                stmdb=(sinfo.get("tmdb_id") or sinfo.get("tmdb") or "").strip()
                if not stmdb:
                    for it in (json.loads(_cf("series.json").read_text()).get("items") or []) \
                              if _cf("series.json").exists() else []:
                        if str(it.get("series_id"))==str(sid):
                            stmdb=(it.get("tmdb") or "").strip(); break
                tmdb_sinfo = tmdb_series_by_id(stmdb, tmdb_key) if stmdb and tmdb_key else None
                sname = safe_fn(tmdb_sinfo["title"]) if tmdb_sinfo and tmdb_sinfo.get("title") \
                        else sanitize(strip_prefix(sinfo.get("name") or sinfo.get("title") or f"Series {sid}"))
                eps=data.get("episodes") or {}
                if not eps: errors.append(f"Geen afleveringen voor {sid}"); continue
                for sk,eplist in sorted(eps.items(),key=lambda kv:int(kv[0]) if str(kv[0]).isdigit() else 0):
                    try: sn=int(sk)
                    except ValueError: sn=0
                    for ep in (eplist or []):
                        en=ep.get("episode_num") or ep.get("episode") or 0
                        try: en=int(en)
                        except Exception: en=0
                        eid=ep.get("id") or ep.get("episode_id") or ep.get("stream_id")
                        ext=pick_ext(ep.get("container_extension") or cfg["ext"]["episode"],"mp4")
                        storage_write_strm(
                            f"{series_sub}/{sname}/{sname} S{sn:02d}E{en:02d}.strm",
                            api.episode_url(eid,ext), cfg, auto_postprocess=False, jellyfin_push=False)
                        created+=1
        except Exception as ex: errors.append(f"{sid}: {ex}")
    if storage_mode(cfg) == "mount" and created > 0:
        if kind=="movie": postprocess_movies(cfg)
        else:             postprocess_series(cfg)
    # Jellyfin eenmalig na alle items
    if created > 0:
        try: jf_refresh(cfg, kind=="movie")
        except Exception: pass
    return jsonify({"ok":True,"created":created,"errors":errors})

@app.post("/api/library/refresh")
def api_library_refresh():
    return jsonify({"ok":True})

# ── API: Queue ────────────────────────────────────────────────────
@app.get("/api/queue")
def api_queue(): return jsonify(download_queue.get_state())

@app.delete("/api/queue/history/<iid>")
def api_del_hist(iid): download_queue.delete_history(iid); return jsonify({"ok":True})

@app.delete("/api/queue/history")
def api_clr_hist(): download_queue.clear_history(); return jsonify({"ok":True})

@app.post("/api/queue/retry/<iid>")
def api_retry(iid): download_queue.retry(iid); return jsonify({"ok":True})

@app.post("/api/queue/cancel")
def api_cancel(): return jsonify({"ok": download_queue.cancel()})

# ── API: Postprocessing ───────────────────────────────────────────
@app.post("/api/postprocess")
def api_postprocess():
    cfg=load_conf(); scope=(request.get_json(force=True) or {}).get("scope","all")
    if storage_mode(cfg) != "mount":
        return jsonify({"ok":True,"results":{},
                        "info":"SMB/FTP mode: namen worden gecleand bij aanmaken."})
    res={}
    if scope in ("all","movies"): res["movies"]=postprocess_movies(cfg)
    if scope in ("all","series"): res["series"]=postprocess_series(cfg)
    return jsonify({"ok":True,"results":res})


# ── API: Tests ────────────────────────────────────────────────────
@app.get("/discover")
def discover():
    return render_template("discover.html", cfg=load_conf())

@app.get("/api/discover")
def api_discover():
    cfg = load_conf()
    key = cfg.get("tmdb",{}).get("api_key","")
    if not key:
        return jsonify({"ok": False, "error": "Geen TMDB API key ingesteld. Voeg deze toe via Instellingen → TMDB."}), 400
    try:
        data = tmdb_discover(key)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

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
def api_test_smb(): return jsonify(smb_test(load_conf()))

@app.post("/api/test/ftp")
def api_test_ftp(): return jsonify(ftp_test(load_conf()))

@app.get("/api/storage/info")
def api_storage_info():
    cfg  = load_conf()
    mode = storage_mode(cfg)
    if not cfg.get("storage", {}).get("show_gauge", True):
        return jsonify({"mode": mode, "supported": False, "disabled": True})
    info = storage_free_space(cfg)
    out  = {"mode": mode, "supported": info is not None}
    if info:
        out.update(info)
    if mode == "ftp":
        out["message"] = "Schijfruimte opvragen is niet beschikbaar bij FTP."
    elif mode == "smb" and info is None:
        if not shutil.which("smbclient"):
            out["message"] = "Installeer smbclient om schijfruimte op te vragen (apt install smbclient)."
        else:
            out["message"] = "Schijfruimte kon niet worden opgehaald van de SMB share."
    elif mode == "mount" and info is None:
        out["message"] = "Schijfruimte kon niet worden bepaald."
    return jsonify(out)

@app.post("/api/test/tmdb")
def api_test_tmdb():
    cfg=load_conf(); key=cfg.get("tmdb",{}).get("api_key","")
    if not key: return jsonify({"ok":False,"error":"Geen API key ingevuld"})
    try:
        r=_requests.get("https://api.themoviedb.org/3/configuration",params={"api_key":key},timeout=8)
        if r.ok: return jsonify({"ok":True,"message":"TMDB verbinding geslaagd"})
        return jsonify({"ok":False,"error":f"HTTP {r.status_code} – ongeldige API key?"})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.get("/api/tmdb/discover")
def api_tmdb_discover():
    """Haal TMDB lijsten op: trending, popular, top_rated voor films en series."""
    cfg  = load_conf()
    key  = cfg.get("tmdb",{}).get("api_key","")
    if not key:
        return jsonify({"ok": False, "error": "Geen TMDB API key ingesteld.", "setup": True}), 400

    kind      = request.args.get("kind", "movie")   # movie | tv
    list_type = request.args.get("list", "trending")
    page      = max(1, min(5, int(request.args.get("page", 1) or 1)))

    # "upcoming" bestaat niet voor TV — gebruik "airing_today"
    if kind == "tv" and list_type == "upcoming":
        list_type = "airing_today"

    cache_name = f"tmdb_{kind}_{list_type}_p{page}.json"
    ttl = max(12, int(cfg["cache"]["ttl_hours"]))  # min 12 uur voor trending data

    def _fetch():
        if list_type == "trending":
            url = f"https://api.themoviedb.org/3/trending/{kind}/week"
        elif list_type in ("popular","top_rated","upcoming","now_playing","on_the_air","airing_today"):
            url = f"https://api.themoviedb.org/3/{kind}/{list_type}"
        else:
            return {"items": [], "page": page, "total_pages": 1}

        r = _requests.get(url, params={"api_key": key, "language": "nl-NL", "page": page}, timeout=8)
        if not r.ok:
            raise RuntimeError(f"TMDB fout: HTTP {r.status_code}")

        data    = r.json()
        results = data.get("results", [])
        total   = data.get("total_pages", 1)

        items = []
        for item in results:
            items.append({
                "tmdb_id":  item.get("id"),
                "title":    item.get("title") or item.get("name", ""),
                "overview": (item.get("overview") or "")[:300],
                "rating":   round(item.get("vote_average", 0), 1),
                "votes":    item.get("vote_count", 0),
                "year":     (item.get("release_date") or item.get("first_air_date", ""))[:4],
                "poster":   ("https://image.tmdb.org/t/p/w342" + item["poster_path"])
                            if item.get("poster_path") else None,
                "backdrop": ("https://image.tmdb.org/t/p/w780" + item["backdrop_path"])
                            if item.get("backdrop_path") else None,
                "kind":     kind,
            })
        return {"items": items, "page": page, "total_pages": min(total, 5)}

    try:
        doc = _gor(cache_name, ttl, _fetch)
        return jsonify({"ok": True, "items": doc.get("items", []),
                        "page": doc.get("page", page),
                        "total_pages": doc.get("total_pages", 1),
                        "cached_at": doc.get("fetched_at")})
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500

@app.post("/api/test/opensubtitles")
def api_test_opensubtitles():
    cfg=load_conf(); o=cfg.get("opensubtitles",{})
    if not o.get("api_key"): return jsonify({"ok":False,"error":"Geen API key ingevuld"})
    global _os_token; _os_token=None
    token=os_login(cfg)
    if token: return jsonify({"ok":True,"message":"OpenSubtitles login geslaagd"})
    return jsonify({"ok":False,"error":"Login mislukt – controleer credentials"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","8080")), debug=False)
