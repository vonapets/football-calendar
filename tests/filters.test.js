// UI filter tests for the wallchart -- run after changing template.html or build.py:
//   python3 build.py && npm i --no-save playwright-core && node tests/filters.test.js
// Drives the installed Google Chrome (macOS path below) at five fixed clock times.
//
// UI filter tests for the wallcharts. Every expectation is computed here from the raw
// payload embedded in the page (an independent oracle), then compared with what the
// page actually renders after real clicks. Run: node filters.test.js [url-or-file ...]
const { chromium } = require("playwright-core");
const fs = require("fs");

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
// default: this repo's own build; or pass file:// or https:// URLs (e.g. the live page)
const PAGES = process.argv.slice(2).length ? process.argv.slice(2)
  : ["file://" + require("path").resolve(__dirname, "..", "calendar.html")];

// clock instants (UTC) + viewer time zone -> the cut the page must apply (UTC)
const CLOCKS = [
  { name: "Tue 29 Sep 12:00 Lisbon", now: "2026-09-29T11:00:00Z", tz: "Europe/Lisbon",     cut: "2026-09-20T23:00:00Z" },
  { name: "Sun 4 Oct 20:00 Lisbon",  now: "2026-10-04T19:00:00Z", tz: "Europe/Lisbon",     cut: "2026-09-20T23:00:00Z" },
  { name: "Mon 5 Oct 00:30 Lisbon",  now: "2026-10-04T23:30:00Z", tz: "Europe/Lisbon",     cut: "2026-09-27T23:00:00Z" },
  { name: "Mon 5 Oct 09:00 Lisbon",  now: "2026-10-05T08:00:00Z", tz: "Europe/Lisbon",     cut: "2026-09-27T23:00:00Z" },
  { name: "Tue 29 Sep 12:00 SaoPaulo", now: "2026-09-29T15:00:00Z", tz: "America/Sao_Paulo", cut: "2026-09-21T03:00:00Z" },
];

const fold = s => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

