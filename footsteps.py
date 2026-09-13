"""footsteps — a day-by-day record of where the library's photos were taken, full span.

Record only. Every location names its provenance. Empty fields stay empty.

    python3 footsteps.py                 # full run (all years, newest year first)
    python3 footsteps.py --no-vision     # everything except the Claude content labels
    python3 footsteps.py --years 2026 2025

Reads all.json (osxphotos query --json --not-shared) and inferred.json (infer.py) in the
repo directory. Writes to ~/review/photo-geo/footsteps/ by default (--out to change).
"""
import argparse, base64, collections, csv, io, json, math, os, statistics, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
STAY_KM = 30.0                 # consecutive days within this radius = one stay
FLIGHT_KM = 300.0              # repo's great-circle model: anchors this far apart = a flight leg
MAX_GAP_DAYS = 7               # a photo-less gap longer than this ends the stay; those nights are unplaced
HOME = (3.1390, 101.6869)      # Kuala Lumpur city centre; "away" = a stay centred > STAY_KM from here
HOME_NAME = 'Kuala Lumpur'
VISION_MODEL = 'claude-sonnet-5'
VISION_PER_DAY = 6
VISION_MAX_PX = 800

# ---------------------------------------------------------------- geometry
def hav(a, b):
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))

def centroid(pts):
    return (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts))

def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=None)   # local wall clock as Photos stores it

# ---------------------------------------------------------------- reverse geocoder (offline)
_rg = None
def geocode(lat, lon):
    global _rg
    if _rg is None:
        import reverse_geocoder, pycountry
        _rg = (reverse_geocoder, pycountry)
    rg, pc = _rg
    r = rg.search([(lat, lon)], mode=1)[0]
    c = pc.countries.get(alpha_2=r['cc'])
    return dict(name=r['name'], city=r['name'], country=c.name if c else r['cc'], cc=r['cc'])

# ---------------------------------------------------------------- load
def load_items(no_infer=False):
    allp = os.path.join(HERE, 'all.json')
    infp = os.path.join(HERE, 'inferred.json')
    d = json.load(open(allp))
    if not no_infer and (not os.path.exists(infp) or os.path.getmtime(infp) < os.path.getmtime(allp)):
        print('running infer.py (inferred.json missing or older than all.json)', flush=True)
        subprocess.run([sys.executable, os.path.join(HERE, 'infer.py')], cwd=HERE, check=True)
    inferred = {x['uuid']: x for x in json.load(open(infp))} if os.path.exists(infp) else {}
    items = []
    for p in d:
        if p.get('intrash') or not p.get('date'):
            continue
        ex = p.get('exif_info') or {}
        pl = p.get('place') or {}
        ad = pl.get('address') or {}
        it = dict(
            uuid=p['uuid'], filename=p['original_filename'], date=p['date'], day=p['date'][:10],
            year=int(p['date'][:4]), t=ts(p['date']),
            movie=bool(p.get('ismovie')), screenshot=bool(p.get('screenshot')),
            favorite=bool(p.get('favorite')), title=p.get('title') or '', description=p.get('description') or '',
            albums=sorted(set(p.get('albums') or [])), keywords=sorted(set(p.get('keywords') or [])),
            persons=sorted(set(x for x in (p.get('persons') or []) if x and x != '_UNKNOWN_')),
            camera=ex.get('camera_model') or '', camera_make=ex.get('camera_make') or '',
            place_name=pl.get('name') or '', place_city=ad.get('city') or '', place_country=ad.get('country') or '',
            place_cc=pl.get('country_code') or '', derivatives=p.get('path_derivatives') or [],
            apple_labels=sorted(set(x.strip().lower() for x in (p.get('labels') or []) if x)),
            lat=None, lon=None, provenance='none', method='', confidence='',
        )
        if p.get('latitude') is not None and p.get('longitude') is not None:
            it.update(lat=p['latitude'], lon=p['longitude'], provenance='exif')
        elif p['uuid'] in inferred:
            x = inferred[p['uuid']]
            it.update(lat=x['lat'], lon=x['lon'], provenance='inferred', method=x['method'],
                      confidence=x.get('confidence', ''), tier=x.get('tier', ''))
        items.append(it)
    items.sort(key=lambda i: i['t'])
    return items

