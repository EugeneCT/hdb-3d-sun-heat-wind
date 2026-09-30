
# Solar 3D building map

Produce one self-contained HTML file: a pannable/tiltable 3D map where real
buildings are extruded, a subset ("targets") are highlighted, and a physically
grounded sun engine drives facade lighting, moving ground shadows, and an optional
per-wall **solar-heat** colouring with **inter-building shadow occlusion**. It runs
in a normal browser (needs internet for the map library + basemap tiles; all
building data is baked into the file).

This is a real, worked pipeline — not a sketch. The scripts here are the exact,
generalised tools; the reference docs hold the maths and the non-obvious traps that
cost hours to rediscover. **Read `notes/maplibre-and-data-gotchas.md` before
writing or debugging any map code** — one of those bugs (ring winding) makes target
buildings silently invisible with no error.

## When to reach for this

Building-scale sun/shadow/heat questions on a real map: "show these HDB blocks in
3D with which facades bake in the afternoon", "shadow study of this street", "3D
map of the neighbourhood coloured by sun exposure", "which units face west".
It is *not* for abstract charts (use a charting skill) or for a single building's
interior.

## The pipeline (5 stages)

Do them in order; each stage's output feeds the next. Put working files in a scratch
dir; the only final artefact is the HTML.

### 1 — Fetch the surrounding buildings (OSM)
`fetch_osm_buildings.py` queries the Overpass API for building footprints
in a bbox and writes GeoJSON with a `hgt` (metres) property per building.

```
python fetch_osm_buildings.py --bbox S,W,N,E --out context.geojson
```

Height comes from the `height` tag, else `building:levels`×3.2, else a default.
Overpass frequently returns a tiny HTML "server too busy" page instead of JSON —
the script retries with backoff. If it still fails, wait and rerun; don't switch to
guessing coordinates.

### 2 — Identify and place the target buildings
Targets are the highlighted subset (a project, a cluster of blocks). Each needs a
**footprint ring (lon/lat), a height, a group (for colour), and — if you want the
heat/facing features — a facings list**. Three ways to get the footprint, in order
of preference:

1. **Already in OSM** — match by `addr:housenumber` (or name). New estates are often
   tagged `building=construction` with the real articulated footprint. This is
   exact — always prefer it. Exclude these ways from the context layer so they don't
   double-draw in grey under your colour.
2. **Trace from a PDF/site plan** — see stage 3.
3. **Approximate box** — from a geocoded centroid + orientation + length/width, as a
   last resort. Label it clearly as approximate in the popup.

Write the targets to `targets.geojson`, one Polygon feature each, with properties
`{name, group, storeys, facings}` (facings e.g. `["N","S","W"]` = the compass
directions the habitable/living-room walls face). Confirm the geocoded position by
eye against the site plan (nearest OSM building, right road) before trusting it.

### 3 — Trace a missing footprint from a PDF (only if needed)
`trace_pdf_footprint.py` extracts a building outline from a floor-plan or
site-plan page and georeferences it. The floor plan (rooms drawn contiguous) works
better than a site plan (units drawn spaced apart). Method: threshold the coloured
room fills → morphological close to bridge the corridors → largest blob → outer
contour → simplify → PCA for orientation → scale (calibrate the long axis against a
*sibling* building whose real size you know from OSM) → rotate to the estate's
bearing → centre on the geocoded centroid. It is approximate; say so. Read the
script header for the exact knobs (target major-axis metres, rotation, centroid).

