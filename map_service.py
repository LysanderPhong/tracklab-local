"""Small-use map gateway: fixed upstreams, bounded cache, no request logging."""
from collections import OrderedDict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import routes
from relay import RelayError

CACHE = Path(os.environ.get('TRACKLAB_MAP_CACHE', Path(__file__).resolve().parent/'runtime'/'map-cache'))
PHOTON = os.environ.get('TRACKLAB_PHOTON_URL', 'https://photon.komoot.io/api/').rstrip('/')+'/'
IDENTITY = os.environ.get('TRACKLAB_MAP_CONTACT', 'TrackLab/0.4 (small private location-testing workbench)')
search_cache = OrderedDict()
search_lock = threading.Lock()
last_search = 0.
tile_locks = [threading.Lock() for _ in range(16)]
cache_lock = threading.Lock()
track_cache = OrderedDict()
track_lock = threading.Lock()
last_tracks = 0.


def tracks(latitude, longitude):
    global last_tracks
    try:
        lat, lon = float(latitude), float(longitude)
        if not math.isfinite(lat) or not math.isfinite(lon) or abs(lat)>85 or abs(lon)>180:
            raise ValueError()
    except (ValueError, TypeError):
        raise RelayError('请先在地图上选择操场附近的位置。') from None
    key = (round(lat, 4), round(lon, 4))
    with track_lock:
        now = time.monotonic()
        if key in track_cache and now-track_cache[key][0]<600:
            return track_cache[key][1]
        if now-last_tracks<3:
            raise RelayError('跑道查询稍快，请三秒后再试。', 429)
        last_tracks = now
        query = (f'[out:json][timeout:8];way(around:1500,{lat:.6f},{lon:.6f})'
                 '[leisure=track][sport~"running|athletics"];out tags geom 40;')
        url = os.environ.get('TRACKLAB_OVERPASS_URL', 'https://overpass-api.de/api/interpreter')
        raw, _ = fetch(url+'?'+urllib.parse.urlencode({'data':query}), 1500000, 'application/json')
        try:
            found = []
            for item in json.loads(raw).get('elements', [])[:40]:
                points = [[p['lat'],p['lon']] for p in item.get('geometry', [])]
                if len(points)<4 or routes.metres(points[0],points[-1])>5:
                    continue  # do not invent a closing segment for an open OSM way
                if len(points)>256:
                    points = points[::math.ceil(len(points)/255)]+[points[-1]]
                try:
                    g = routes.loop_geometry(points)
                except ValueError:
                    continue
                found.append({'name': str(item.get('tags',{}).get('name','未命名跑道'))[:160],
                              'points': [[p['latitude'],p['longitude']] for p in g['track'][:-1]],
                              'lap_metres': round(g['lap_metres'],1), 'osm_id': item.get('id')})
            result = {'tracks':found,'provider':'OpenStreetMap / Overpass',
                      'message':'请在地图上检查跑道后确认。' if found else '附近没有收录的闭合跑道，请手动画线或使用可调整的椭圆草图。'}
        except (KeyError, ValueError, TypeError, IndexError):
            raise RelayError('跑道数据格式异常，请使用手动画线。', 502) from None
        track_cache[key]=(now,result)
        while len(track_cache)>64:
            track_cache.popitem(last=False)
        return result


def fetch(url, maximum, accept):
    request = urllib.request.Request(url, headers={'User-Agent': IDENTITY, 'Accept': accept})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read(maximum+1)
            if len(body) > maximum:
                raise RelayError('地图服务响应过大，请稍后重试。', 502)
            return body, response.headers
    except (OSError, urllib.error.URLError) as exc:
        raise RelayError('地图服务暂时未连接，请稍后重试，或使用已收藏地点。', 503) from exc