# ---------------------------------------------------------------- days
def day_clusters(positioned):
    """Split one day's positioned items (time order) into location clusters > STAY_KM apart."""
    clusters = []
    for it in positioned:
        p = (it['lat'], it['lon'])
        if clusters and hav(centroid(clusters[-1]['pts']), p) <= STAY_KM:
            clusters[-1]['pts'].append(p); clusters[-1]['items'].append(it)
        else:
            clusters.append(dict(pts=[p], items=[it]))
    for c in clusters:
        c['centre'] = centroid(c['pts'])
        c['first'] = c['items'][0]['t']; c['last'] = c['items'][-1]['t']
        c['provenance'] = 'exif' if any(i['provenance'] == 'exif' for i in c['items']) else 'inferred'
        place_items = [i for i in c['items'] if i['place_country']]
        if place_items:
            city = collections.Counter(i['place_city'] for i in place_items if i['place_city']).most_common(1)
            country = collections.Counter(i['place_country'] for i in place_items).most_common(1)[0][0]
            c.update(city=city[0][0] if city else '', country=country, place_provenance='apple',
                     cc=collections.Counter(i['place_cc'] for i in place_items).most_common(1)[0][0])
        else:
            g = geocode(*c['centre'])
            c.update(city=g['city'], country=g['country'], cc=g['cc'], place_provenance='geocoder')
    return clusters

def build_days(items):
    by_day = collections.defaultdict(list)
    for it in items:
        by_day[it['day']].append(it)
    days = {}
    for day, its in sorted(by_day.items()):
        pos = [i for i in its if i['lat'] is not None]
        # exif positions win; inferred are only used when the day has no exif fix at all
        exif = [i for i in pos if i['provenance'] == 'exif']
        use = exif if exif else pos
        clusters = day_clusters(use) if use else []
        d = dict(day=day, year=int(day[:4]), items=its, n=len(its),
                 photos=sum(1 for i in its if not i['movie']), videos=sum(1 for i in its if i['movie']),
                 screenshots=sum(1 for i in its if i['screenshot']),
                 n_exif=len(exif), n_inferred=sum(1 for i in pos if i['provenance'] == 'inferred'),
                 n_unplaced=len(its) - len(pos), clusters=clusters,
                 provenance=('exif' if exif else ('inferred' if pos else 'none')),
                 persons=sorted(set(p for i in its for p in i['persons'])),
                 albums=sorted(set(a for i in its for a in i['albums'])),
                 keywords=sorted(set(k for i in its for k in i['keywords'])))
        days[day] = d
    return days

# ---------------------------------------------------------------- stays and flights
def build_stays(days):
    stays = []
    for day in sorted(days):
        d = days[day]
        if not d['clusters']:
            continue
        night = d['clusters'][-1]                       # last position of the day
        if stays and hav(stays[-1]['centre'], night['centre']) <= STAY_KM and (date.fromisoformat(day) - date.fromisoformat(stays[-1]['end'])).days <= MAX_GAP_DAYS:
            s = stays[-1]
            s['pts'].append(night['centre']); s['end'] = day; s['photo_days'] += 1
            s['photo_count'] += d['n']; s['centre'] = centroid(s['pts'])
            s['prov'].add(night['provenance']); s['place_votes'][(night['city'], night['country'], night['cc'], night['place_provenance'])] += 1
        else:
            stays.append(dict(start=day, end=day, pts=[night['centre']], centre=night['centre'], photo_days=1,
                              photo_count=d['n'], prov={night['provenance']},
                              place_votes=collections.Counter({(night['city'], night['country'], night['cc'], night['place_provenance']): 1})))
    for k, s in enumerate(stays, 1):
        (city, country, cc, pp), _ = s['place_votes'].most_common(1)[0]
        s.update(stay_id=k, city=city, country=country, cc=cc, place_provenance=pp,
                 position_provenance='exif' if s['prov'] == {'exif'} else ('inferred' if s['prov'] == {'inferred'} else 'mixed'),
                 days=(date.fromisoformat(s['end']) - date.fromisoformat(s['start'])).days + 1,
                 away=hav(s['centre'], HOME) > STAY_KM)
        s['nights'] = s['days'] - 1
    return stays

