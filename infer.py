import json, math, collections
from datetime import datetime, timedelta

d = json.load(open('all.json'))
def ts(p):
    # camera wall clock; Photos' tz offset on untagged shots is a guess and disagrees with GPS-derived offsets on anchors
    return datetime.fromisoformat(p['date']).replace(tzinfo=None)
anchors = sorted([p for p in d if p.get('latitude') is not None and p.get('date')], key=ts)
untag = [p for p in d if p.get('latitude') is None and p.get('date')]
A_ts = [ts(p) for p in anchors]

def tier(p):
    return 'A' if (p['exif_info'] or {}).get('camera_make') else 'B'

def hav(a, b):
    R=6371.0
    la1,lo1,la2,lo2 = map(math.radians,(a[0],a[1],b[0],b[1]))
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*R*math.asin(math.sqrt(h))

def to_xyz(lat,lon):
    la,lo=math.radians(lat),math.radians(lon)
    return (math.cos(la)*math.cos(lo),math.cos(la)*math.sin(lo),math.sin(la))
def from_xyz(v):
    x,y,z=v; n=math.sqrt(x*x+y*y+z*z)
    return (math.degrees(math.asin(z/n)),math.degrees(math.atan2(y,x)))
def slerp(a,b,f):
    va,vb=to_xyz(*a),to_xyz(*b)
    return from_xyz(tuple(va[i]*(1-f)+vb[i]*f for i in range(3)))
def bearing(a,b):
    la1,lo1,la2,lo2 = map(math.radians,(a[0],a[1],b[0],b[1]))
    y=math.sin(lo2-lo1)*math.cos(la2); x=math.cos(la1)*math.sin(la2)-math.sin(la1)*math.cos(la2)*math.cos(lo2-lo1)
    return (math.degrees(math.atan2(y,x))+360)%360
def dest(a, brg_deg, dist_km):
    R=6371.0; la1,lo1=math.radians(a[0]),math.radians(a[1]); b=math.radians(brg_deg); dr=dist_km/R
    la2=math.asin(math.sin(la1)*math.cos(dr)+math.cos(la1)*math.sin(dr)*math.cos(b))
    lo2=lo1+math.atan2(math.sin(b)*math.sin(dr)*math.cos(la1),math.cos(dr)-math.sin(la1)*math.sin(la2))
    return (math.degrees(la2),(math.degrees(lo2)+540)%360-180)

import bisect
WIN = timedelta(minutes=15)
results=[]; nofix=[]
for p in untag:
    t=ts(p); i=bisect.bisect_left(A_ts,t)
    before = anchors[i-1] if i>0 else None
    after  = anchors[i] if i<len(anchors) else None
    def sameday(a): return a is not None and ts(a).date()==t.date()
    if not sameday(before): before=None
    if not sameday(after): after=None
    gb = (t-ts(before)).total_seconds() if before else None
    ga = (ts(after)-t).total_seconds() if after else None
    near = min([g for g in (gb,ga) if g is not None], default=None)
    rec = dict(uuid=p['uuid'], filename=p['original_filename'], date=p['date'], tier=tier(p),
               camera=(p['exif_info'] or {}).get('camera_model'), labels=p['labels'], imported_by=p['imported_by'],
               screenshot=p['screenshot'], w=p['original_width'], h=p['original_height'])
    if near is None or near > WIN.total_seconds():
        rec['reason']='no anchor within 15 min same day'; rec['nearest_gap_s']=near
        rec['day_anchor_before']=(before['latitude'],before['longitude'],before['date']) if before else None
        rec['day_anchor_after']=(after['latitude'],after['longitude'],after['date']) if after else None
        nofix.append(rec); continue
    if before and after:
        A=(before['latitude'],before['longitude']); B=(after['latitude'],after['longitude'])
        f = gb/(gb+ga) if (gb+ga)>0 else 0.5
        lat,lon = slerp(A,B,f); method='interpolate'
        span=gb+ga; dist=hav(A,B); speed_kmh = dist/(span/3600) if span>0 else 0
        rec.update(anchor_before=dict(lat=A[0],lon=A[1],date=before['date'],gap_s=gb),
                   anchor_after=dict(lat=B[0],lon=B[1],date=after['date'],gap_s=ga),
                   anchor_dist_km=round(dist,2), implied_speed_kmh=round(speed_kmh,1))
    else:
        anc = before or after; sgn = 1 if before else -1
        gap = gb if before else ga
        # dead-reckon: velocity from the anchor and its own neighbour on the same side
        j = anchors.index(anc); nb = anchors[j-1] if before and j>0 else (anchors[j+1] if after and j+1<len(anchors) else None)
        P=(anc['latitude'],anc['longitude'])
        speed_kmh=0; brg=None
        if nb and sameday(nb):
            Q=(nb['latitude'],nb['longitude']); dt=abs((ts(anc)-ts(nb)).total_seconds())
            if 0<dt<=3*3600:
                dist=hav(Q,P); speed_kmh=dist/(dt/3600)
                brg = bearing(Q,P) if before else bearing(P,Q)  # direction of travel toward the untagged shot
                if not before: brg=(brg)%360  # from after-anchor back toward earlier neighbour
        if brg is not None and speed_kmh>5:
            lat,lon = dest(P, brg, speed_kmh*gap/3600); method='dead_reckon'
        else:
            lat,lon = P; method='nearest_anchor'
        rec.update(anchor=dict(lat=P[0],lon=P[1],date=anc['date'],gap_s=gap,side='before' if before else 'after'),
                   derived_speed_kmh=round(speed_kmh,1), derived_bearing=None if brg is None else round(brg,1))
    rec.update(lat=round(lat,6), lon=round(lon,6), method=method, nearest_gap_s=near)
    results.append(rec)

