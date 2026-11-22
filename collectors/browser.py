"""Listing pages that are empty until JavaScript runs: read with a real (headless) browser.

Each source in browser_sources.json is a listing page (news, vacancies, calls). The collector opens it in headless
Chromium, takes the links of the main content (text + url, and a date when one stands next to the link) and posts
them all. The gateway keeps only the links it has not seen, so «new since yesterday» needs no state here. The first
run of a source is sent with seed:true (cursor `seeded_<name>`): its links are recorded as seen without entering the
Inbox, otherwise the whole page would arrive as news.

This is a plain browser, not a disguise: it keeps Chromium's own headless user agent, hides nothing, and reads a
site only when its robots.txt allows the page. A site that answers 403 to automated clients has said no; such
sources stay in the list with "enabled": false and the reason, until they offer a feed or an API.

Needs: pip install playwright && playwright install chromium (or CHROMIUM=<path to a Chromium binary>).
"""
import datetime, hashlib, json, os, re, urllib.error, urllib.parse, urllib.request, urllib.robotparser

from .common import HERE, UA, clip, item, log, norm, squeeze

SOURCES = os.path.join(HERE, 'browser_sources.json')
BLOCK_TITLES = ['just a moment', 'access denied', 'attention required', 'request rejected', 'forbidden',
                'are you a robot', 'security check', 'pardon our interruption', 'error 403', '403 forbidden']
DEFAULT_SCOPE = 'main, [role=main], #main, #content, .main-content, article'
MONTHS = {'ιανουαριου': 1, 'φεβρουαριου': 2, 'μαρτιου': 3, 'απριλιου': 4, 'μαιου': 5, 'ιουνιου': 6, 'ιουλιου': 7,
          'αυγουστου': 8, 'σεπτεμβριου': 9, 'οκτωβριου': 10, 'νοεμβριου': 11, 'δεκεμβριου': 12,
          'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6, 'july': 7, 'august': 8,
          'september': 9, 'october': 10, 'november': 11, 'december': 12,
          'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'sept': 9, 'oct': 10,
          'nov': 11, 'dec': 12, 'ιαν': 1, 'φεβ': 2, 'μαρ': 3, 'απρ': 4, 'μαι': 5, 'ιουν': 6, 'ιουλ': 7, 'αυγ': 8,
          'σεπ': 9, 'οκτ': 10, 'νοε': 11, 'δεκ': 12}

# Runs in the page. Links of the scope, outside navigation, on the same site; with the text of the block around
# each link (the card or list row), where a date usually stands.
EXTRACT = r"""
([scope, minText]) => {
  const roots = Array.from(document.querySelectorAll(scope));
  const base = roots.length ? roots : [document.body];
  const site = location.hostname.split('.').slice(-2).join('.');
  const seen = new Set(), out = [];
  for (const root of base) {
    for (const a of root.querySelectorAll('a[href]')) {
      if (a.closest('nav, header, footer, [role=navigation], [role=banner], [role=contentinfo], .breadcrumb, .pagination, .pager, .menu, .cookie, [class*=cookie]')) continue;
      let url; try { url = new URL(a.getAttribute('href'), location.href); } catch (e) { continue; }
      if (!/^https?:$/.test(url.protocol)) continue;
      if (!url.hostname.endsWith(site)) continue;
      url.hash = '';
      const href = url.href;
      if (href === location.href.split('#')[0] || seen.has(href)) continue;
      let text = (a.innerText || '').replace(/\s+/g, ' ').trim();
      if (text.length < minText) text = (a.getAttribute('title') || a.getAttribute('aria-label') || text).trim();
      // The card or row of this link: the largest block around it that is still about this link only.
      let box = a;
      while (box.parentElement && box.parentElement !== document.body) {
        const p = box.parentElement;
        if ((p.innerText || '').length > 600 || p.querySelectorAll('a[href]').length > 4) break;
        box = p;
      }
      let around = box ? (box.innerText || '').replace(/\s+/g, ' ').trim() : '';
      if (text.length < minText) {
        // A card whose link wraps only an image or «read more»: take the card's heading.
        const h = box && box.querySelector('h1, h2, h3, h4, h5, [class*=title]');
        const ht = h ? (h.innerText || '').replace(/\s+/g, ' ').trim() : '';
        if (ht.length >= minText) text = ht;
      }
      if (text.length < minText) continue;
      const t = box && box.querySelector('time[datetime]');
      seen.add(href);
      out.push({ text: text.slice(0, 300), url: href, around: around.slice(0, 500), time: t ? t.getAttribute('datetime') : '' });
    }
  }
  return { title: document.title || '', links: out, textLength: (document.body.innerText || '').length };
}
"""