def build_flights(days):
    """Repo model: consecutive positioned clusters > FLIGHT_KM apart along a great circle."""
    seq = []
    for day in sorted(days):
        for c in days[day]['clusters']:
            seq.append(c)
    flights = []
    for a, b in zip(seq, seq[1:]):
        dist = hav(a['centre'], b['centre'])
        if dist <= FLIGHT_KM:
            continue
        gap_h = (b['first'] - a['last']).total_seconds() / 3600
        same_day = a['last'].date() == b['first'].date()
        flights.append(dict(
            year=b['first'].year, date=b['first'].date().isoformat(),
            origin_last_seen=a['last'].isoformat(timespec='minutes'),
            destination_first_seen=b['first'].isoformat(timespec='minutes'),
            origin_city=a['city'], origin_country=a['country'], origin_lat=round(a['centre'][0], 4), origin_lon=round(a['centre'][1], 4),
            destination_city=b['city'], destination_country=b['country'], destination_lat=round(b['centre'][0], 4), destination_lon=round(b['centre'][1], 4),
            distance_km=round(dist), gap_hours=round(gap_h, 1),
            implied_speed_kmh=round(dist / gap_h) if 0 < gap_h <= 24 else '',
            model='flight_model_same_day' if same_day else 'great_circle_between_days',
            origin_provenance=f"{a['provenance']}/{a['place_provenance']}", destination_provenance=f"{b['provenance']}/{b['place_provenance']}"))
    return flights

def build_months(items, days, stays, flights):
    first = min(i['t'] for i in items).date().replace(day=1)
    last = max(i['t'] for i in items).date()
    # night d -> d+1 belongs to the stay covering d; nights between stays are unplaced
    night_state = {}
    for s in stays:
        d0, d1 = date.fromisoformat(s['start']), date.fromisoformat(s['end'])
        d = d0
        while d < d1:
            night_state[d] = 'away' if s['away'] else 'home'; d += timedelta(days=1)
    for a, b in zip(stays, stays[1:]):
        d = date.fromisoformat(a['end']); e = date.fromisoformat(b['start'])
        while d < e:
            night_state.setdefault(d, 'unplaced'); d += timedelta(days=1)
    photo_month = collections.Counter((i['t'].year, i['t'].month) for i in items)
    countries = collections.defaultdict(set)
    for day, d in days.items():
        for c in d['clusters']:
            countries[(int(day[:4]), int(day[5:7]))].add(c['country'])
    fl_month = collections.Counter((int(f['date'][:4]), int(f['date'][5:7])) for f in flights)
    rows = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        away = unpl = 0
        d = date(y, m, 1)
        while d.month == m:
            st = night_state.get(d)
            away += st == 'away'; unpl += st == 'unplaced'; d += timedelta(days=1)
        rows.append(dict(year=y, month=m, nights_away_from_KL=away, nights_unplaced=unpl,
                         countries='; '.join(sorted(countries.get((y, m), []))),
                         flights=fl_month.get((y, m), 0), photo_count=photo_month.get((y, m), 0)))
        m += 1
        if m == 13: y, m = y + 1, 1
    return rows

# ---------------------------------------------------------------- vision labels
def pick_vision_items(d):
    cands = [i for i in d['items'] if i['derivatives']]
    fav = [i for i in cands if i['favorite'] and not i['screenshot']]
    plain = [i for i in cands if not i['favorite'] and not i['screenshot'] and not i['movie']]
    rest = [i for i in cands if i not in fav and i not in plain]
    out = []
    for pool in (fav, plain, rest):
        need = VISION_PER_DAY - len(out)
        if need <= 0: break
        if len(pool) <= need:
            out += pool
        else:   # spread across the day: evenly spaced by time order
            step = len(pool) / need
            out += [pool[int(k * step)] for k in range(need)]
    return sorted(out, key=lambda i: i['t'])