async function payloadOf(url) {
  const html = url.startsWith("file://") ? fs.readFileSync(url.slice(7), "utf8")
                                         : await (await fetch(url, { cache: "no-store" })).text();
  const m = html.match(/const DATA = (.*?);\n/s);
  return JSON.parse(m[1].replace(/<\\\//g, "</"));
}

let failures = 0, passes = 0;
function check(label, ok, detail = "") {
  if (ok) { passes++; return; }
  failures++;
  console.log(`   FAIL ${label}${detail ? " -- " + detail : ""}`);
}

async function suite(browser, url, clock) {
  const raw = await payloadOf(url);
  const isBrazil = raw.competitions.some(c => c.key === "bra1");
  const tier = Object.fromEntries(raw.competitions.map(c => [c.key, c.tier]));
  const byId = new Map(raw.fixtures.map(f => [String(f.id), f]));
  const cutMs = Date.parse(clock.cut);
  const dayKey = ms => new Intl.DateTimeFormat("en-CA", { timeZone: clock.tz }).format(new Date(ms));
  const cutKey = dayKey(cutMs);

  const isBig = isBrazil
    ? f => (f.hot === 2 && f.heat >= 6) ||
           (tier[f.comp] === "euro" && f.hot >= 1 && (f.tag === "Semi-final" || f.tag === "Final")) ||
           f.tag === "Final"
    : f => f.hot === 2;

  // the page's default state: every comp on, qualifying hidden, top-clubs ON (football) / OFF (Brazil)
  const st = { comps: new Set(raw.competitions.map(c => c.key)), qual: false, top: !isBrazil, q: "" };
  const shown = raw.fixtures.filter(f => f.ts * 1000 >= cutMs);
  const passes_ = (f, s = st, ignoreComps = false) =>
    (ignoreComps || s.comps.has(f.comp)) && (s.qual || !f.qual) && (!s.top || f.hot > 0) &&
    (!s.q || fold(f.home).includes(fold(s.q)) || fold(f.away).includes(fold(s.q)));
  const expect = (s = st) => shown.filter(f => passes_(f, s));

  const ctx = await browser.newContext({ timezoneId: clock.tz, viewport: { width: 1400, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  await page.clock.setFixedTime(new Date(clock.now));
  await page.goto(url);
  await page.waitForSelector("#viewport .day, #viewport .lrow, #viewport .wcol");

  const ids = sel => page.$$eval(sel, els => els.map(e => e.dataset.fx));
  const rows = () => ids("#viewport [data-fx]");
  const toList = async () => { await page.click('[data-view="list"]'); await page.click('[data-scope="season"]'); };
  const sameSet = (a, b) => a.length === b.length && a.every(x => b.includes(x));

  // --- 1. the cut ---------------------------------------------------------
  const pageData = await page.evaluate(() => ({
    n: DATA.fixtures.length, minTs: Math.min(...DATA.fixtures.map(f => f.ts)),
    breaks: DATA.breaks.map(b => b.end), changes: DATA.changes.map(c => c.to_utc),
    showFrom: SHOW_FROM.getTime(),
  }));
  check("cut instant", pageData.showFrom === cutMs, `page ${new Date(pageData.showFrom).toISOString()} want ${clock.cut}`);
  check("fixtures trimmed to the cut", pageData.n === shown.length, `page ${pageData.n} want ${shown.length}`);
  check("nothing older than the cut", pageData.minTs * 1000 >= cutMs);
  const lastWeek = shown.filter(f => f.ts * 1000 < cutMs + 7 * 864e5);
  check("last week's matches still shown", lastWeek.length > 0 || !raw.fixtures.some(f => f.ts * 1000 >= cutMs && f.ts * 1000 < cutMs + 7 * 864e5));
  const wantBreaks = raw.breaks.filter(b => b.end >= cutKey).length;
  check("ended breaks dropped", pageData.breaks.length === wantBreaks && pageData.breaks.every(e => e >= cutKey),
        `page ${pageData.breaks.length} want ${wantBreaks}`);
  check("old reschedules dropped", pageData.changes.every(t => !t || Date.parse(t) >= cutMs));

  // month grid: nothing before the cut, and pre-cut days styled out
  const gridIds = await rows();
  check("month grid has no pre-cut match", gridIds.every(id => byId.get(id).ts * 1000 >= cutMs));
  const outOk = await page.$$eval("#viewport .day", (els, cut) => els.every(el => {
    const k = el.querySelector("[data-jump]").dataset.jump;
    return k >= cut ? true : el.classList.contains("out");
  }), cutKey);
  check("pre-cut days styled as out-of-range", outOk);

  // --- 2. paging floor ----------------------------------------------------
  await page.click("#today");
  for (let i = 0; i < 24; i++) { if (await page.$eval("#prev", b => b.disabled)) break; await page.click("#prev"); }
  const floorLabel = await page.textContent("#period");
  const cutMonth = new Intl.DateTimeFormat("en-GB", { timeZone: clock.tz, month: "long", year: "numeric" }).format(new Date(cutMs));
  check("month paging stops at the cut's month", floorLabel.trim() === cutMonth, `stopped at "${floorLabel}" want "${cutMonth}"`);
  await page.keyboard.press("ArrowLeft");
  check("ArrowLeft cannot pass the floor", (await page.textContent("#period")).trim() === cutMonth);
  await page.click('[data-view="week"]');
  for (let i = 0; i < 30; i++) { if (await page.$eval("#prev", b => b.disabled)) break; await page.click("#prev"); }
  const weekIds = await rows();
  const firstWeekDays = await page.$$eval("#viewport .wcol .wn", els => els.map(e => e.textContent.trim()));
  check("week paging stops at the cut's week",
        firstWeekDays[0].startsWith(String(new Date(Date.parse(clock.cut) + 12 * 36e5).getUTCDate())),
        `first day shown ${firstWeekDays[0]}`);
  check("week view shows only that week", weekIds.every(id => { const t = byId.get(id).ts * 1000; return t >= cutMs && t < cutMs + 7 * 864e5; }));
  const wantWeek = expect().filter(f => f.ts * 1000 < cutMs + 7 * 864e5).map(f => String(f.id));
  check("week view shows every match of that week", sameSet(weekIds, wantWeek), `page ${weekIds.length} want ${wantWeek.length}`);

  // --- 3. list view = the full filtered set --------------------------------
  await toList();
  let got = await rows();
  let want = expect().map(f => String(f.id));
  check("default filters (list, everything shown)", sameSet(got, want), `page ${got.length} want ${want.length}`);

  // chip counts = per-comp tally under the other filters
  const chipN = await page.$$eval("[data-comp]", els => Object.fromEntries(els.map(e => [e.dataset.comp, +e.querySelector(".n").textContent])));
  const wantN = {};
  for (const f of shown) if (passes_(f, st, true)) wantN[f.comp] = (wantN[f.comp] || 0) + 1;
  check("chip counts", raw.competitions.every(c => chipN[c.key] === (wantN[c.key] || 0)),
        JSON.stringify(raw.competitions.filter(c => chipN[c.key] !== (wantN[c.key] || 0)).map(c => [c.key, chipN[c.key], wantN[c.key] || 0])));

  // --- 4. each competition chip off and back on ----------------------------
  const chipBad = [];
  for (const c of raw.competitions) {
    await page.click(`[data-comp="${c.key}"]`);
    st.comps.delete(c.key);
    got = await rows(); want = expect().map(f => String(f.id));
    if (!sameSet(got, want) || got.some(id => byId.get(id).comp === c.key)) chipBad.push(c.key + " off");
    await page.click(`[data-comp="${c.key}"]`);
    st.comps.add(c.key);
    got = await rows(); want = expect().map(f => String(f.id));
    if (!sameSet(got, want)) chipBad.push(c.key + " on");
  }
  check(`all ${raw.competitions.length} chips toggle correctly`, !chipBad.length, chipBad.join(", "));

  await page.click('[data-all="0"]');
  got = await rows();
  const first = raw.competitions[0].key;
  check('"None" leaves only the first competition', got.every(id => byId.get(id).comp === first) &&
        got.length === expect({ ...st, comps: new Set([first]) }).length);
  await page.click('[data-all="1"]');
  check('"All" restores everything', sameSet(await rows(), expect().map(f => String(f.id))));

  // --- 5. big-clubs toggle -------------------------------------------------
  await page.click("[data-top]");
  st.top = !st.top;
  got = await rows(); want = expect().map(f => String(f.id));
  check(`big-clubs toggle -> ${st.top ? "on" : "off"}`, sameSet(got, want), `page ${got.length} want ${want.length}`);
  if (st.top) check("big-clubs on shows only listed clubs", got.every(id => byId.get(id).hot > 0));
  await page.click("[data-top]"); st.top = !st.top;
  check("big-clubs toggle back", sameSet(await rows(), expect().map(f => String(f.id))));

  // --- 6. qualifying toggle (football only) --------------------------------
  if (await page.$("[data-qual]")) {
    await page.click("[data-qual]"); st.qual = true;
    got = await rows(); want = expect().map(f => String(f.id));
    check("qualifying shown", sameSet(got, want), `page ${got.length} want ${want.length}`);
    await page.click("[data-qual]"); st.qual = false;
    got = await rows();
    check("qualifying hidden again", got.every(id => !byId.get(id).qual));
  }

  // --- 7. team search, accent-insensitive ----------------------------------
  const terms = isBrazil ? ["gremio", "Grêmio", "sao paulo", "ATLETICO", "vasco"] : ["atletico", "bayern", "man", "real madrid"];
  st.top = false;
  if (!isBrazil) await page.click("[data-top]");       // football: turn top-clubs off so search sees everything
  for (const t of terms) {
    await page.fill("#q", t); st.q = t;
    got = await rows(); want = expect().map(f => String(f.id));
    check(`search "${t}"`, sameSet(got, want) && got.length > 0, `page ${got.length} want ${want.length}`);
  }
  await page.fill("#q", ""); st.q = "";
  if (!isBrazil) { await page.click("[data-top]"); st.top = true; }

  // --- 8. big-match panel --------------------------------------------------
  for (const scope of ["30", "view"]) {
    await page.click('[data-view="month"]');
    await page.click("#today");
    await page.click(`[data-big="${scope}"]`);
    if (await page.$("[data-bigmore]")) {
      const open = await page.$eval("[data-bigmore]", b => b.getAttribute("aria-expanded") === "true");
      if (!open) await page.click("[data-bigmore]");
    }
    const cards = await ids("#bigBody [data-fx]");
    const todayKey = dayKey(Date.parse(clock.now));
    let lo, hi;
    if (scope === "30") { lo = todayKey; hi = dayKey(Date.parse(todayKey + "T12:00:00Z") + 29 * 864e5); }
    else { lo = todayKey.slice(0, 7) + "-01"; hi = todayKey.slice(0, 7) + "-31"; }
    const wantBig = expect().filter(f => isBig(f)).filter(f => { const k = dayKey(f.ts * 1000); return k >= lo && k <= hi; }).map(f => String(f.id));
    check(`big panel (${scope === "30" ? "next 30 days" : "month in view"})`, sameSet(cards, wantBig), `page ${cards.length} want ${wantBig.length}`);
  }

  // --- 9. ribbon click lands inside the ribbon's own span -------------------
  const box = await page.$eval("#ribbon", el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  await page.click('[data-view="month"]');
  await page.mouse.click(box.x + 2, box.y + box.h / 2);
  // the left edge is the cut; with no match until later (an international break) it
  // clamps forward to the first date that has one
  const firstMs = Math.min(...shown.map(f => f.ts * 1000));
  const edgeMonth = new Intl.DateTimeFormat("en-GB", { timeZone: clock.tz, month: "long", year: "numeric" }).format(new Date(Math.max(cutMs, firstMs)));
  check("ribbon left edge -> first shown date's month", (await page.textContent("#period")).trim() === edgeMonth,
        `got "${(await page.textContent("#period")).trim()}"`);

  check("no page errors", !errors.length, errors.join(" | "));
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  for (const url of PAGES) {
    for (const clock of CLOCKS) {
      const before = failures;
      process.stdout.write(`${url.split("/").slice(-2).join("/")}  @ ${clock.name}\n`);
      try { await suite(browser, url, clock); }
      catch (e) { failures++; console.log("   CRASH", e.message.split("\n")[0]); }
      if (failures === before) console.log("   all checks pass");
    }
  }
  await browser.close();
  console.log(`\n${passes} checks passed, ${failures} failed`);
  process.exit(failures ? 1 : 0);
})();