def search(query, provider='photon'):
    global last_search
    query = ' '.join(query.split())
    if not 2 <= len(query) <= 120:
        raise RelayError('请输入 2–120 个字的地点名称。')
    if provider not in {'photon', 'nominatim'}:
        raise RelayError('请选择支持的搜索服务。')
    key = hashlib.sha256((provider+'|'+query).encode()).hexdigest()
    with search_lock:
        now = time.monotonic()
        cached = search_cache.get(key)
        if cached and now-cached[0] < 600:
            return cached[1]
        if now-last_search < 1:
            raise RelayError('搜索稍快，请一秒后再试。', 429)
        last_search = now
        endpoint = PHOTON if provider == 'photon' else os.environ.get('TRACKLAB_NOMINATIM_URL', 'https://nominatim.openstreetmap.org/search')
        params = {'q': query, 'limit': 20}
        if provider == 'nominatim':
            params.update(format='jsonv2', addressdetails=1, **{'accept-language': 'zh-CN'})
        raw, _ = fetch(endpoint+'?'+urllib.parse.urlencode(params), 500000, 'application/json')
        try:
            decoded = json.loads(raw)
            results = []
            items = decoded['features'] if provider == 'photon' else decoded
            for feature in items[:20]:
                if provider == 'photon':
                    lon, lat = feature['geometry']['coordinates'][:2]
                    p = feature['properties']
                    name = str(p.get('name') or p.get('street') or '地图地点')[:160]
                    parts = [str(p[k]) for k in ('country', 'state', 'city', 'district', 'street') if p.get(k)]
                    address = ' · '.join(dict.fromkeys(parts))[:300]
                else:
                    lat, lon = float(feature['lat']), float(feature['lon'])
                    address = str(feature.get('display_name', ''))[:300]
                    name = str(feature.get('name') or address.split(',')[0] or '地图地点')[:160]
                if not all(type(v) in (float, int) and math.isfinite(v) for v in (lat, lon)) or abs(lat)>90 or abs(lon)>180:
                    continue
                results.append(dict(name=name, address=address, latitude=lat, longitude=lon))
        except (KeyError, TypeError, ValueError):
            raise RelayError('搜索服务返回格式异常，请稍后重试。', 502) from None
        result = {'results': results, 'provider': provider+' / OpenStreetMap'}
        search_cache[key] = (now, result)
        while len(search_cache) > 256:
            search_cache.popitem(last=False)
        return result


def tile(path):
    match = re.fullmatch(r'/tiles/(\d{1,2})/(\d{1,6})/(\d{1,6})\.png', path)
    if not match:
        raise RelayError('地图图块地址无效。', 404)
    z, x, y = map(int, match.groups())
    if not 0 <= z <= 19 or not (0 <= x < 2**z and 0 <= y < 2**z):
        raise RelayError('地图范围无效。', 404)
    key = f'{z}-{x}-{y}'
    with tile_locks[hash(key) % len(tile_locks)]:
        CACHE.mkdir(parents=True, exist_ok=True)
        file = CACHE/(key+'.png')
        expiry = file.with_suffix('.expires')
        try:
            valid_until = float(expiry.read_text())
        except (OSError, ValueError):
            valid_until = file.stat().st_mtime+604800 if file.exists() else 0
        if file.exists() and time.time() < valid_until:
            return file.read_bytes()
        raw, headers = fetch(f'https://tile.openstreetmap.org/{z}/{x}/{y}.png', 1000000, 'image/png')
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise RelayError('底图服务暂时不可用。', 502)
        # Cache at least seven days per OSM policy; no prefetch/offline download.
        with cache_lock:
            files = list(CACHE.glob('*.png'))
            if sum(p.stat().st_size for p in files)+len(raw) > 128*1024*1024:
                # Refuse new tiles at capacity rather than evict fresh tiles
                # and repeatedly burden the public service.
                raise RelayError('地图缓存已满，请管理员清理过期图块。', 503)
            tmp = file.with_suffix('.tmp')
            tmp.write_bytes(raw)
            tmp.replace(file)
            max_age = re.search(r'max-age=(\d+)', headers.get('Cache-Control', ''))
            ttl = max(604800, int(max_age[1])) if max_age else 604800
            expiry.write_text(str(time.time()+ttl))
        return raw
