# EduCoach collectors

Small scripts that read public Greek and EU sources about education which an AI research agent cannot read on its own: registers with no search, registers with too much noise, and listings that are empty until JavaScript runs. Each script keeps what is new and relevant and posts it to the **Inbox** of a private gateway; a daily research run reads the Inbox next to its own research. No AI here: plain requests, keyword rules, and the gateway's memory of what it has already seen.

The gateway is not part of this repository. Its address and key come from the environment (`EC_API_URL`, `EC_SECRET`); nothing secret is stored here. Without them the scripts still run with `--dry-run` and print what they would send.

They run once a day on GitHub Actions (`.github/workflows/collectors.yml`, 03:20 UTC), before the research routine.

| Collector | Source | Why a script | What it keeps |
|---|---|---|---|
| `fek.py` | ΦΕΚ (the Government Gazette), τεύχη Α and Β, through the National Printing House API (`api.et.gr/apiFEK/{issue}/{year}/{n}/{html\|pdf}`) | The API has no search: you must know the sheet number. | Walks the new numbers, reads the first pages of each PDF (`pdftotext`), splits a sheet into its acts and keeps the acts about education. About 6 of 320 Β sheets pass (two and a half weeks, November 2026). A law (τεύχος Α) is also kept when the titles of its articles are about education. |
| `diavgeia.py` | Διαύγεια open data API, for Υπουργείο Παιδείας, ΔΥΠΑ, ΙΕΠ, ΕΟΠΠΕΠ, ΕΘΑΑΕ, ΙΝΕΔΙΒΙΜ | The API works, but the Ministry alone posts ~300 acts a day, nearly all routine. | Act types Α.1.1, Α.1.2, Α.2, Α.3, Γ.3.1, 2.4.7.1, then the same topic rules. About 10 of 2,460 acts pass (ten days). All circulars (Α.3) of the Ministry are kept unless routine. |
| `browser.py` | Listing pages that are empty until JavaScript runs (`browser_sources.json`) | A plain request gets an empty shell. | Opens the page in headless Chromium, posts the links of the list (or, with `"mode": "page"`, one item when the page's text changes). |

`relevance.py` holds what «about education» means (topics → category, routine words that drop an act). `test_filters.py` checks the rules on real titles: `python3 -m unittest collectors.test_filters`.

## What the browser collector does not do

It is a plain headless browser. It keeps Chromium's own user agent, hides nothing, and reads a page only when the site's `robots.txt` allows it. A site that answers 403/429 to automated clients has refused; those sources stay in `browser_sources.json` with `"enabled": false` and the reason in `tested`. Enable one only when the site offers a way in (a feed, an API, a changed robots.txt).

State on 19/11/2026, from a home connection (GitHub's runners have other IP addresses, so results there may differ):

- Read: ΔΥΠΑ Νέα-Ανακοινώσεις, Europol vacancies, Commission Blue Book traineeships (page-change watch).
- Refused a headless browser: UN Careers (403), EURES (429), EMBL jobs (429).
- Refuse plain requests with 403, not tried with a browser: Cedefop (news, vacancies), OECD education, Erasmus+ news, Council of the EU traineeships, Council of Europe traineeships, Ίδρυμα Ωνάση, ΕΚΤ, ErasmusIntern.

## Run locally

```bash
python3 -m collectors.run --dry-run --explain                 # everything, nothing sent; why each item was kept or dropped
python3 -m collectors.run --dry-run --only fek,diavgeia       # some collectors
python3 -m collectors.run --dry-run --only browser --sources dypa_news,europol_vacancies
python3 -m collectors.run --dry-run --state-file /tmp/c.json  # keep cursors between dry runs, to test «new since last run»
python3 -m collectors.run                                     # for real (needs the gateway config)
```

- Needs Python 3.11+, `pdftotext` (`brew install poppler` / `apt install poppler-utils`), and for the browser collector `pip install -r collectors/requirements.txt && python -m playwright install chromium` (or `CHROMIUM=<path to a Chromium binary>`).
- Gateway config: `EC_API_URL` and `EC_SECRET` in the environment (GitHub secrets with the same names), else `ec.config.json` at the repo root (`{"apiUrl": …, "secret": …}`, gitignored).
- `--dry-run` prints one JSON object per post on stdout (`source`, `seed`, `error`, `cursors`, `items`); logs go to stderr.
- A first ΦΕΚ run (no cursor) finds the latest numbers by bisection and reads the last 10 Α and 40 Β sheets: about 4 minutes at ~50 requests a minute (the API allows 100).

## Gateway contract

```
POST {secret, action: "collector_state"}  →  {ok, cursors: {key: value, …}}
POST {secret, action: "inbox_add", source: "<collector>", items: [...], cursors: {...}, seed: false, error: ""}
                                          →  {ok, added, duplicates}
item = {source, title, url, published_at: "YYYY-MM-DD" | "", excerpt (≤ 600 chars), category?, ref?}
```

- The gateway dedupes on `ref` (default: `url`). Refs: `fek-<issue>-<year>-<n>`, `diavgeia-<ΑΔΑ>`, the link url, `<name>-<hash>` for a page watch.
- `seed: true` records the items as seen without putting them in the Inbox (first run of a link source).
- `error` reports a failed run or failed sources; `items` may be empty.
- `category`: `school`, `admission`, `university`, `careers`, `opportunities`, `skills`, `edtech`, `policy`, `parents`, `inclusion`, `seasonal`.

Cursors: `fek_<issue>_<year>` (last number read) and `fek_<issue>_<year>_gaps` (numbers that did not exist yet, comma separated, retried); `diavgeia_<org uid>` (the day up to which POSTED acts were read; the search is by posting date, because an act issued last week can be posted today); `seeded_<name>` (a browser source has had its first run).

## Add a source

- **A listing page that needs JavaScript:** add an entry to `browser_sources.json` (`name`, `label`, `url`, `category`; see `_help` in the file for `scope`, `link_regex`, `require_date`, `mode: "page"`). Test with `--dry-run --only browser --sources <name> --explain`. Its first real run only seeds.
- **A Διαύγεια body:** add its uid to `ORGS` in `diavgeia.py` (look it up in `https://diavgeia.gov.gr/opendata/organizations.json`). ΙΚΥ has no record of its own there; its scholarship acts appear under the Ministry.
- **A new kind of source:** a module with `collect(run, explain=False)` that calls `run.post(source, items, cursors)`, added to `COLLECTORS` in `run.py`. Use `common.Http` (polite gap between requests, a plain non-browser user agent) and `common.item`.
- **A topic the filters miss:** add a stem to `TOPICS` in `relevance.py` (text is matched lowercase, without accents, `ς` → `σ`), and a real title to `test_filters.py`.
