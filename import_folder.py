"""Import camera-dated files from a folder into Photos, skipping what the library already holds.

    exiftool -q -q -fast2 -r -json -DateTimeOriginal -Model -FileSize# -ImageSize -MIMEType FOLDER > inv.json
    python3 import_folder.py inv.json --album "Import 2026-09-13" --dry-run
    python3 import_folder.py inv.json --album "Import 2026-09-13"

Only files with an EXIF DateTimeOriginal are considered (a file with no capture date would land in
Photos with today's date). Duplicates are dropped inside the set (same capture second, camera, size,
pixel size) and against the library (all.json in this directory): same original filename + capture
second, or same capture second + camera + pixel size. Imports go through Photos' own AppleScript
`import` in batches, into one new album, with Photos' duplicate check left ON as a second guard.
"""
import argparse, collections, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))

def dto_iso(s):                       # '2015:07:09 14:20:31' -> '2015-07-09T14:20:31'
    return s[:10].replace(':', '-') + 'T' + s[11:19]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inventory'); ap.add_argument('--album', required=True)
    ap.add_argument('--batch', type=int, default=40); ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--plan', default='import-plan.json')
    a = ap.parse_args()
    inv = json.load(open(a.inventory))
    lib = json.load(open(os.path.join(HERE, 'all.json')))
    k_name = set(); k_shot = set()
    for p in lib:
        if not p.get('date'): continue
        t = p['date'][:19]
        k_name.add((os.path.basename(p['original_filename']).lower(), t))
        cam = ((p.get('exif_info') or {}).get('camera_model') or '')
        k_shot.add((t, cam, p.get('original_width'), p.get('original_height')))
    plan, why = [], collections.Counter()
    seen = set()
    for x in inv:
        dto = x.get('DateTimeOriginal')
        mime = x.get('MIMEType') or ''
        if not dto or len(dto) < 19 or not dto[:4].isdigit(): why['no capture date'] += 1; continue
        if not (mime.startswith('image/') or mime.startswith('video/')): why['not image/video'] += 1; continue
        t = dto_iso(dto); cam = x.get('Model') or ''
        w, h = (x.get('ImageSize') or 'x').split('x')[:2]
        w = int(w) if w.isdigit() else None; h = int(h) if h.isdigit() else None
        key = (t, cam, x.get('FileSize'), w, h)
        if key in seen: why['duplicate inside folder'] += 1; continue
        seen.add(key)
        name = os.path.basename(x['SourceFile']).lower()
        if (name, t) in k_name: why['in library (name+time)'] += 1; continue
        if (t, cam, w, h) in k_shot: why['in library (time+camera+size)'] += 1; continue
        plan.append(dict(path=x['SourceFile'], date=t, camera=cam, mime=mime, size=x.get('FileSize'), w=w, h=h))
    plan.sort(key=lambda r: r['date'])
    json.dump(plan, open(a.plan, 'w'), indent=1, ensure_ascii=False)
    print('inventory', len(inv), '| to import', len(plan), '| skipped', dict(why))
    print('by year:', sorted(collections.Counter(r['date'][:4] for r in plan).items()))
    print('by camera:', collections.Counter(r['camera'] for r in plan).most_common(6))
    if a.dry_run or not plan: return
    log = open('import.log', 'a'); done = 0
    for i in range(0, len(plan), a.batch):
        files = [r['path'] for r in plan[i:i + a.batch]]
        lst = ', '.join('POSIX file "' + f.replace('"', '\\"') + '"' for f in files)
        scr = f'''tell application "Photos"
  if not (exists album "{a.album}") then make new album named "{a.album}"
  set r to import {{{lst}}} into album "{a.album}"
  set out to ""
  repeat with m in r
    set out to out & (id of m) & linefeed
  end repeat
  return out
end tell'''
        for attempt in range(3):
            p = subprocess.run(['osascript', '-e', scr], capture_output=True, text=True, timeout=900)
            if p.returncode == 0: break
            time.sleep(5)
        ids = [s for s in p.stdout.split('\n') if s.strip()]
        done += len(ids)
        log.write(f'{time.strftime("%H:%M:%S")} batch {i // a.batch + 1}: sent {len(files)} imported {len(ids)} rc {p.returncode} {p.stderr.strip()[:200]}\n'); log.flush()
        print(f'batch {i // a.batch + 1}/{(len(plan) + a.batch - 1) // a.batch}: sent {len(files)}, imported {len(ids)}', flush=True)
    print('imported total', done, 'of', len(plan), '-> album', a.album)

if __name__ == '__main__':
    main()