### 4 — Generate the map
`build_sun_heat_map.py` consumes `context.geojson`, `targets.geojson`, and a
small config (centre, **latitude**, view angles, group colours) and emits the HTML
with the full engine: 3D extrusions, per-group target colours, HTML-marker labels,
living-room **facing strips**, a **🔥 Heat** toggle (per-wall solar gain at the
current time, red→blue), a **🌑 Shade** toggle (folds inter-building shadow occlusion
into the heat so lower floors shaded by a neighbour go blue), and a **🌬 Wind** toggle
(facade exposure to the prevailing monsoon wind for the selected month — windward/
breezy = teal, sheltered = grey; Singapore MSS climatology, tied to the Month slider).
Heat and Wind are mutually exclusive on the shared wall-band layer. A **Floor** slider highlights one floor (a gold ring at that level on every target)
and reads out its exact per-facade sun/shade/wind at the current time. Config knobs
(edit the `CONFIG` block or pass `--config config.json`): `center`, `latitude_deg`,
`zoom/pitch/bearing`, `groups` (colours); **`band_metres`** — heat band height, set to
the storey height (~3) for **per-floor** resolution or larger (~22) for fewer features
with many buildings; **`tz_offset`** — the clock timezone (Singapore **+8**), required
for the clock→solar time correction so times match reality (see §1 of the solar
reference — without it "4 PM" showed ~2:50 PM sun); **`storey_metres`** (~3.05) for the
floor slider. Heat/Wind keep the solid building model + roof visible (the coloured
strips sit just outside the walls), so the massing isn't lost.

```
python build_sun_heat_map.py --context context.geojson \
    --targets targets.geojson --config config.json --out map.html
```

The solar maths (sun position, insolation, shadow projection, occlusion) is
documented in `notes/solar-and-geometry.md`; the script implements it for the
latitude you set. For a location far from the equator, only the latitude changes —
the physics is the same.

### 5 — Verify it actually renders
There is usually **no browser MCP available and no display**, so you cannot "just
open it". `verify_headless.py` drives a headless Chromium (Playwright's
bundled one) to load the file, capture console errors, screenshot it, and report how
many target/heat features actually rendered. **Always run this** — it catches the
invisible-building winding bug, glyph 404s, and layer errors that produce a
silently-broken map.

```
python verify_headless.py --html map.html --out shot.png \
    [--click '#heat'] [--set-time 17] [--jump lon,lat,zoom,pitch,bearing]
```

`queryRenderedFeatures` returning 0 for your target layer means the buildings are
present in the source but not drawing — almost always the ring-winding bug. Fix and
re-verify. Screenshot from an angle that faces the *sunlit* walls when checking heat
(a fixed camera shows the front walls heating in the morning and cooling by evening —
that's correct, not a bug).

## Key design decisions (carry these forward)

- **Heat is instantaneous, not annual.** Colour each wall by the solar gain it
  receives *at the selected time*, recomputed as the slider moves — that's what
  users mean by "does this wall get hot". An annual total is static and reads as
  broken ("the wall doesn't change by time of day?").
- **Split walls into vertical bands** so shadow occlusion can shade lower floors
  while upper floors stay hot — the compelling, honest visual. One colour per whole
  wall can't show that.
- **Shadowed ≠ zero.** Keep a small residual (~12%) on shaded walls; diffuse sky
  still warms them.
- **Labels via HTML markers, not a symbol layer** — symbol layers need glyph
  fonts from a server that 404s; markers never do.
- **State your approximations in the UI** (traced/box footprints, no-shadow heat,
  internet dependency). Honesty in the artefact itself.

## Honest limits to surface to the user
Needs internet (library + tiles). OSM heights are as-tagged (many default).
Traced/box footprints are approximate. Shade recompute costs ~0.3 s per time step,
so "Play" is choppy with Shade on; per-floor `band_metres` multiplies band count, so
keep it coarse for many-building maps. Casters are limited to nearby tall buildings.
**Wind** is facade *exposure to the seasonal prevailing direction* (monsoon
climatology, `WINDFROM` in the template) — not a live reading and not a flow/CFD
simulation; it ignores how buildings deflect wind into wakes. Say this plainly. If
the user wants live wind, Singapore's data.gov.sg realtime API
(`api.data.gov.sg/v1/environment/wind-direction` + `wind-speed`) can seed the current
direction at build time.
