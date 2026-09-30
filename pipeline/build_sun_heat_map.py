#!/usr/bin/env python3
"""Generate a self-contained 3D sun/shadow/heat map (MapLibre GL) from GeoJSON.

Inputs:
  --context  GeoJSON of surrounding buildings (Polygon features, property `hgt` m)
  --targets  GeoJSON of the highlighted buildings. Each Polygon feature needs
             properties: name, group, storeys (or hgt), facings (e.g. ["N","S","W"]).
             Optional `osm_id` -> that id is removed from the context layer so it
             isn't drawn twice.
  --config   JSON: {center:[lon,lat], latitude_deg, zoom, pitch, bearing,
             groups:{groupname:{label,color}}, title}   (all optional; see DEFAULTS)
  --out      output .html

The heat runtime is INSTANTANEOUS (per-wall solar gain at the current Time/Month,
recomputed live) with an optional inter-building shadow-occlusion toggle. Physics is
in references/solar-and-geometry.md. Everything except the map library + basemap
tiles is baked into the HTML.
"""
import argparse, json, math

FCOL = {"N": "#3b7fc4", "E": "#12a08b", "S": "#b5872a", "W": "#d0603a"}
CARD = {"N": 0, "E": 90, "S": 180, "W": 270}
DEFAULTS = {"center": None, "latitude_deg": 1.35, "zoom": 15.6, "pitch": 58,
            "bearing": -16, "groups": {}, "title": "3D buildings — sun & heat",
            "band_metres": 22,   # heat vertical band height; ~3 = per floor (finer = more features)
            "tz_offset": 8,      # clock timezone offset (Singapore = +8); for the solar-time fix
            "storey_metres": 3.05}  # assumed floor-to-floor height (for the floor selector)


def rnd(c):
    return [round(c[0], 6), round(c[1], 6)]


def ensure_ccw(ring):
    a = sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
            for i in range(len(ring)))
    return ring[::-1] if a < 0 else ring


def hsl_rgb(h, s, l):
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
    return (round((r + m) * 255), round((g + m) * 255), round((b + m) * 255))


def heat_lut(lat_deg):
    """Annual direct-beam irradiation on a vertical wall vs azimuth, normalised 0..1.
    Used only for the initial (pre-interaction) colour; runtime recolours live."""
    LAT = math.radians(lat_deg)
    grid = [math.radians(a) for a in range(0, 360, 5)]
    acc = [0.0] * len(grid)
    for day in range(1, 366, 6):
        dec = math.radians(23.44) * math.sin(2 * math.pi * (284 + day) / 365)
        for mn in range(0, 1440, 15):
            H = math.radians(15 * (mn / 60 - 12))
            sel = math.sin(LAT) * math.sin(dec) + math.cos(LAT) * math.cos(dec) * math.cos(H)
            if sel <= 0.02:
                continue
            el = math.asin(sel)
            az = math.atan2(math.sin(H), math.cos(H) * math.sin(LAT) - math.tan(dec) * math.cos(LAT)) + math.pi
            dni, ce = math.exp(-0.15 / math.sin(el)), math.cos(el)
            for i, A in enumerate(grid):
                ci = ce * math.cos(az - A)
                if ci > 0:
                    acc[i] += dni * ci
    lo, hi = min(acc), max(acc)
    return [(v - lo) / (hi - lo) for v in acc]


def heat_at(lut, az):
    x = (az % 360) / 5.0
    i = int(x) % 72
    return lut[i] * (1 - (x - int(x))) + lut[(i + 1) % 72] * (x - int(x))


def heat_color(v):
    return "#%02x%02x%02x" % hsl_rgb((1 - max(0, min(1, v))) * 240.0, 0.72, 0.5)


def _edges(ring):
    """Yield (p1, p2, outward_bearing_deg, length_m) for a CCW ring's edges."""
    lat0 = ring[0][1]
    coslat = math.cos(math.radians(lat0))
    for i in range(len(ring) - 1):
        p1, p2 = ring[i], ring[i + 1]
        dE = (p2[0] - p1[0]) * 111320 * coslat
        dN = (p2[1] - p1[1]) * 110540
        ln = math.hypot(dE, dN)
        if ln < 2:
            continue
        oE, oN = dN / ln, -dE / ln            # outward normal (CCW ring)
        az = (math.degrees(math.atan2(oE, oN)) + 360) % 360
        yield p1, p2, oE, oN, az, ln, coslat


def facing_strips(ring, hgt, faces):
    faceset, out = set(faces or []), []
    for p1, p2, oE, oN, az, ln, coslat in _edges(ring):
        if ln < 2:
            continue
        best = min(CARD, key=lambda c: min(abs(az - CARD[c]), 360 - abs(az - CARD[c])))
        if min(abs(az - CARD[best]), 360 - abs(az - CARD[best])) > 28 or best not in faceset:
            continue
        oLon, oLat = oE * 2 / (111320 * coslat), oN * 2 / 110540
        strip = ensure_ccw([rnd(p1), rnd(p2), rnd([p2[0] + oLon, p2[1] + oLat]),
                            rnd([p1[0] + oLon, p1[1] + oLat])])
        out.append({"type": "Feature", "properties": {"col": FCOL[best], "hgt": hgt + 1.5},
                    "geometry": {"type": "Polygon", "coordinates": [strip + [strip[0]]]}})
    return out