def derivative_jpeg(it):
    from PIL import Image
    paths = [p for p in it['derivatives'] if p.lower().endswith(('.jpeg', '.jpg'))]
    if not paths: return None
    p = max(paths, key=lambda q: os.path.getsize(q) if os.path.exists(q) else -1)
    if not os.path.exists(p): return None
    im = Image.open(p).convert('RGB'); im.thumbnail((VISION_MAX_PX, VISION_MAX_PX))
    buf = io.BytesIO(); im.save(buf, 'JPEG', quality=80)
    return base64.standard_b64encode(buf.getvalue()).decode()

VISION_SYSTEM = ("You label photographs for a factual archive. For each image return 1 to 3 plain nouns naming "
                 "what is physically in frame (objects, food, animals, buildings, landscape types, documents, screens). "
                 "Nouns only, lowercase, singular, English. No sentences, no adjectives, no moods, no purposes, no guesses "
                 "about place names or people's identities. If the image is a text screenshot say 'screenshot' plus the visible "
                 "subject noun. Reply with JSON only: {\"1\": [\"noun\", ...], \"2\": [...]} keyed by image number.")

def label_day(client, d, cache):
    picks = [i for i in pick_vision_items(d) if i['uuid'] not in cache]
    if not picks: return 0
    content = []; sent = []
    for k, it in enumerate(picks, 1):
        b64 = derivative_jpeg(it)
        if not b64: cache[it['uuid']] = {'labels': [], 'note': 'no derivative on disk'}; continue
        content.append({'type': 'text', 'text': f'Image {len(sent)+1}:'})
        content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': b64}})
        sent.append(it)
    if not sent: return 0
    content.append({'type': 'text', 'text': f'{len(sent)} images. Return the JSON.'})
    for attempt in range(3):
        try:
            r = client.beta.messages.create(
                model=VISION_MODEL, max_tokens=600, system=VISION_SYSTEM,
                messages=[{'role': 'user', 'content': content}],
                output_config={'effort': 'low'},
                betas=['server-side-fallback-2026-07-01'], extra_body={'fallbacks': 'default'})
            if r.stop_reason == 'refusal':
                for it in sent: cache[it['uuid']] = {'labels': [], 'note': 'refusal', 'model': r.model}
                return len(sent)
            text = ''.join(b.text for b in r.content if b.type == 'text').strip()
            text = text[text.find('{'): text.rfind('}') + 1]
            js = json.loads(text)
            for k, it in enumerate(sent, 1):
                labs = js.get(str(k)) or []
                cache[it['uuid']] = {'labels': [str(x).strip().lower() for x in labs][:3], 'model': r.model,
                                     'usage': [r.usage.input_tokens, r.usage.output_tokens] if k == 1 else None}
            return len(sent)
        except Exception as e:
            if attempt == 2:
                for it in sent: cache[it['uuid']] = {'labels': [], 'note': f'error: {type(e).__name__}: {str(e)[:120]}'}
                return len(sent)
            time.sleep(3 * (attempt + 1))

def run_vision(days, years, cache_path):
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    try:
        import anthropic
        client = anthropic.Anthropic()
        client.messages.count_tokens(model=VISION_MODEL, messages=[{'role': 'user', 'content': 'x'}])
    except Exception as e:
        print(f'vision: skipped, no usable Anthropic credentials ({type(e).__name__})', flush=True)
        return cache, False
    for y in years:
        todo = [days[k] for k in sorted(days, reverse=True) if days[k]['year'] == y]
        n = 0; t0 = time.time()
        with ThreadPoolExecutor(4) as ex:
            for got in ex.map(lambda d: label_day(client, d, cache), todo):
                n += got or 0
                if n and n % 60 == 0:
                    json.dump(cache, open(cache_path, 'w'), ensure_ascii=False)
        json.dump(cache, open(cache_path, 'w'), ensure_ascii=False, indent=0)
        tok = [v['usage'] for v in cache.values() if v.get('usage')]
        print(f'vision {y}: {n} images labelled in {round(time.time()-t0)}s; cache {len(cache)} uuids', flush=True)
        yield y, cache
    return

