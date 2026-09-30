#!/usr/bin/env python3
"""Fetch OSM building footprints in a bbox from the Overpass API -> GeoJSON.

Each output feature is a Polygon with a `hgt` property (metres): from the `height`
tag if present, else building:levels x 3.2, else a default. Ways carrying an
`addr:housenumber` keep it (so downstream can match target buildings by number).

Overpass regularly answers with a small HTML "server too busy / runtime error" page
instead of JSON; this retries with backoff. If it still fails, the mirror is busy —
wait a minute and rerun, don't fall back to guessing coordinates.

Usage:
  python fetch_osm_buildings.py --bbox 1.3025,103.7825,1.3175,103.7975 --out ctx.geojson
  (--bbox is South,West,North,East = minlat,minlon,maxlat,maxlon)
"""
import argparse, json, re, sys, time, urllib.parse, urllib.request

MIRRORS = ["https://overpass-api.de/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter"]


def parse_height(tags):
    for key, mul in (("height", 1.0), ("building:levels", 3.2)):
        v = tags.get(key)
        if v:
            m = re.search(r"[\d.]+", v)
            if m:
                return max(3.0, round(float(m.group()) * mul, 1))
    return 8.0


def query(bbox, timeout):
    s, w, n, e = bbox
    return (f"[out:json][timeout:{timeout}];"
            f'(way["building"]({s},{w},{n},{e});'
            f'relation["building"]({s},{w},{n},{e}););out geom;')


def fetch(bbox, timeout=90, tries=5):
    data = urllib.parse.urlencode({"data": query(bbox, timeout)}).encode()
    for i in range(tries):
        for url in MIRRORS:
            try:
                with urllib.request.urlopen(url, data=data, timeout=timeout + 30) as r:
                    body = r.read().decode("utf-8", "replace")
                if body.lstrip().startswith("{"):
                    return json.loads(body)
                # HTML error page (busy/timeout) -> retry
            except Exception as ex:
                sys.stderr.write(f"  attempt {i+1} {url.split('//')[1][:20]}: {str(ex)[:60]}\n")
        time.sleep(6 * (i + 1))
    raise RuntimeError("Overpass returned no JSON after retries (server busy) — wait and rerun")


def to_features(osm):
    feats = []
    for el in osm["elements"]:
        if el.get("type") != "way" or "geometry" not in el or len(el["geometry"]) < 4:
            continue
        ring = [[round(p["lon"], 6), round(p["lat"], 6)] for p in el["geometry"]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        t = el.get("tags", {})
        props = {"hgt": parse_height(t), "osm_id": el["id"]}
        if t.get("addr:housenumber"):
            props["housenumber"] = t["addr:housenumber"]
        if t.get("name"):
            props["name"] = t["name"]
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return feats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", required=True, help="S,W,N,E (minlat,minlon,maxlat,maxlon)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    bbox = [float(x) for x in a.bbox.split(",")]
    feats = to_features(fetch(bbox))
    json.dump({"type": "FeatureCollection", "features": feats}, open(a.out, "w"))
    named = sum(1 for f in feats if "housenumber" in f["properties"])
    print(f"wrote {a.out}: {len(feats)} buildings ({named} with a housenumber)")


if __name__ == "__main__":
    main()
