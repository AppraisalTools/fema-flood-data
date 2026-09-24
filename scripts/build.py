#!/usr/bin/env python3
"""
Build lookup files from FEMA "NFHL Data-County" downloads.

Usage:   python3 scripts/build.py incoming/*.zip
Output:  data/<ST>/panels.json          every FIRM panel we have for that state (number, effective date, outline)
         data/<ST>/z/<FIRM_PAN10>.json  flood-zone polygons overlapping that panel (minimal-hazard X omitted:
                                        a point inside a panel that hits no polygon is Zone X)
         data/index.json                what's loaded and when

Standard-library only (no GDAL / shapely), so it runs anywhere - including a GitHub Action.
Re-running with a newer download for a county replaces that county's data and leaves other counties alone.
"""
import sys, os, io, json, struct, zipfile, datetime, re, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
STATE_ABBR = {'37': 'NC', '45': 'SC', '51': 'VA', '13': 'GA', '47': 'TN'}
PREC = 6  # decimal places for lon/lat (~0.1 m)

# ---------------------------------------------------------------- shapefile / dbf readers
def read_dbf(buf):
    n = struct.unpack('<I', buf[4:8])[0]
    hlen, rlen = struct.unpack('<HH', buf[8:12])
    fields, pos = [], 32
    while buf[pos] != 0x0D:
        name = buf[pos:pos + 11].split(b'\x00')[0].decode('latin1').strip()
        ftype = chr(buf[pos + 11]); flen = buf[pos + 16]
        fields.append((name, ftype, flen)); pos += 32
    out = []
    for i in range(n):
        rec = buf[hlen + i * rlen: hlen + (i + 1) * rlen]
        if rec[:1] == b'*':
            out.append(None); continue
        o, row = 1, {}
        for name, ftype, flen in fields:
            raw = rec[o:o + flen].decode('latin1').strip(); o += flen
            if ftype == 'D' and len(raw) == 8 and raw.isdigit():
                raw = raw[4:6] + '/' + raw[6:8] + '/' + raw[0:4]
            elif ftype in 'NF' and raw not in ('', '*'):
                try: raw = float(raw) if ('.' in raw or 'e' in raw.lower()) else int(raw)
                except ValueError: pass
            row[name.upper()] = raw
        out.append(row)
    return out

def read_shp(buf):
    """Yields (bbox, rings) per record; rings are lists of [lon, lat]."""
    pos, end = 100, len(buf)
    while pos + 8 <= end:
        clen = struct.unpack('>i', buf[pos + 4:pos + 8])[0] * 2
        rec = buf[pos + 8: pos + 8 + clen]; pos += 8 + clen
        st = struct.unpack('<i', rec[:4])[0]
        if st == 0:
            yield None, []; continue
        if st not in (5, 15, 25):
            raise ValueError('unexpected shape type %d' % st)
        bbox = list(struct.unpack('<4d', rec[4:36]))
        nparts, npts = struct.unpack('<ii', rec[36:44])
        parts = list(struct.unpack('<%di' % nparts, rec[44:44 + 4 * nparts]))
        p0 = 44 + 4 * nparts
        pts = struct.unpack('<%dd' % (2 * npts), rec[p0:p0 + 16 * npts])
        rings = []
        for k in range(nparts):
            a = parts[k]; b = parts[k + 1] if k + 1 < nparts else npts
            rings.append([[round(pts[2 * j], PREC), round(pts[2 * j + 1], PREC)] for j in range(a, b)])
        yield bbox, rings

def find_member(z, layer, ext):
    want = (layer + ext).lower()
    for n in z.namelist():
        if os.path.basename(n).lower() == want:
            return n
    return None

def load_layer(z, layer):
    shp, dbf = find_member(z, layer, '.shp'), find_member(z, layer, '.dbf')
    if not shp or not dbf:
        return None
    # the inner zip-within-zip case (MSC sometimes nests the shapefile zip)
    attrs = read_dbf(z.read(dbf))
    geoms = list(read_shp(z.read(shp)))
    prj = find_member(z, layer, '.prj')
    if prj:
        wkt = z.read(prj).decode('latin1')
        if 'PROJCS' in wkt:
            raise ValueError(layer + ' is projected (' + wkt[:60] + '...) - expected geographic lon/lat')
    return [(a, g[0], g[1]) for a, g in zip(attrs, geoms) if a is not None and g[1]]

def open_zip(path):
    z = zipfile.ZipFile(path)
    if find_member(z, 'S_FIRM_PAN', '.shp'):
        return z
    for n in z.namelist():  # nested zip
        if n.lower().endswith('.zip'):
            inner = zipfile.ZipFile(io.BytesIO(z.read(n)))
            if find_member(inner, 'S_FIRM_PAN', '.shp'):
                return inner
    raise ValueError(path + ': no S_FIRM_PAN shapefile found - is this the "NFHL Data-County" download?')

# ---------------------------------------------------------------- build
def bbox_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])