json.dump(results, open('inferred.json','w'), indent=1, ensure_ascii=False, default=str)
json.dump(nofix, open('nofix.json','w'), indent=1, ensure_ascii=False, default=str)
print("untagged:",len(untag)," inferred:",len(results)," no fix:",len(nofix))
for T in 'AB':
    r=[x for x in results if x['tier']==T]; n=[x for x in nofix if x['tier']==T]
    print(f"tier {T}: inferred {len(r)} ({collections.Counter(x['method'] for x in r)}), nofix {len(n)}")
fast=[x for x in results if x.get('implied_speed_kmh',0)>150 or x.get('derived_speed_kmh',0)>150]
print("inferred at >150 km/h (in flight / train):",len(fast), collections.Counter(x['tier'] for x in fast))
port=[x for x in results if 'Porthole' in (x['labels'] or [])]
print("Porthole-labelled inferred:",len(port),"of",sum(1 for p in untag if 'Porthole' in (p['labels'] or [])))

# ---- fallback pass (tier A only): same-day anchors beyond 15 min
CRUISE=850.0  # km/h assumed cruise for flight_model
FLIGHT_KM=300 # anchor separation above which the day is treated as a flight day
still=[]
for rec in list(nofix):
    if rec['tier']!='A': still.append(rec); continue
    b=rec['day_anchor_before']; a=rec['day_anchor_after']
    t=datetime.fromisoformat(rec['date']).replace(tzinfo=None)
    if b and a:
        A=(b[0],b[1]); B=(a[0],a[1]); tb=datetime.fromisoformat(b[2]).replace(tzinfo=None); ta=datetime.fromisoformat(a[2]).replace(tzinfo=None)
        gb=(t-tb).total_seconds(); ga=(ta-t).total_seconds(); dist=hav(A,B)
        if dist>FLIGHT_KM:
            # flight model: assume in the air, back-calculate from the arrival-side anchor along the great circle
            slack=20*60  # anchor is taken after landing/taxi; assume 20 min from touchdown to photo
            air=max(ga-slack,0); back_km=min(CRUISE*air/3600, dist)
            f=1-back_km/dist
            lat,lon=slerp(A,B,f); method='flight_model'; conf='low'
            rec.update(anchor_before=dict(lat=A[0],lon=A[1],date=b[2],gap_s=gb),anchor_after=dict(lat=B[0],lon=B[1],date=a[2],gap_s=ga),
                       anchor_dist_km=round(dist,1), model_note=f'great circle from before-anchor to after-anchor; {round(back_km)} km short of after-anchor at {CRUISE} km/h cruise, 20 min landing slack')
        else:
            f=gb/(gb+ga) if (gb+ga)>0 else .5
            lat,lon=slerp(A,B,f); method='interpolate_wide'; conf='low'
            rec.update(anchor_before=dict(lat=A[0],lon=A[1],date=b[2],gap_s=gb),anchor_after=dict(lat=B[0],lon=B[1],date=a[2],gap_s=ga),anchor_dist_km=round(dist,1))
    elif b or a:
        x=b or a; lat,lon=x[0],x[1]; method='nearest_anchor_day'; conf='very_low'
        rec.update(anchor=dict(lat=x[0],lon=x[1],date=x[2],side='before' if b else 'after',gap_s=abs((t-datetime.fromisoformat(x[2]).replace(tzinfo=None)).total_seconds())))
    else:
        still.append(rec); continue
    rec.pop('reason',None); rec.update(lat=round(lat,6),lon=round(lon,6),method=method,confidence=conf)
    results.append(rec)
for r in results:
    r.setdefault('confidence','high' if r['method']=='interpolate' else 'medium')
    sp=r.get('implied_speed_kmh') or r.get('derived_speed_kmh') or 0
    if sp>1000:
        r['confidence']='low'; r['model_note']=f'anchors imply {round(sp)} km/h: one anchor timestamp or fix is inconsistent'
nofix=still
json.dump(results, open('inferred.json','w'), indent=1, ensure_ascii=False, default=str)
json.dump(nofix, open('nofix.json','w'), indent=1, ensure_ascii=False, default=str)
print("after fallback: inferred",len(results),"nofix",len(nofix))
print("tier A methods:",collections.Counter(r['method'] for r in results if r['tier']=='A'))
print("tier A still no fix:",sum(1 for r in nofix if r['tier']=='A'))
