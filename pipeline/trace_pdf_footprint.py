#!/usr/bin/env python3
"""Trace one building's footprint from a PDF floor-plan page and georeference it.

Use only when OSM has no footprint for a target building. A *floor-plan* page
(rooms drawn contiguous, sharing walls) works; a *site plan* (units drawn spaced
with gaps) usually does not connect into one blob.

Method: render the page -> threshold the coloured room fills -> morphological close
to bridge the internal corridors/cores -> take the largest blob -> fill holes ->
outer contour -> simplify -> PCA for the drawing-orientation -> scale so the major
axis matches a real length (calibrate against a SIBLING building you can measure in
OSM) -> rotate to the estate's real bearing -> centre on the geocoded centroid.
The result is APPROXIMATE — label it "traced (approx)" in the target properties.

Deps: pymupdf, numpy, scipy, scikit-image, pillow  (pip install as needed).

Usage:
  python trace_pdf_footprint.py --pdf plan.pdf --page 17 \
      --centroid 103.79067,1.30897 --major-metres 66 --rotate -8 \
      --out footprint.geojson
Tuning knobs (inspect the saved preview PNG and adjust):
  --close 61 --close-iter 3   (bigger connects far wings but blurs notches)
  --tol 22                    (contour simplification tolerance, px)
Then place the ring into targets.geojson with name/group/storeys/facings and
src="traced".
"""
import argparse, json, math
import numpy as np
import fitz
from scipy import ndimage
from skimage import measure
from PIL import Image


def extract(pdf, page, dpi, close, close_iter, tol, preview):
    pg = fitz.open(pdf)[page - 1]
    pix = pg.get_pixmap(dpi=dpi)
    im = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].astype(int)
    sat = im.max(2) - im.min(2)
    coloured = (sat > 45) & (im.max(2) > 110) & (im.min(2) < 235)  # room fills, not black/white
    cc = ndimage.binary_closing(coloured, np.ones((close, close)), iterations=close_iter)
    cc = ndimage.binary_fill_holes(cc)
    lab, n = ndimage.label(cc)
    sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
    foot = lab == int(np.argmax(sizes)) + 1
    cont = max(measure.find_contours(foot.astype(float), 0.5), key=len)
    poly = measure.approximate_polygon(cont, tolerance=tol)      # (row,col)=(y,x)
    pts = np.column_stack([poly[:, 1], poly[:, 0]]).astype(float)  # (x,y)
    if preview:
        ys, xs = np.where(foot)
        img = Image.fromarray((foot * 70 + 30).astype(np.uint8)).convert("RGB")
        from PIL import ImageDraw
        ImageDraw.Draw(img).line([tuple(p) for p in pts] + [tuple(pts[0])], fill=(255, 60, 60), width=6)
        img.crop((xs.min() - 40, ys.min() - 40, xs.max() + 40, ys.max() + 40)).save(preview)
    return pts


def georeference(pts, lon0, lat0, major_m, rotate_deg):
    c = pts.mean(0); d = pts - c
    cov = np.cov(d.T); w, v = np.linalg.eigh(cov)
    maj = v[:, np.argmax(w)]; pmaj = d @ maj
    scale = major_m / (pmaj.max() - pmaj.min())
    rot = math.radians(rotate_deg); ca, sa = math.cos(rot), math.sin(rot)
    coslat = math.cos(math.radians(lat0))
    ring = []
    for x, y in pts:
        e = (x - c[0]) * scale; nth = -(y - c[1]) * scale        # floor plan north-up: east=+x, north=-y
        e2 = e * ca - nth * sa; n2 = e * sa + nth * ca
        ring.append([round(lon0 + e2 / (111320 * coslat), 6), round(lat0 + n2 / 110540, 6)])
    a = sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
            for i in range(len(ring)))
    if a < 0:
        ring = ring[::-1]
    ring.append(ring[0])
    return ring


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--centroid", required=True, help="lon,lat of the building's real centre")
    ap.add_argument("--major-metres", type=float, required=True, help="real long-axis length (m)")
    ap.add_argument("--rotate", type=float, default=0.0, help="deg CW to align drawing to true north")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--close", type=int, default=61)
    ap.add_argument("--close-iter", type=int, default=3)
    ap.add_argument("--tol", type=float, default=22)
    ap.add_argument("--preview", default="footprint_preview.png")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    lon0, lat0 = [float(x) for x in a.centroid.split(",")]
    pts = extract(a.pdf, a.page, a.dpi, a.close, a.close_iter, a.tol, a.preview)
    ring = georeference(pts, lon0, lat0, a.major_metres, a.rotate)
    json.dump({"type": "Feature", "properties": {"src": "traced"},
               "geometry": {"type": "Polygon", "coordinates": [ring]}}, open(a.out, "w"))
    print(f"wrote {a.out}: {len(ring)-1}-vertex ring | preview {a.preview} "
          f"(inspect it; adjust --close / --rotate / --major-metres and rerun)")


if __name__ == "__main__":
    main()
