"""Write inferred GPS + photogeo provenance into copies of exported camera captures (tier A only)."""
import json, os, subprocess, shutil, datetime, sys
OUT = sys.argv[1] if len(sys.argv) > 1 else 'tagged'
CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'photogeo.config')
os.makedirs(OUT, exist_ok=True)
r = json.load(open('inferred.json')); now = datetime.datetime.now().isoformat(timespec='seconds'); n = 0; skipped = []
for x in r:
    if x['tier'] != 'A':
        continue                       # never geotag a received image
    d = 'export_A/' + x['uuid']
    fs = [f for f in (os.listdir(d) if os.path.isdir(d) else []) if f.lower().endswith(('.jpg', '.jpeg', '.heic', '.png', '.tif', '.tiff', '.dng'))]
    if not fs:
        skipped.append((x['date'][:16], x['filename'])); continue
    dst = f'{OUT}/{x["date"][:10]}_{x["uuid"][:8]}_{fs[0]}'; shutil.copy2(f'{d}/{fs[0]}', dst)
    lat, lon = x['lat'], x['lon']
    ab = x.get('anchor_before') or (x.get('anchor') if x.get('anchor', {}).get('side') == 'before' else None)
    aa = x.get('anchor_after') or (x.get('anchor') if x.get('anchor', {}).get('side') == 'after' else None)
    cmd = ['exiftool', '-config', CFG, '-m', '-overwrite_original', '-P',
           f'-GPSLatitude={abs(lat)}', f'-GPSLatitudeRef={"N" if lat >= 0 else "S"}',
           f'-GPSLongitude={abs(lon)}', f'-GPSLongitudeRef={"E" if lon >= 0 else "W"}',
           f'-GPSProcessingMethod=INFERRED:{x["method"]}',
           '-XMP-photogeo:GPSInferred=true', f'-XMP-photogeo:InferenceMethod={x["method"]}',
           f'-XMP-photogeo:Confidence={x["confidence"]}', f'-XMP-photogeo:InferredAt={now}']
    if ab: cmd.append(f'-XMP-photogeo:AnchorBefore={ab["lat"]:.6f},{ab["lon"]:.6f} @ {ab["date"]} (gap {round(ab["gap_s"]/60,1)} min)')
    if aa: cmd.append(f'-XMP-photogeo:AnchorAfter={aa["lat"]:.6f},{aa["lon"]:.6f} @ {aa["date"]} (gap {round(aa["gap_s"]/60,1)} min)')
    if x.get('model_note'): cmd.append(f'-XMP-photogeo:Note={x["model_note"]}')
    cmd.append(dst)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0: print('ERR', dst, p.stderr.strip()[:200]); continue
    n += 1
print('written', n, 'skipped', len(skipped)); [print(' skip', s) for s in skipped]
