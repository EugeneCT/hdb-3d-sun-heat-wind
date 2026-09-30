# MapLibre + data gotchas (read before writing or debugging map code)

These are the traps that cost real hours. Most produce a *silently* broken map — no
error, just missing/wrong output — so you can't rely on "it didn't throw".

## 1. Ring winding — the invisible-building bug (the worst one)
MapLibre / geojson-vt treats a polygon's **first ring as the exterior only if it is
counter-clockwise**. A lone **clockwise** ring is read as a *hole* → the feature has
no fill area → it renders **nothing**, and `querySourceFeatures()` returns **0**, with
**no console error**. OSM footprints happen to be CCW so they work; hand-built rings
(boxes, traced polygons, offset wall strips) are easily CW.

**Always enforce CCW on every ring you construct.** Signed area
`Σ(x_i·y_{i+1} − x_{i+1}·y_i)`; if negative, reverse. The scripts do this
(`ensure_ccw`). If `verify_headless.py` reports `ours3d: 0` while the source has
features, this is it, 95% of the time.

## 2. You cannot "just open it" — verify headless
No browser MCP, no display. Use `scripts/verify_headless.py` (Playwright's bundled
Chromium). It captures console errors, screenshots, and — crucially —
`queryRenderedFeatures` counts per layer, which is the only way to catch bug #1 and
layer errors. Screenshot from an angle facing the **sunlit** walls when checking
heat; a fixed camera correctly shows front walls hot in the morning, cool by evening.

## 3. Labels: HTML markers, not a symbol layer
A `symbol` layer needs glyph PBFs from a `glyphs` URL. Public demo glyph servers
404 for many fontstacks → labels vanish + console 404s. Use `maplibregl.Marker`
with a styled `<div>` per label — no font server, always works. Don't set `glyphs`
in the style if you have no symbol layers.

## 4. Per-face colour needs separate geometry
`fill-extrusion-color` colours a whole building one colour — you cannot colour
individual walls of a single extrusion. To colour walls (by facing, by heat), build
**one thin extruded strip per wall edge** (offset ~1.5 m outward, extruded to the
building height) as separate features, coloured individually. Vertical gradients
(shadowed lower floors) need **bands**: separate features with
`fill-extrusion-base` + `fill-extrusion-height`.

## 5. Live recolouring: mutate the GeoJSON and setData
MapLibre paint expressions can't read "the current sun". To recolour walls each time
step, keep the heat FeatureCollection in JS, rewrite each feature's `col` (and for
shadows, rebuild the shadow FeatureCollection), then `source.setData(fc)`. Same
pattern drives the moving ground shadows.

## 6. Don't double-draw targets
If a target's footprint came from an OSM way, that same way is also in the context
layer → it renders grey under your colour (z-fighting / wrong colour peeking). Carry
the `osm_id` on the target and **exclude those ids from the context layer**.

## 7. Facade lighting via setLight (not real shadows)
`map.setLight({anchor:'map', position:[r, azimuth_deg, polar_deg]})` shades the
extrusion faces by a directional light. Set `azimuth = sun azimuth`,
`polar = 90 − elevation`. Keep intensity modest (~0.32) or shaded faces go so dark
the target colours disappear. MapLibre does **not** cast real inter-building shadows
on walls — that's why we compute ground shadows and wall occlusion ourselves.

## 8. Internet + CSP
The file needs internet for the MapLibre library (unpkg) and basemap tiles
(CARTO/OSM raster). All building data is baked in, so buildings show even if tiles
lag. This means it will **not** work as a sandboxed artifact with a strict CSP — it's
a standalone local HTML file. Say so to the user. Wrap tile/library failure in the
`err` div and `map.on('error',()=>{})` so a tile hiccup doesn't blank the page.

## 9. Overpass is flaky
Expect intermittent tiny HTML "server too busy / runtime error" pages instead of
JSON. Retry with backoff and try a mirror (`fetch_osm_buildings.py` does). Don't
respond to a busy server by abandoning real data for guessed coordinates.

## 10. New buildings may be tagged `building=construction`
Recently built estates are often present in OSM as `building=construction`, tagged
with `addr:housenumber` and the real articulated footprint. Match targets by
housenumber; don't assume "not in OSM" just because `building=yes` isn't there.
Verify a geocoded centroid against the site plan before trusting it (a housenumber
like "52" may be the car park, not the residential "52A").
