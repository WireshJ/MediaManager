from __future__ import annotations
import os, json, re, datetime, threading, uuid, time, subprocess
from typing import Any, Dict, Optional, List
from collections import defaultdict, OrderedDict
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from pathlib import Path
import requests as _requests

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("APP_SECRET", "mediamanager-secret-2026")

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
_PFX_RE  = re.compile(r'^\|[A-Za-z]{2,3}\|\s*|^[A-Za-z]{2,3}\s*-\s*')
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
        new_name    = safe_fn(strip_prefix(folder_name))
        new_folder  = series_dir / new_name

        if folder != new_folder:
            if new_folder.exists():
                results["errors"].append(f"Doelmap bestaat al: {new_name}")
            else:
                try:
                    folder.rename(new_folder)
                    results["renamed"].append(f"{folder_name} → {new_name}")
                    folder = new_folder
                except Exception as e:
                    results["errors"].append(str(e)); continue
        else:
            results["skipped"].append(folder_name)

        for f in sorted(folder.iterdir()):
            if not f.is_file(): continue
            stem = f.stem; ext = f.suffix
            cleaned_stem = safe_fn(strip_prefix(stem))
            # Verwijder (US),(JP) etc. suffix vóór de extensie
            cleaned_stem = re.sub(r'\s*\([A-Za-z]{2}\)$','',cleaned_stem).strip()
            cleaned = cleaned_stem + ext
            if cleaned != f.name:
                tgt = folder / cleaned
                if not tgt.exists():
                    try: f.rename(tgt)
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

def scan_local(cfg):
    base = Path(cfg["output"]["base"])
    mdir = base / cfg["output"]["movies"]
    sdir = base / cfg["output"]["series"]
    films = []
    if mdir.exists():
        for root,_,files in os.walk(mdir):
            for f in files:
                if f.endswith(".strm"): films.append(os.path.join(root,f))
    series_dict: Dict[str,List] = {}
    if sdir.exists():
        for root,_,files in os.walk(sdir):
            for f in files:
                if f.endswith(".strm"):
                    rel = Path(root).relative_to(sdir).parts
                    sname = rel[0] if rel else "Overig"
                    series_dict.setdefault(sname,[]).append(os.path.join(root,f))
    return films, series_dict

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
                    os_download(title,lang,dest,cfg,season,episode)

            jf_refresh(cfg,is_film)

            # Postprocessing
            e["status"]="postprocessing..."
            if is_film: postprocess_movies(cfg)
            else: postprocess_series(cfg)

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

@app.route("/library",methods=["GET","POST"])
def library():
    cfg=load_conf()
    if request.method=="POST":
        sel=request.form.getlist("selected_files")
        if sel:
            download_queue.add(sel)
            flash(f"⏳ {len(sel)} bestand(en) toegevoegd aan de download queue.","success")
        else:
            flash("⚠️ Geen bestanden geselecteerd.","warning")
        return redirect(url_for("library"))

    films_raw,series_raw=scan_local(cfg)
    tk=cfg["tmdb"]["api_key"] if cfg["tmdb"].get("enabled") else ""
    base_series=str(out_path(cfg,"series"))+os.sep

    films=[{"path":f,"info":tmdb_movie(f,tk) if tk else {}} for f in films_raw]
    series={sn:{"info":tmdb_series(sn,tk) if tk else {},"seasons":group_seasons(eps)}
            for sn,eps in series_raw.items()}

    return render_template("library.html",films=films,series=series,BASE_SERIES_PATH=base_series)

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
    cfg=load_conf(); p=request.get_json(force=True) or {}
    sid=p.get("stream_id"); title=p.get("title") or f"Live {sid}"
    ext=pick_ext(p.get("ext"),cfg["ext"]["live"] or "ts")
    url=make_api(cfg).live_url(sid,ext)
    dest=out_path(cfg,"live")/(sanitize(title)+".strm")
    write_strm(dest,url)
    return jsonify({"ok":True,"path":str(dest),"url":url})

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
    ext=pick_ext(p.get("ext"),cfg["ext"]["movie"] or "mp4")
    url=make_api(cfg).vod_url(sid,ext)
    sb=sanitize(f"{tmdb_id} - {sanitize(title_raw)}") if tmdb_id else sanitize(title_raw)
    d=out_path(cfg,"movies")/sb; d.mkdir(parents=True,exist_ok=True)
    write_strm(d/f"{sb}.strm",url)
    return jsonify({"ok":True,"path":str(d/f"{sb}.strm"),"tmdb":tmdb_id or None,"url":url})

@app.post("/api/strm/series")
def api_strm_series():
    cfg=load_conf(); p=request.get_json(force=True) or {}
    sid=p.get("series_id"); forced_ext=p.get("ext") or cfg["ext"]["episode"]
    try:
        api=make_api(cfg); data=api.get_series_info(sid)
        sname=sanitize((data.get("info") or {}).get("name") or (data.get("info") or {}).get("title") or f"Series {sid}")
        eps=data.get("episodes") or {}
        if not eps: return jsonify({"ok":False,"error":"Geen afleveringen"}),404
        created=0; root=out_path(cfg,"series")/sname; root.mkdir(parents=True,exist_ok=True)
        for sk,eplist in sorted(eps.items(),key=lambda kv:int(kv[0]) if str(kv[0]).isdigit() else 0):
            try: sn=int(sk)
            except ValueError: sn=0
            for ep in (eplist or []):
                en=ep.get("episode_num") or ep.get("episode") or 0
                try: en=int(en)
                except Exception: en=0
                eid=ep.get("id") or ep.get("episode_id") or ep.get("stream_id")
                ext=pick_ext(ep.get("container_extension") or forced_ext,"mp4")
                write_strm(root/f"{sname} S{sn:02d}E{en:02d}.strm", api.episode_url(eid,ext))
                created+=1
        return jsonify({"ok":True,"count":created,"path":str(root)})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

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

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","8080")),debug=False)
