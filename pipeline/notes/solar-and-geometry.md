# Solar position, insolation, shadows, occlusion — the maths

All of this is implemented in `scripts/build_sun_heat_map.py` (Python, for the baked
strips) and `scripts/map_template.html` (JS, for the live runtime). This file
explains *why* each formula is what it is, so you can adapt or debug confidently.
Coordinate frame everywhere: **x = east, y = north, z = up**; azimuth measured
clockwise from north (N=0, E=90, S=180, W=270).

## Table of contents
1. Sun position (any latitude)
2. Why the equator behaves oddly
3. Direct-beam irradiance on a wall (the heat number)
4. Ground shadow projection
5. Vertical bands + ray-vs-prism occlusion (shade in heat)
6. Metres ↔ lon/lat

## 1. Sun position
Given day-of-year `N` and local solar hour `hr`, latitude `φ`:
```
δ (declination) = 23.44° · sin(2π·(284+N)/365)
H (hour angle)  = 15°·(hr − 12)
elevation el    = asin( sinφ·sinδ + cosφ·cosδ·cosH )
azimuth   az    = atan2( sinH , cosH·sinφ − tanδ·cosφ ) + π   → 0=N,90=E,180=S,270=W
```
The `+π` puts the origin at north.

**Critical real-data correction — clock time ≠ solar time.** `H = 15°·(hr−12)` is only
right if `hr` is *solar* time. Clock time differs by two things: the **longitude
offset** from the timezone's standard meridian (`4 min × (longitude − 15°·tz_offset)`)
and the **equation of time** (±16 min). Singapore is at ~103.8°E but uses UTC+8
(meridian 120°E), so its real solar noon is ~**13:10**, not 12:00 — verified against
ephemeris (sunrise-sunset.org: sunrise 07:04 / solar noon 13:10 / sunset 19:17 for
2026-08-05; the naive model gave 05:58 / 12:00 / 18:02, ~1h10m early). Always convert:
```
B   = 2π·(dayN − 81)/364
EoT = 9.87·sin(2B) − 7.53·cos(B) − 1.5·sin(B)          # minutes
TC  = 4·(longitude − 15·tz_offset) + EoT               # minutes, clock → solar
solar_hr = clock_hr + TC/60      → use solar_hr in H
```
This lands solar noon on 13:10 to the minute (the ~5 min residual on sunrise/sunset is
atmospheric refraction, which ephemeris sites include and a geometric model doesn't).
The runtime needs the map's **longitude** and **tz_offset** (config; +8 for Singapore —
you cannot derive tz from longitude, since Singapore deliberately runs an hour ahead of
its solar zone). The annual `heat_lut` integrates over solar hours already, so it's
unaffected. Change `φ`, `longitude`, `tz_offset` for anywhere else.

## 2. Why the equator behaves oddly (sanity check your intuition)
Near the equator the sun passes almost overhead and swings ±23.5° in declination
across the year. Consequences that look like bugs but are correct:
- At noon the sun is slightly **north** of zenith Apr–Aug (δ>φ) and **south** Oct–Feb.
- **Every** facing gets roughly the same *total* annual sun (~6 h/day). What differs
  is *when*: a west wall gets all its sun in the hot afternoon, an east wall all in
  the cool morning, north/south walls get grazing high-angle sun. So for comfort:
  **N ≈ best, W = worst**, even though annual insolation E≈W. Say this to users who
  expect "west = most sun overall".

## 3. Direct-beam irradiance on a wall (the heat colour)
Vertical wall, outward normal at azimuth `A`. Sun unit vector toward the sun is
`(cos·el·sin·az, cos·el·cos·az, sin·el)`. Cosine of incidence on the wall =
`cos(el)·cos(az − A)`. Clear-sky direct-normal (relative) with air-mass attenuation:
`dni ≈ exp(−0.15 / sin(el))`. So instantaneous wall gain:
```
heat = dni · max(0, cos(el)·cos(az − A))     (0 when sun below horizon or behind wall)
```
Normalise by ~0.62 (the peak of `dni·cos(el)` over the day, reached ~30° elevation)
so a wall square-on to the sun hits full red. Colour by HSL hue = `(1−v)·240°`
(240=blue cool → 0=red hot).

**Make it instantaneous, recomputed as Time moves.** An annual-total heat map is
static and users read that as broken ("the wall doesn't change by time of day?").
The annual LUT (`heat_lut`) is only for the initial pre-interaction colour.

## 4. Ground shadow projection
A point P at height z casts to the ground along the sun ray. Horizontal offset =
`−z · cot(el) · (sin·az, cos·az)` (opposite the sun; length grows as the sun lowers).
A building's ground shadow ≈ the **convex hull of its footprint corners plus those
corners offset by (offset·height)**. **Clamp `cot(el)` (≈ ≤12)** or at sunset the
shadow becomes kilometres long and floods the map.

## 5. Vertical bands + occlusion (Shade-in-heat)
To show lower floors shaded while upper floors stay hot, split each wall into ~22 m
vertical bands (fill-extrusion `base`/`height`). A band sample point P=(x,y,z_mid) is
in shadow if a neighbouring prism blocks the sun:
- Cast the horizontal ray from (x,y) toward the sun azimuth.
- For each caster building (footprint edges in metres, roof height h_B): find the
  nearest edge-crossing distance `d` (2D ray–segment). If `d>0` and
  `h_B ≥ z_mid + d·tan(el)`, the sun ray is blocked → shadowed.
- Shadowed heat *= ~0.12 (residual diffuse sky), not 0.
2D ray–segment: ray `P+t·dir`, segment `A + u·(B−A)`; with `den = dir×(B−A)`,
`t = (A−P)×(B−A)/den`, `u = (A−P)×dir/den`; hit if `t>ε, 0≤u≤1`.

**Performance:** limit casters to buildings ≥22 m within ~250 m. Even so, occlusion
over ~1000 bands costs ~0.3 s per time step — fine for scrubbing, choppy for Play.
Offset the band sample point ~1.5 m outward from the wall so a sun-facing wall never
"self-hits" its own footprint; genuine pinwheel self-shadowing (one wing shading
another of the same building) is still caught because it's one polygon.

## 6. Metres ↔ lon/lat (local planar, fine at neighbourhood scale)
```
east_m  = (lon − lon0)·111320·cos(lat0)      lon = lon0 + east_m /(111320·cos(lat0))
north_m = (lat − lat0)·110540                lat = lat0 + north_m/110540
```
Do occlusion in a single shared metre frame (one origin for the whole scene) so you
convert caster edges once, not per sample.