def clip_ring(ring, b):
    """Sutherland-Hodgman clip of one ring to rectangle b=[minx,miny,maxx,maxy].
    Point-in-polygon answers for points inside b are unchanged by the clip."""
    def clip(pts, inside, cross):
        out = []
        for i in range(len(pts)):
            cur, prev = pts[i], pts[i - 1]
            if inside(cur):
                if not inside(prev): out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
        return out
    def xcut(x):
        return lambda p, q: [x, round(p[1] + (q[1] - p[1]) * (x - p[0]) / (q[0] - p[0]), PREC)]
    def ycut(y):
        return lambda p, q: [round(p[0] + (q[0] - p[0]) * (y - p[1]) / (q[1] - p[1]), PREC), y]
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring[:]
    pts = clip(pts, lambda p: p[0] >= b[0], xcut(b[0]))
    if pts: pts = clip(pts, lambda p: p[0] <= b[2], xcut(b[2]))
    if pts: pts = clip(pts, lambda p: p[1] >= b[1], ycut(b[1]))
    if pts: pts = clip(pts, lambda p: p[1] <= b[3], ycut(b[3]))
    if len(pts) < 3: return None
    return pts + [pts[0]]

def ring_bbox(rings):
    xs = [p[0] for r in rings for p in r]; ys = [p[1] for r in rings for p in r]
    return [min(xs), min(ys), max(xs), max(ys)]

def load_json(p, default):
    try:
        with open(p) as f: return json.load(f)
    except FileNotFoundError:
        return default

def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f: json.dump(obj, f, separators=(',', ':'))

def process(path):
    z = open_zip(path)
    panels = load_layer(z, 'S_FIRM_PAN')
    zones = load_layer(z, 'S_FLD_HAZ_AR')
    if not panels or not zones:
        raise ValueError(path + ': missing S_FIRM_PAN or S_FLD_HAZ_AR')
    # source key = the DFIRM_ID(s) in this download, used to replace this county's data on re-runs
    key = sorted({str(a.get('DFIRM_ID', '')).strip() for a, _, _ in zones if a.get('DFIRM_ID')})
    key = ','.join(key) or os.path.basename(path)
    st_fips = str(panels[0][0].get('ST_FIPS') or str(panels[0][0].get('FIRM_PAN', ''))[:2]).zfill(2)[:2]
    st = STATE_ABBR.get(st_fips, st_fips)

    ppath = os.path.join(DATA, st, 'panels.json')
    pdoc = load_json(ppath, {'panels': {}})
    new_panels = {}
    for a, bb, rings in panels:
        fp = str(a.get('FIRM_PAN', '')).strip().upper()
        m = re.match(r'^(\d{5}[C0-9]\d{4})([A-Z])?$', fp)
        if not m:
            continue
        p10 = m.group(1); suffix = m.group(2) or str(a.get('SUFFIX', '')).strip().upper()
        new_panels[p10] = {'p': p10 + suffix, 'd': a.get('EFF_DATE') or None, 't': a.get('PANEL_TYP') or None,
                           'b': [round(v, PREC) for v in bb], 'r': rings, 'k': key}
    pdoc['panels'].update(new_panels)

    # zone tiles: one file per panel; keep other counties' polygons, replace this county's
    kept = removed = 0
    per_panel = {p: [] for p in new_panels}
    for a, bb, rings in zones:
        zone = str(a.get('FLD_ZONE', '')).strip()
        sub = str(a.get('ZONE_SUBTY', '')).strip()
        if zone == 'X' and 'MINIMAL' in sub.upper():
            removed += 1; continue   # the default everywhere else inside a panel
        for p10, pe in new_panels.items():
            if not bbox_overlap(bb, pe['b']):
                continue
            box = pe['b']
            if bb[0] >= box[0] and bb[1] >= box[1] and bb[2] <= box[2] and bb[3] <= box[3]:
                crings = rings                                   # wholly inside this panel
            else:
                pad = 0.0005                                     # ~50 m margin so edge points still resolve
                cb = [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad]
                crings = [c for c in (clip_ring(r, cb) for r in rings) if c]
            if not crings:
                continue
            per_panel[p10].append({'z': zone, 's': sub or None, 'b': [round(v, PREC) for v in ring_bbox(crings)],
                                   'r': crings, 'k': key})
            kept += 1
    for p10, feats in per_panel.items():
        tpath = os.path.join(DATA, st, 'z', p10 + '.json')
        old = [f for f in load_json(tpath, []) if f.get('k') != key]
        save_json(tpath, old + feats)
    pdoc['updated'] = datetime.date.today().isoformat()
    save_json(ppath, pdoc)

    idx = load_json(os.path.join(DATA, 'index.json'), {'sources': {}})
    dates = sorted({v['d'] for v in new_panels.values() if v['d']}, key=lambda s: s[6:] + s[:2] + s[3:5])
    idx['sources'][key] = {'state': st, 'file': os.path.basename(path), 'panels': len(new_panels),
                           'zones': kept, 'latest_panel_date': dates[-1] if dates else None,
                           'loaded': datetime.date.today().isoformat()}
    save_json(os.path.join(DATA, 'index.json'), idx)
    print('%s -> %s: %d panels, %d zone pieces (%d minimal-X skipped), key %s'
          % (os.path.basename(path), st, len(new_panels), kept, removed, key))

if __name__ == '__main__':
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(ROOT, 'incoming', '*.zip')))
    if not files:
        sys.exit('no zip files given / found in incoming/')
    for f in files:
        process(f)
