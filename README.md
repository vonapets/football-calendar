# Season Wallchart

A fixture calendar for the 2026/27 season covering the Premier League, La Liga,
both English domestic cups, both Spanish cups, and all three UEFA competitions.
It refreshes itself four times a day, keeps a record of every kick-off that moves,
and picks the big-club fixtures out of the noise.

**Live page:** https://vonapets.github.io/football-calendar/

Send that link to anyone. It rebuilds itself four times a day on GitHub's servers,
so whoever opens it — today, next week, on a phone in another country — gets the
current fixtures, scores and reschedules. Nobody has to publish anything.

---

## How it runs

`.github/workflows/sync.yml` runs on GitHub Actions at **02:30, 08:30, 14:30 and
22:45 UTC**. Each run pulls the nine competitions from ESPN, diffs them against the
previous snapshot, records anything that moved, rebuilds the page, and deploys it
to GitHub Pages. No API key, no password, no laptop.

The **22:45** run is the one that earns its place: European kick-offs finish around
21:30 UTC, so it lands the night's results before the day turns over.

Nothing runs on the Mac any more, and that is the point. The old `launchd` job was
switched off on 25 Aug 2026 — it could only update the page when the machine
happened to be awake, and over 20–25 Aug two of its four alarms never fired once,
because a slept-through `StartCalendarInterval` is not reliably run on wake. The
plist is parked at `~/Library/LaunchAgents/com.wallchart.daily.plist.disabled` if it
is ever wanted back; `run.sh` still works by hand for a local build.

To watch a run, or force one immediately:

    gh run list   --repo vonapets/football-calendar
    gh workflow run sync.yml --repo vonapets/football-calendar

There is also a **Run workflow** button on the Actions tab.

### Two things worth knowing

- **GitHub Pages caches for ten minutes.** A refresh straight after a deploy can
  still serve the previous copy for a few minutes. It sorts itself out.
- **Scheduled workflows are disabled after 60 days of repository inactivity.** The
  sync commits a snapshot on most runs, which counts as activity, so this should
  never trigger during a season. GitHub emails the repo owner before it does.

---

## Where the data comes from

ESPN's public soccer feed (`site.api.espn.com`) — the same JSON endpoint espn.com
loads its own scoreboards from. No key, no signup, one request per competition.

**Be aware:** this is an undocumented endpoint. ESPN never published it as an API
and does not promise to keep it stable, so at some point it may change shape and
need an hour of repair. Because of that, `sync.py` is deliberately defensive — if
a fetch fails or comes back empty, it keeps yesterday's fixtures rather than
wiping the calendar. It is also why the shared page is best kept private rather
than circulated widely.

If it ever breaks for good, the paid alternative is API-Football Pro ($19/month),
which serves all nine competitions from one documented API.

## What the pieces do

| File | What it is |
|---|---|
| `config.json` | Competitions, the season window, the confirmed break dates, and the top-club list. Edit this, not the code. |
| `sync.py` | Fetches fixtures, works out what changed since yesterday, writes `data/`. |
| `build.py` | Turns the saved data into `calendar.html`. |
| `.github/workflows/sync.yml` | The schedule. Runs the sync on GitHub's servers and publishes the page. |
| `run.sh` | Sync and build locally, by hand. No longer scheduled. |
| `template.html` | The page design. Only touch this to change how the calendar looks. |
| `test_diff.py` | Proves the reschedule detection still works. Run after changing `sync.py`. |
| `data/fixtures.json` | Today's snapshot. |
| `data/changes.json` | Every kick-off that has moved, with old and new dates. Never overwritten. |
| `docs/source-research.json` | Where the break dates and competition IDs were verified. |

## How reschedules are caught

There is no "what changed" feed, so the calendar works it out by comparing each
day's fixtures against the previous day. Two different things can happen:

- **A kick-off time is edited** (usually a TV pick). The match keeps its id and
  gets a new time — detected directly.
- **A match is postponed.** ESPN does not move it. It freezes the original entry
  as *postponed* and creates a brand-new entry at the new date. `sync.py` links
  the two back together by competition and team pairing, so the calendar still
  shows "moved from X to Y" rather than two unrelated matches.

Both land in the **Schedule changes** panel and highlight the match in the grid.

## Breaks and holidays

`config.json` holds the confirmed dates: the four FIFA international windows, the
La Liga winter break, and the Supercopa window. Days inside a break are shaded and
listed in the **Breaks & shutdowns** panel.

`sync.py` also finds breaks on its own — any stretch of 11+ days with no Premier
League or La Liga football gets flagged, so an unlisted shutdown still shows up.

## Big matches

Two things mark out the fixtures worth caring about, both driven by the
`top_teams` block in `config.json` — 34 clubs: eight from England, six from
Spain, twenty from the rest of Europe.

- **In the calendar itself.** A listed club's name is set in bold. When *both*
  clubs are listed, the whole fixture is ringed and lifted off the card. No new
  colour is used for this — the nine competition colours plus orange-for-moved
  and ochre-for-break already fill the palette, so importance is carried by
  weight and contrast instead.
- **In the "Matches that matter" panel**, directly above the calendar. Every
  two-big-club fixture inside a one-month window, in date order, with the venue
  and — where there is one — the name of the rivalry (Manchester derby, El
  Clasico, Der Klassiker). The window is either the **next 30 days** or the
  **month you are looking at**; the buttons are in the panel header.
  The three-bar meter is how big the match is: two big clubs, one elite club
  plus a big one, or two elite clubs / a derby / a final.

**Top clubs only**, the checkbox at the head of the filter rail, is **on by
default** — including for anyone opening the shared link. It hides every fixture
without a listed club in it (451 of 1250 survive). Clicking it shows everything;
the choice is remembered per browser.

### Changing the list

Edit `top_teams.clubs` in `config.json` and run `python3 build.py` — no re-sync,
it takes a second. `tier` is 1 for elite and 2 for big; `aka` holds alternative
spellings, because the feed's idea of a club's name is not always yours.

`build.py` prints any configured club it could not find in the fixture list. In
August that is normal and expected — Bayern, PSG, Inter and the rest only enter
the feed once the Champions League league phase is drawn. After the draw, a name
still on that list means the spelling needs an `aka` entry.

## Things worth knowing

- **Five competitions are empty right now** — the FA Cup, Copa del Rey and the
  three UEFA league phases have not been drawn yet. No source anywhere has those
  fixtures. They fill in automatically as the draws happen (UEFA late August,
  FA Cup and Copa del Rey late October).
- **European qualifying is hidden by default.** 428 early-round qualifiers would
  otherwise bury the fixtures you care about. The toggle is above the calendar.
- **`TBD` instead of a time** means the kick-off is not yet confirmed — common for
  La Liga, which sets times a few weeks ahead. The date is right, the time is not
  final. Showing that honestly beats displaying a placeholder as if it were real.
- **Times follow the device.** Kick-offs are stored in UTC and converted in the
  browser, so the page shows correct local time wherever it is opened.
