import json, math, collections, html, os, sys
from datetime import datetime
OUT = sys.argv[1] if len(sys.argv)>1 else '.'
os.makedirs(OUT, exist_ok=True)
d = json.load(open('all.json'))
r = json.load(open('inferred.json'))
anch = {p['uuid']:p for p in d if p.get('latitude') is not None and p.get('date')}
def hav(a,b):
    R=6371.0; la1,lo1,la2,lo2=map(math.radians,(a[0],a[1],b[0],b[1]))
    h=math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*R*math.asin(math.sqrt(h))
# ---- flight days: any tier-A inferred point on a day with anchors >300 km apart or implied speed >150
days=collections.defaultdict(list)
for x in r: days[x['date'][:10]].append(x)
anch_by_day=collections.defaultdict(list)
for p in anch.values(): anch_by_day[p['date'][:10]].append(p)
flight_days=set()
for day,xs in days.items():
    if any(x['tier']=='A' and (x.get('implied_speed_kmh',0)>150 or x.get('derived_speed_kmh',0)>150 or x['method']=='flight_model') for x in xs):
        flight_days.add(day)
# ---- GeoJSON
feats=[]
for x in r:
    props={k:v for k,v in x.items() if k not in ('lat','lon')}
    props['inferred']=True
    feats.append(dict(type='Feature',geometry=dict(type='Point',coordinates=[x['lon'],x['lat']]),properties=props))
lines={}
for day in sorted(flight_days):
    pts=[(p['date'],p['latitude'],p['longitude'],'anchor') for p in anch_by_day[day]]+[(x['date'],x['lat'],x['lon'],x['method']) for x in days[day] if x['tier']=='A']
    pts.sort(key=lambda t: datetime.fromisoformat(t[0]).replace(tzinfo=None))
    lines[day]=pts
    feats.append(dict(type='Feature',geometry=dict(type='LineString',coordinates=[[p[2],p[1]] for p in pts]),properties=dict(day=day,kind='track',n_points=len(pts))))
json.dump(dict(type='FeatureCollection',features=feats),open(f'{OUT}/inferred.geojson','w'),ensure_ascii=False,default=str)
# ---- KML
def esc(s): return html.escape(str(s),quote=False)
def pm(x):
    thumb=f'thumbs/{x["uuid"]}.jpg'
    img=f'<img src="{thumb}" width="320"/><br/>' if os.path.exists(f'{OUT}/{thumb}') else ''
    desc=f'{img}<b>{esc(x["filename"])}</b><br/>{esc(x["date"])}<br/>camera: {esc(x.get("camera"))}<br/>method: <b>{esc(x["method"])}</b> ({esc(x.get("confidence"))})<br/>labels: {esc(", ".join(x["labels"] or []))}<br/>'
    if 'anchor_before' in x: desc+=f'anchor before: {x["anchor_before"]["date"]} (+{round(x["anchor_before"]["gap_s"]/60)} min)<br/>anchor after: {x["anchor_after"]["date"]} (-{round(x["anchor_after"]["gap_s"]/60)} min)<br/>anchor separation: {x.get("anchor_dist_km")} km<br/>'
    if 'anchor' in x: desc+=f'anchor ({x["anchor"]["side"]}): {x["anchor"]["date"]} gap {round(x["anchor"]["gap_s"]/60)} min<br/>'
    if x.get('model_note'): desc+=esc(x['model_note'])+'<br/>'
    if x.get('imported_by'): desc+=f'imported by: {esc(x["imported_by"][0])}<br/>'
    style={'high':'#hi','medium':'#med','low':'#low','very_low':'#vlow'}[x.get('confidence','medium')]
    return f'<Placemark><name>{esc(x["date"][:16])} {esc(x["filename"])}</name><styleUrl>{style}</styleUrl><TimeStamp><when>{esc(x["date"])}</when></TimeStamp><description><![CDATA[{desc}]]></description><Point><coordinates>{x["lon"]},{x["lat"]},0</coordinates></Point></Placemark>'
k=['<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Inferred photo positions</name>']
for sid,col in [('hi','ff00c800'),('med','ff00a5ff'),('low','ff0000ff'),('vlow','ff888888'),('anc','ffffffff')]:
    k.append(f'<Style id="{sid}"><IconStyle><color>{col}</color><scale>{0.6 if sid=="anc" else 1.0}</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle><LabelStyle><scale>0</scale></LabelStyle></Style>')
k.append('<Style id="trk"><LineStyle><color>ff0080ff</color><width>2.5</width></LineStyle></Style>')
tierA=[x for x in r if x['tier']=='A']; tierB=[x for x in r if x['tier']=='B']
k.append('<Folder><name>Camera captures (tier A) — inferred</name>')
for conf in ['high','medium','low','very_low']:
    xs=sorted([x for x in tierA if x.get('confidence')==conf],key=lambda x:x['date'])
    if not xs: continue
    k.append(f'<Folder><name>{conf} confidence ({len(xs)})</name>'+''.join(pm(x) for x in xs)+'</Folder>')
k.append('</Folder>')
k.append('<Folder><name>Flight / travel days — tracks with anchors</name>')
for day,pts in lines.items():
    k.append(f'<Folder><name>{day}</name><Placemark><name>track {day}</name><styleUrl>#trk</styleUrl><LineString><tessellate>1</tessellate><coordinates>'+' '.join(f'{p[2]},{p[1]},0' for p in pts)+'</coordinates></LineString></Placemark>')
    for p in pts:
        if p[3]=='anchor': k.append(f'<Placemark><name>anchor {esc(p[0][11:16])}</name><styleUrl>#anc</styleUrl><Point><coordinates>{p[2]},{p[1]},0</coordinates></Point></Placemark>')
    k.append('</Folder>')
k.append('</Folder>')
k.append(f'<Folder><name>Received / saved images (tier B, {len(tierB)}) — position = where the phone was when the image arrived</name><visibility>0</visibility>'+''.join(pm(x) for x in sorted(tierB,key=lambda x:x['date']))+'</Folder>')
k.append('</Document></kml>')
open(f'{OUT}/inferred.kml','w').write('\n'.join(k))
print("flight days:",sorted(flight_days)); print("kml placemarks:",len(r),"lines:",len(lines))
