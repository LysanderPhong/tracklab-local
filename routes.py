"""Approximate WGS-84 track traced from the attributed satellite image.

Image export bounds are degrees, 1200 x 1200 pixels. These are visual
estimates, not surveyed lanes or the school's geofence.
"""
from bisect import bisect_right
import math

BOUNDS = (109.999, 18.385, 110.029, 18.415)
# Closed cubic curves, in the original image's pixel coordinates.
CURVES = [
    ((559.0, 534.0), (555.0, 517.0), (579.0, 511.0), (587.0, 528.0)),
    ((587.0, 528.0), (591.0, 540.0), (595.0, 559.0), (593.0, 568.0)),
    ((593.0, 568.0), (591.0, 583.0), (569.0, 581.0), (563.0, 566.0)),
    ((563.0, 566.0), (560.0, 556.0), (559.0, 543.0), (559.0, 534.0)),
]


def to_coordinate(x, y):
    west, south, east, north = BOUNDS
    return north - y / 1200 * (north - south), west + x / 1200 * (east - west)


def metres(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(h)))


def _track():
    points = []
    for controls in CURVES:
        for step in range(32):
            t = step / 32
            weights = ((1-t)**3, 3*(1-t)**2*t, 3*(1-t)*t*t, t**3)
            x, y = (sum(p[axis]*w for p, w in zip(controls, weights)) for axis in (0, 1))
            lat, lon = to_coordinate(x, y)
            points.append({"x": x, "y": y, "latitude": lat, "longitude": lon})
    return points + [points[0].copy()]


TRACK = _track()
DISTANCES = [0.0]
for a, b in zip(TRACK, TRACK[1:]):
    DISTANCES.append(DISTANCES[-1] + metres((a['latitude'], a['longitude']), (b['latitude'], b['longitude'])))
LAP_METRES = DISTANCES[-1]


def position(distance, plan=None):
    track = plan.get('track', TRACK) if plan else TRACK
    lengths = plan.get('lengths', DISTANCES) if plan else DISTANCES
    offset = distance % lengths[-1]
    index = min(len(track)-2, bisect_right(lengths, offset)-1)
    fraction = (offset-lengths[index]) / (lengths[index+1]-lengths[index])
    return {key: track[index][key] + fraction*(track[index+1][key]-track[index][key]) for key in track[0]}


def _number(value, lower, upper, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name}应在 {lower}–{upper} 之间。")
    return float(value)


def build_plan(data, demo=False):
    km = _number(data.get('distance_km', 1), 1, 5, '距离（km）')
    pace = _number(data.get('pace', 8), 3, 12, '配速（分钟/km）')
    variation = data.get('variation', True)
    if not isinstance(variation, bool):
        raise ValueError('配速变化选项无效。')
    amplitude = min(.45, pace-3, 12-pace) if variation else 0
    t, distance = 0., 0.
    samples = [{"t": 0., "m": 0., "pace": pace}]
    while distance < km*1000 - 1e-8:
        p = pace + amplitude*(.65*math.sin(2*math.pi*(t+.5)/73) + .35*math.sin(2*math.pi*(t+.5)/127))
        speed = 1000 / (60*p)
        dt = min(1., (km*1000-distance)/speed)
        if demo:
            dt = min(dt, 20-t)
        distance += speed*dt
        t += dt
        samples.append({"t": t, "m": distance, "pace": p})
        if demo and t >= 20:
            break
    geometry = loop_geometry(data['points']) if 'points' in data else {}
    return {"mode": "route", "distance_km": km, "pace": pace, "variation": variation, "demo": demo,
            "duration": t, "metres": distance, "lap_metres": LAP_METRES,
            "samples": samples, **geometry}


def frame(plan, elapsed):
    if plan.get('mode') == 'fixed':
        return {'elapsed': max(0., min(plan['duration'], elapsed)), 'distance': 0.,
                'pace': 0., 'latitude': plan['latitude'], 'longitude': plan['longitude']}
    samples = plan['samples']
    elapsed = max(0., min(plan['duration'], elapsed))
    # Samples occur every second except the final partial second.
    index = min(int(elapsed), len(samples)-2)
    a, b = samples[index:index+2]
    fraction = min(1., max(0., (elapsed-a['t'])/(b['t']-a['t'])))
    distance = a['m'] + (b['m']-a['m'])*fraction
    return {"elapsed": elapsed, "distance": distance, "pace": b['pace'], **position(distance, plan)}


def loop_geometry(points):
    if not isinstance(points, list) or not 4 <= len(points) <= 256:
        raise ValueError('路线需要 4–256 个点，按跑道顺序选点后闭合。')
    clean = []
    for p in points:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError('路线点格式无效。')
        pair = [_number(p[0], -85, 85, '纬度'), _number(p[1], -180, 180, '经度')]
        if not clean or metres(clean[-1], pair) >= .1:
            clean.append(pair)
    if len(clean) < 4:
        raise ValueError('路线有效点不足，请沿跑道选点。')
    if metres(clean[0], clean[-1]) >= .1:
        clean.append(clean[0][:])
    else:
        clean[-1] = clean[0][:]
    if len(clean)<5:
        raise ValueError('请至少选择 4 个不同的路线点。')
    if max(p[1] for p in clean)-min(p[1] for p in clean) > 1:
        raise ValueError('路线跨度过大。')
    lengths = [0.]
    for a, b in zip(clean, clean[1:]):
        length = metres(a, b)
        if length > 500 or length < .01:
            raise ValueError('相邻点应小于 500 米且不能重合，请补充跑道转弯处的点。')
        lengths.append(lengths[-1]+length)
    if not 50 <= lengths[-1] <= 5000:
        raise ValueError('单圈长度应为 50–5000 米，请检查路线。')
    return {'track': [dict(latitude=p[0], longitude=p[1]) for p in clean],
            'lengths': lengths, 'lap_metres': lengths[-1]}


def route_payload(data):
    if 'points' not in data:
        raise ValueError('请先选择并确认一条跑道路线。')
    demo = data.get('demo', False)
    if not isinstance(demo, bool):
        raise ValueError('试播选项无效。')
    plan = build_plan(data, demo)
    # Keep the payload bounded; the receiver reconstructs the same plan.
    return {'points': [[p['latitude'], p['longitude']] for p in plan['track'][:-1]],
            'distance_km': plan['distance_km'], 'pace': plan['pace'],
            'variation': plan['variation'], 'demo': demo}


def fixed_plan(data):
    return {'mode': 'fixed', 'latitude': _number(data.get('latitude'), -90, 90, '纬度'),
            'longitude': _number(data.get('longitude'), -180, 180, '经度'),
            'duration': _number(data.get('seconds', 300), 20, 1800, '持续秒数'),
            'metres': 0., 'demo': False}


def preview(plan):
    return {key: value for key, value in plan.items() if key not in {'samples', 'lengths'}} | {
        "track": plan.get('track', TRACK),
        "pace_min": min(s['pace'] for s in plan['samples']),
        "pace_max": max(s['pace'] for s in plan['samples']),
    }