# ---------------------------------------------------------------- outputs
def write_csv(path, rows, cols):
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore'); w.writeheader(); w.writerows(rows)

def journal_block(d, stay_of_day, flights_by_day, cache):
    loc = ' → '.join(f"{c['city'] + ', ' if c['city'] else ''}{c['country']}" for c in d['clusters']) if d['clusters'] else ''
    s = stay_of_day.get(d['day'])
    stay = f"{s['city'] + ', ' if s['city'] else ''}{s['country']}" if s else ''
    fl = '; '.join(f"{f['origin_city'] or f['origin_country']} → {f['destination_city'] or f['destination_country']} ({f['distance_km']} km, {f['model']})"
                   for f in flights_by_day.get(d['day'], []))
    claude = sorted(set(l for it in d['items'] for l in (cache.get(it['uuid']) or {}).get('labels', [])))
    apple = sorted(set(l for it in d['items'] for l in it['apple_labels']))
    labels = '; '.join(x for x in [', '.join(claude) + ' (claude)' if claude else '', ', '.join(apple) + ' (apple)' if apple else ''] if x)
    prov = d['provenance']
    if d['clusters']:
        prov += ' / place: ' + ', '.join(sorted(set(c['place_provenance'] for c in d['clusters'])))
    counts = f"{d['photos']} photos" + (f", {d['videos']} videos" if d['videos'] else '') + (f", {d['screenshots']} screenshots" if d['screenshots'] else '')
    lines = [f"### {d['day']}",
             f"- stay: {stay}" + (f" · positions: {loc}" if loc and loc != stay else ''),
             f"- items: {counts} · placed: {d['n_exif']} exif, {d['n_inferred']} inferred, {d['n_unplaced']} unplaced"]
    if fl: lines.append(f"- flight: {fl}")
    if d['persons']: lines.append(f"- persons: {', '.join(d['persons'])}")
    if d['albums']: lines.append(f"- albums: {', '.join(d['albums'])}")
    if d['keywords']: lines.append(f"- keywords: {', '.join(d['keywords'])}")
    titles = sorted(set(i['title'] for i in d['items'] if i['title']))
    if titles: lines.append(f"- titles: {' | '.join(titles)}")
    if labels: lines.append(f"- content: {labels}")
    lines.append(f"- provenance: {prov}")
    return '\n'.join(lines)