def find_date(text, iso=''):
    """A date in the text next to a link → 'YYYY-MM-DD', or ''. Only dates within ±3 years count."""
    year = datetime.date.today().year

    def ok(y, m, d):
        try:
            datetime.date(y, m, d)
        except ValueError:
            return ''
        return '%04d-%02d-%02d' % (y, m, d) if abs(y - year) <= 3 else ''
    m = re.match(r'(\d{4})-(\d\d)-(\d\d)', iso or '')
    if m:
        return ok(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    t = norm(text)
    m = re.search(r'\b(\d{1,2})[./-](\d{1,2})[./-](20\d\d)\b', t)
    if m:
        return ok(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r'\b(20\d\d)-(\d\d)-(\d\d)\b', t)
    if m:
        return ok(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r'\b(\d{1,2})(?:st|nd|rd|th|ησ|η)?\s+([a-zα-ω]{3,12})\.?,?\s+(20\d\d)\b', t)
    if m and m.group(2) in MONTHS:
        return ok(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))
    m = re.search(r'\b([a-zα-ω]{3,12})\.?\s+(\d{1,2}),?\s+(20\d\d)\b', t)
    if m and m.group(1) in MONTHS:
        return ok(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
    return ''


def load_sources():
    with open(SOURCES) as f:
        return json.load(f)['sources']


def chromium_path():
    p = os.environ.get('CHROMIUM')
    return p if p and os.path.exists(p) else None


def robots_allow(url):
    """(allowed, reason). The site's robots.txt decides; a robots.txt that itself answers 401/403 means no."""
    u = urllib.parse.urlsplit(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        req = urllib.request.Request(f'{u.scheme}://{u.netloc}/robots.txt', headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            rp.parse(r.read().decode('utf-8', 'ignore').splitlines())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, f'the site refuses automated clients (robots.txt answers HTTP {e.code})'
        return True, ''                       # no robots.txt (404): nothing is disallowed
    except Exception:
        return True, ''
    if not rp.can_fetch(UA, url):
        return False, 'robots.txt disallows this page'
    return True, ''


def read_source(browser, src):
    """Returns (items, problem). problem is '' when the page was read."""
    allowed, why = robots_allow(src['url'])
    if not allowed:
        return [], why
    locale = src.get('locale', 'el-GR')
    ctx = browser.new_context(locale=locale, timezone_id='Europe/Athens', viewport={'width': 1366, 'height': 900})
    page = ctx.new_page()
    try:
        resp = page.goto(src['url'], wait_until='domcontentloaded', timeout=45000)
        status = resp.status if resp else 0
        try:
            if src.get('wait_for'):
                page.wait_for_selector(src['wait_for'], timeout=20000)
            else:
                page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass                                   # slow trackers keep the network busy: read what is there
        page.wait_for_timeout(int(src.get('settle_ms', 1500)))
        for sel in src.get('click', []):           # e.g. a cookie banner that hides the list
            try:
                page.click(sel, timeout=3000)
                page.wait_for_timeout(800)
            except Exception:
                pass
        data = page.evaluate(EXTRACT, [src.get('scope') or DEFAULT_SCOPE, int(src.get('min_text', 25))])
        title = norm(data.get('title'))
        if status in (401, 403, 429) or any(b in title for b in BLOCK_TITLES):
            return [], f'the site refuses automated clients (HTTP {status}, title «{clip(data.get("title"), 60)}»)'
        if status >= 400:
            return [], f'HTTP {status}'
        if src.get('mode') == 'page':
            # A page with no list (a programme page that changes when applications open): one item per new text.
            text = squeeze(page.evaluate("(scope) => { for (const q of scope.split(',')) { const r = document.querySelector(q.trim()); if (r && (r.innerText || '').length > 200) return r.innerText; } return document.body.innerText || ''; }",
                                         src.get('scope') or 'main, [role=main], body'))
            if len(text) < int(src.get('min_chars', 400)):
                return [], f'the page has almost no text ({len(text)} characters)'
            stable = re.sub(r'\d', '#', norm(text))          # counters and clocks must not look like a change
            ref = f'{src["name"]}-{hashlib.sha1(stable.encode()).hexdigest()[:12]}'
            return [item(src['label'], f'{src["label"]}: η σελίδα άλλαξε', src['url'], '', text, src.get('category', ''), ref=ref)], ''
        links = data['links']
        if src.get('require_date'):
            links = [l for l in links if find_date(l['around'], l.get('time'))]
        inc, exc = src.get('link_regex'), src.get('exclude_regex')
        if inc:
            links = [l for l in links if re.search(inc, l['url'])]
        if exc:
            links = [l for l in links if not re.search(exc, l['url'])]
        links = links[:int(src.get('max_items', 40))]
        if len(links) < int(src.get('min_links', 2)):
            return [], f'no list found ({len(data["links"])} links in scope, {data["textLength"]} characters of text)'
        items = []
        for l in links:
            around = l['around'] if l['around'] != l['text'] else ''
            items.append(item(src['label'], l['text'], l['url'], find_date(around or l['text'], l.get('time')),
                              around, src.get('category', '')))
        return items, ''
    finally:
        ctx.close()


def collect(run, explain=False, only=None):
    from playwright.sync_api import sync_playwright      # imported here: the other collectors do not need it
    sources = [s for s in load_sources() if s.get('enabled', True) and (not only or s['name'] in only)]
    today = datetime.date.today().isoformat()
    problems, read = [], 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=chromium_path())
        try:
            for src in sources:
                try:
                    items, problem = read_source(browser, src)
                except Exception as e:
                    items, problem = [], f'{type(e).__name__}: {str(e).splitlines()[0][:160]}'
                if problem:
                    problems.append(f'{src["name"]}: {problem}')
                    log(f'  FAIL {src["name"]}: {problem}')
                    continue
                read += 1
                key = f'seeded_{src["name"]}'
                first = not run.cursor(key)
                if explain:
                    log(f'  ok   {src["name"]}: {len(items)} links' + (' (first run: seed)' if first else ''))
                    for it in items[:3]:
                        log(f'         {it["published_at"] or "no date  "} {clip(it["title"], 110)}')
                run.post('browser', items, {key: today} if first else {}, seed=first)
        finally:
            browser.close()
    run.note(f'browser: {read}/{len(sources)} sources read' + (f'; failed: {"; ".join(problems)}' if problems else ''))
    if problems:
        run.post('browser', [], {}, error='; '.join(problems)[:500])
    if sources and not read:
        raise RuntimeError('no source could be read: ' + problems[0])
    return read