def heat_strips(ring, hgt, lut, band_m=22.0):
    """One vertical band feature per `band_m` of wall height, so shadow occlusion can
    shade lower floors independently. band_m ~= storey height gives per-floor
    resolution (finer = a smoother gradient but more features to recolour each step).
    Each carries az + a sample point (clon,clat)."""
    out = []
    nb = max(1, round(hgt / band_m))
    for p1, p2, oE, oN, az, ln, coslat in _edges(ring):
        if ln < 3:
            continue
        v = heat_at(lut, az)
        oLon, oLat = oE * 1.5 / (111320 * coslat), oN * 1.5 / 110540
        strip = ensure_ccw([rnd(p1), rnd(p2), rnd([p2[0] + oLon, p2[1] + oLat]),
                            rnd([p1[0] + oLon, p1[1] + oLat])])
        geom = [strip + [strip[0]]]
        clon = (p1[0] + p2[0]) / 2 + oLon * 0.5
        clat = (p1[1] + p2[1]) / 2 + oLat * 0.5
        for k in range(nb):
            z0, z1 = k * hgt / nb, (k + 1) * hgt / nb
            out.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": geom},
                        "properties": {"col": heat_color(v), "az": round(az, 1),
                                       "base": round(z0, 1), "top": round(z1, 1),
                                       "zmid": round((z0 + z1) / 2, 1),
                                       "clon": round(clon, 6), "clat": round(clat, 6)}})
    return out


def build(context, targets, cfg, out):
    lut = heat_lut(cfg["latitude_deg"])
    tgt_osm_ids = {f["properties"].get("osm_id") for f in targets["features"]}
    ctx = [f for f in context["features"] if f["properties"].get("osm_id") not in tgt_osm_ids]

    ours, strips, heat = [], [], []
    for f in targets["features"]:
        p = f["properties"]
        ring = ensure_ccw([rnd(c) for c in f["geometry"]["coordinates"][0]])
        ring = ring + [ring[0]]
        hgt = float(p.get("hgt") or p.get("storeys", 20) * 3.0)
        ours.append({"type": "Feature",
                     "properties": {"name": p.get("name", ""), "group": p.get("group", ""),
                                    "storeys": p.get("storeys", round(hgt / 3)), "hgt": hgt,
                                    "src": p.get("src", "osm")},
                     "geometry": {"type": "Polygon", "coordinates": [ring]}})
        strips += facing_strips(ring, hgt, p.get("facings"))
        heat += heat_strips(ring, hgt, lut, cfg.get("band_metres", 22))

    center = cfg["center"] or _centroid(ours or ctx)
    color_expr = ["match", ["get", "group"]]
    legend = []
    for g, meta in cfg["groups"].items():
        color_expr += [g, meta["color"]]
        legend.append(f'<span class="sw" style="background:{meta["color"]}"></span><b>{meta.get("label", g)}</b>')
    color_expr.append("#888")
    if len(color_expr) == 3:                       # no groups configured
        color_expr = "#0f635a"

    sh = cfg.get("storey_metres", 3.05)
    maxfloor = max((round(f["properties"]["hgt"] / sh) for f in ours), default=1)
    html = (TEMPLATE
            .replace("{{TITLE}}", cfg["title"])
            .replace("{{LATd}}", repr(cfg["latitude_deg"]))
            .replace("{{TZ}}", repr(cfg.get("tz_offset", 8)))
            .replace("{{MAXFLOOR}}", str(maxfloor))
            .replace("{{STOREYH}}", repr(sh))
            .replace("{{CENTER}}", json.dumps(center))
            .replace("{{ZOOM}}", repr(cfg["zoom"])).replace("{{PITCH}}", repr(cfg["pitch"]))
            .replace("{{BEARING}}", repr(cfg["bearing"]))
            .replace("{{COLOR}}", json.dumps(color_expr))
            .replace("{{LEGEND}}", " · ".join(legend) or "<b>Targets</b>")
            .replace("{{CTX}}", json.dumps({"type": "FeatureCollection", "features": ctx}))
            .replace("{{OURS}}", json.dumps({"type": "FeatureCollection", "features": ours}))
            .replace("{{STRIPS}}", json.dumps({"type": "FeatureCollection", "features": strips}))
            .replace("{{HEAT}}", json.dumps({"type": "FeatureCollection", "features": heat})))
    open(out, "w").write(html)
    print(f"wrote {out}: {len(ctx)} context, {len(ours)} targets, "
          f"{len(strips)} facing strips, {len(heat)} heat bands")


def _centroid(feats):
    xs, ys = [], []
    for f in feats:
        for c in f["geometry"]["coordinates"][0]:
            xs.append(c[0]); ys.append(c[1])
    return [sum(xs) / len(xs), sum(ys) / len(ys)]


TEMPLATE = open(__file__.replace("build_sun_heat_map.py", "map_template.html")).read() \
    if __import__("os").path.exists(__file__.replace("build_sun_heat_map.py", "map_template.html")) \
    else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)
    ap.add_argument("--targets", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = dict(DEFAULTS)
    if a.config:
        cfg.update(json.load(open(a.config)))
    if TEMPLATE is None:
        raise SystemExit("map_template.html must sit next to this script")
    build(json.load(open(a.context)), json.load(open(a.targets)), cfg, a.out)


if __name__ == "__main__":
    main()