def write_outputs(out, items, days, stays, flights, months, cache, vision_years_done):
    os.makedirs(out, exist_ok=True)
    stay_of_day = {}
    for s in stays:
        d = date.fromisoformat(s['start'])
        while d <= date.fromisoformat(s['end']):
            stay_of_day[d.isoformat()] = s; d += timedelta(days=1)
    flights_by_day = collections.defaultdict(list)
    for f in flights: flights_by_day[f['date']].append(f)
    years = sorted(set(d['year'] for d in days.values()), reverse=True)
    index = ['# Footsteps — journal index', '',
             f'Library span {min(days)} to {max(days)}. Generated {datetime.now().isoformat(timespec="minutes")}.', '',
             '| year | days with photos | items | stays starting | flights | content labels |', '|---|---|---|---|---|---|']
    for y in years:
        yd = [days[k] for k in sorted(days) if days[k]['year'] == y]
        blocks = [journal_block(d, stay_of_day, flights_by_day, cache) for d in yd]
        with open(f'{out}/journal-{y}.md', 'w') as f:
            f.write(f'# Footsteps {y}\n\nRecord only. Fields: stay city, country · item counts · placed by provenance · flights (repo great-circle model, > {int(FLIGHT_KM)} km) · persons · albums · content labels: Claude vision nouns where run (claude) and the Photos on-device labels for every item (apple) · provenance.\n\n')
            f.write('\n\n'.join(blocks) + '\n')
        index.append(f"| {y} | {len(yd)} | {sum(d['n'] for d in yd)} | {sum(1 for s in stays if s['start'][:4]==str(y))} | {sum(1 for x in flights if x['year']==y)} | {'done' if y in vision_years_done else ('not run')} — [journal-{y}.md](journal-{y}.md) |")
    open(f'{out}/journal.md', 'w').write('\n'.join(index) + '\n')
    write_csv(f'{out}/stays.csv', [dict(s, year=int(s['start'][:4]), lat=round(s['centre'][0], 5), lon=round(s['centre'][1], 5), away_from_KL=int(s['away'])) for s in stays],
              ['stay_id', 'year', 'start', 'end', 'days', 'nights', 'photo_days', 'photo_count', 'city', 'country', 'cc', 'lat', 'lon', 'away_from_KL', 'position_provenance', 'place_provenance'])
    write_csv(f'{out}/flights.csv', flights,
              ['year', 'date', 'origin_last_seen', 'destination_first_seen', 'origin_city', 'origin_country', 'origin_lat', 'origin_lon',
               'destination_city', 'destination_country', 'destination_lat', 'destination_lon', 'distance_km', 'gap_hours', 'implied_speed_kmh', 'model', 'origin_provenance', 'destination_provenance'])
    write_csv(f'{out}/months.csv', months, ['year', 'month', 'nights_away_from_KL', 'nights_unplaced', 'countries', 'flights', 'photo_count'])
    day_rows = []
    for k in sorted(days):
        d = days[k]; c = d['clusters'][-1] if d['clusters'] else None
        day_rows.append(dict(day=k, year=d['year'], items=d['n'], photos=d['photos'], videos=d['videos'], screenshots=d['screenshots'],
                             n_exif=d['n_exif'], n_inferred=d['n_inferred'], n_unplaced=d['n_unplaced'],
                             city=c['city'] if c else '', country=c['country'] if c else '', lat=round(c['centre'][0], 5) if c else '', lon=round(c['centre'][1], 5) if c else '',
                             provenance=d['provenance'], place_provenance=c['place_provenance'] if c else '',
                             stay_id=stay_of_day[k]['stay_id'] if k in stay_of_day else '', persons='; '.join(d['persons']), albums='; '.join(d['albums'])))
    write_csv(f'{out}/days.csv', day_rows, list(day_rows[0].keys()))
    write_map(out, stays, flights, years)

