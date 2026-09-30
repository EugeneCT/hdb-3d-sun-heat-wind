#!/usr/bin/env python3
"""Render the generated map in a headless Chromium and report whether it worked.

There is usually no browser MCP and no display, so this is how you actually SEE the
map and catch the silent failures: the ring-winding bug (targets present in the
source but 0 rendered), glyph 404s, and layer errors. ALWAYS run this after
generating a map.

It uses Playwright's bundled Chromium if present. If Playwright's package isn't on
sys.path, pass --playwright <path-to-node_modules> (find with:
  find / -maxdepth 8 -path '*/node_modules/playwright-core/package.json' 2>/dev/null )
and it will locate the browser under ~/.cache/ms-playwright.

Usage:
  python verify_headless.py --html map.html --out shot.png
  python verify_headless.py --html map.html --out heat.png --click '#heat' --set-time 17 \
      --jump 103.7905,1.3100,16.8,60,-30
Reports: console errors, and rendered feature counts for ours3d / heat3d (0 = broken).
"""
import argparse, glob, json, os, subprocess, sys, tempfile


def find_chromium():
    for p in glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium*/chrome-linux*/chrome")):
        return p
    for p in ("/opt/google/chrome/chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if os.path.exists(p):
            return p
    return None


def find_playwright():
    for base in glob.glob(os.path.expanduser("~/.npm/_npx/*/node_modules")) + \
            glob.glob("/usr/lib/node_modules") + glob.glob("/usr/local/lib/node_modules"):
        if os.path.exists(os.path.join(base, "playwright-core", "package.json")):
            return base
    return None


NODE = r"""
const {chromium} = require('%PW%/playwright-core');
(async () => {
  const b = await chromium.launch({executablePath:'%CH%', args:['--no-sandbox','--disable-gpu']});
  const p = await b.newPage({viewport:{width:1200,height:850}});
  const errs=[]; p.on('console',m=>{if(m.type()==='error')errs.push(m.text());});
  p.on('pageerror',e=>errs.push('pageerror '+e.message));
  await p.goto('file://%HTML%',{waitUntil:'load',timeout:45000});
  await p.waitForTimeout(5000);
  %JUMP%
  %ACTIONS%
  await p.waitForTimeout(1500);
  const info = await p.evaluate(()=>{const m=window.__map; const q=id=>{try{return m.getLayer(id)?m.queryRenderedFeatures({layers:[id]}).length:'no-layer';}catch(e){return 'err';}};
    return {ours3d:q('ours3d'), heat3d:q('heat3d'), ctx3d:q('ctx3d')};});
  await p.screenshot({path:'%OUT%'});
  console.log(JSON.stringify({rendered:info, errors:errs.slice(0,6)}));
  await b.close();
})();
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--out", default="shot.png")
    ap.add_argument("--click", action="append", default=[], help="CSS selector(s) to click, e.g. #heat")
    ap.add_argument("--set-time", type=float, default=None)
    ap.add_argument("--jump", default=None, help="lon,lat,zoom,pitch,bearing")
    ap.add_argument("--playwright", default=None)
    ap.add_argument("--chromium", default=None)
    a = ap.parse_args()
    ch = a.chromium or find_chromium()
    pw = a.playwright or find_playwright()
    if not ch or not pw:
        sys.exit(f"missing chromium ({ch}) or playwright ({pw}); "
                 f"try: npx playwright install chromium, and pass --playwright")
    jump = ""
    if a.jump:
        lon, lat, z, pi, be = a.jump.split(",")
        jump = f"await p.evaluate(()=>window.__map.jumpTo({{center:[{lon},{lat}],zoom:{z},pitch:{pi},bearing:{be}}}));await p.waitForTimeout(1200);"
    acts = ""
    for sel in a.click:
        acts += f"await p.click('{sel}');await p.waitForTimeout(1200);"
    if a.set_time is not None:
        acts += (f"await p.evaluate(v=>{{const t=document.getElementById('t');t.value=v;"
                 f"t.dispatchEvent(new Event('input'));}},{a.set_time});await p.waitForTimeout(1200);")
    js = (NODE.replace("%PW%", pw).replace("%CH%", ch)
          .replace("%HTML%", os.path.abspath(a.html)).replace("%OUT%", os.path.abspath(a.out))
          .replace("%JUMP%", jump).replace("%ACTIONS%", acts))
    f = tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False); f.write(js); f.close()
    r = subprocess.run(["node", f.name], capture_output=True, text=True)
    out = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        info = json.loads(out)
        print(json.dumps(info, indent=1))
        r0 = info["rendered"]
        if r0.get("ours3d") == 0:
            print("\n*** ours3d rendered 0 features — targets are in the source but not drawing."
                  "\n*** This is almost always CLOCKWISE ring winding (MapLibre reads it as a hole)."
                  "\n*** Fix: enforce CCW on target rings, regenerate, re-verify.")
    except Exception:
        print("raw:", r.stdout[-800:], "\nstderr:", r.stderr[-800:])
    os.unlink(f.name)


if __name__ == "__main__":
    main()