def write_map(out, stays, flights, years):
    sj = [dict(id=s['stay_id'], y=int(s['start'][:4]), y2=int(s['end'][:4]), lat=round(s['centre'][0], 4), lon=round(s['centre'][1], 4), n=s['nights'],
               label=f"{s['city'] + ', ' if s['city'] else ''}{s['country']}", start=s['start'], end=s['end'], prov=s['position_provenance'] + '/' + s['place_provenance']) for s in stays]
    fj = [dict(y=f['year'], a=[f['origin_lat'], f['origin_lon']], b=[f['destination_lat'], f['destination_lon']],
               label=f"{f['date']}: {f['origin_city'] or f['origin_country']} → {f['destination_city'] or f['destination_country']}, {f['distance_km']} km") for f in flights]
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Footsteps — stays and flights</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
html,body{{margin:0;height:100%;font:14px/1.4 -apple-system,Helvetica,Arial,sans-serif;color:#222;background:#fff}}
#map{{position:absolute;inset:0}}
#panel{{position:absolute;top:12px;left:12px;z-index:1000;background:#fff;border:1px solid #ddd;border-radius:6px;padding:10px 14px;min-width:260px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
#panel h1{{font-size:15px;margin:0 0 6px;font-weight:600}}
#panel input{{width:100%}}
#yr{{font-weight:600;color:#0a5fd6}}
.leaflet-tooltip{{font-size:12px;color:#222;background:#fff;border:1px solid #ccc}}
small{{color:#666}}
</style></head><body>
<div id="map"></div>
<div id="panel"><h1>Footsteps — stays and flights</h1>
<div>year <span id="yr">all</span> <label><input type="checkbox" id="all" checked> all years</label></div>
<input type="range" id="slider" min="{min(years)}" max="{max(years)}" value="{max(years)}" step="1">
<div id="stats"></div>
<small>marker area ∝ nights in the stay · lines = repo great-circle model, legs &gt; {int(FLIGHT_KM)} km · basemap Esri World Light Gray</small>
</div>
<script>
const STAYS={json.dumps(sj)};const FLIGHTS={json.dumps(fj)};
const map=L.map('map',{{worldCopyJump:true}}).setView([10,110],3);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}',{{attribution:'Tiles &copy; Esri',maxZoom:16}}).addTo(map);
const layer=L.layerGroup().addTo(map);
function draw(){{
  const all=document.getElementById('all').checked;const y=+document.getElementById('slider').value;
  document.getElementById('yr').textContent=all?'all':y;layer.clearLayers();
  const st=STAYS.filter(s=>all||(s.y<=y&&s.y2>=y));const fl=FLIGHTS.filter(f=>all||f.y===y);
  fl.forEach(f=>L.polyline([f.a,f.b],{{color:'#0a5fd6',weight:1.2,opacity:.55}}).bindTooltip(f.label).addTo(layer));
  st.forEach(s=>{{const r=4+2.2*Math.sqrt(Math.max(s.n,0));
    L.circleMarker([s.lat,s.lon],{{radius:r,color:'#0a5fd6',weight:1,fillColor:'#0a5fd6',fillOpacity:.35}})
     .bindTooltip(`${{s.label}} · ${{s.start}} → ${{s.end}} · ${{s.n}} nights · ${{s.prov}}`).addTo(layer);}});
  document.getElementById('stats').textContent=`${{st.length}} stays · ${{fl.length}} flight legs`;
}}
document.getElementById('slider').oninput=()=>{{document.getElementById('all').checked=false;draw();}};
document.getElementById('all').onchange=draw;draw();
</script></body></html>"""
    open(f'{out}/map.html', 'w').write(html)

# ---------------------------------------------------------------- main
def year_report(y, items, days, stays, flights):
    yi = [i for i in items if i['year'] == y]
    tagged = sum(1 for i in yi if i['provenance'] == 'exif'); inf = sum(1 for i in yi if i['provenance'] == 'inferred')
    print(f"{y}: items {len(yi)} (tagged {tagged}, inferred {inf}, unplaced {len(yi)-tagged-inf}); "
          f"days with photos {sum(1 for d in days.values() if d['year']==y)}; stays starting {sum(1 for s in stays if s['start'][:4]==str(y))}; "
          f"flights {sum(1 for f in flights if f['year']==y)}", flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.expanduser('~/review/photo-geo/footsteps'))
    ap.add_argument('--years', nargs='*', type=int, help='restrict the vision pass to these years')
    ap.add_argument('--no-vision', action='store_true')
    ap.add_argument('--no-infer', action='store_true', help='do not (re)run infer.py')
    a = ap.parse_args()
    items = load_items(a.no_infer)
    print(f'items {len(items)}: exif {sum(i["provenance"]=="exif" for i in items)}, inferred {sum(i["provenance"]=="inferred" for i in items)}, unplaced {sum(i["provenance"]=="none" for i in items)}', flush=True)
    days = build_days(items)
    stays = build_stays(days)
    flights = build_flights(days)
    months = build_months(items, days, stays, flights)
    years = sorted(set(i['year'] for i in items), reverse=True)
    for y in years: year_report(y, items, days, stays, flights)
    print('vision candidates:', sum(len(pick_vision_items(d)) for d in days.values()), 'images over', len(days), 'days', flush=True)
    cache_path = os.path.join(a.out, 'labels-cache.json')
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    done = set(int(k) for k in (json.load(open(cache_path + '.years')) if os.path.exists(cache_path + '.years') else []))
    write_outputs(a.out, items, days, stays, flights, months, cache, done)
    print(f'wrote {a.out} (journal, stays.csv, flights.csv, months.csv, days.csv, map.html)', flush=True)
    if not a.no_vision:
        for y, cache in run_vision(days, [y for y in years if (not a.years or y in a.years)], cache_path):
            done.add(y); json.dump(sorted(done), open(cache_path + '.years', 'w'))
            write_outputs(a.out, items, days, stays, flights, months, cache, done)
            print(f'{y}: journal-{y}.md rewritten with content labels', flush=True)
    print(f'months.csv: {a.out}/months.csv', flush=True)

if __name__ == '__main__':
    main()
