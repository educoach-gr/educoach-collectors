"""Shared pieces of the collectors: gateway calls, HTTP, Greek keyword matching, the run context.

A collector reads a source Claude cannot read on its own, keeps what is new and relevant, and posts it to the
gateway's Inbox (action `inbox_add`). Cursors (what has been processed so far) live in the gateway too
(action `collector_state`), so a collector has no local state and can run anywhere.
"""
import json, os, re, sys, time, unicodedata, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# A plain, non-browser name: these are open registers and public listings, read at a polite rate.
UA = 'education-collectors/1.0'

# The categories of an idea card (the gateway accepts only these keys in `category`).
CATEGORIES = ['school', 'admission', 'university', 'careers', 'opportunities', 'skills', 'edtech', 'policy',
              'parents', 'inclusion', 'seasonal']
EXCERPT_MAX = 600


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- gateway

def load_cfg():
    """EC_API_URL + EC_SECRET from the environment, else ec.config.json at the repo root (gitignored)."""
    url, secret = os.environ.get('EC_API_URL'), os.environ.get('EC_SECRET')
    if url and secret:
        return {'apiUrl': url, 'secret': secret}
    path = os.environ.get('EC_CONFIG', os.path.join(ROOT, 'ec.config.json'))
    try:
        with open(path) as f:
            c = json.load(f)
        return {'apiUrl': c['apiUrl'], 'secret': c['secret']}
    except (OSError, KeyError, ValueError):
        raise SystemExit('No gateway config: set EC_API_URL and EC_SECRET, or create ec.config.json')


def gateway(action, **kw):
    """POST to the gateway. Retries when Google answers 404 or an HTML page instead of JSON (as ec.py does).
    Both actions used here are safe to repeat: `collector_state` reads, `inbox_add` dedupes on ref/url."""
    c = load_cfg()
    body = json.dumps(dict(secret=c['secret'], action=action, **kw)).encode()
    tries = 4
    for n in range(tries):
        try:
            req = urllib.request.Request(c['apiUrl'], data=body, headers={'Content-Type': 'application/json'})
            r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
            break
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            if n == tries - 1:
                raise RuntimeError(f'{action}: the gateway did not answer ({e})')
            log(f'  gateway: {e}, again in {5 * (n + 1)}s')
            time.sleep(5 * (n + 1))
    if not r.get('ok'):
        raise RuntimeError(f'{action}: ' + str(r.get('error'))[:500])
    return r


# ---------------------------------------------------------------- http

def retry_after(value, default=20, most=60):
    """Seconds to wait after a 429: the server's Retry-After when it is a number of seconds, never more than `most`."""
    try:
        return max(1, min(most, int(str(value).strip())))
    except (TypeError, ValueError):
        return default


class Http:
    """GET with a minimum gap between requests to the same collector's source (polite rate)."""

    def __init__(self, gap=1.0):
        self.gap, self.last, self.count = gap, 0.0, 0

    def get(self, url, timeout=60, headers=None, max_bytes=None, tries=3):
        """Returns (status, bytes). 4xx is returned, not raised; network errors and 5xx are retried."""
        for n in range(tries):
            wait = self.gap - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.count += 1
            try:
                req = urllib.request.Request(url, headers=dict({'User-Agent': UA}, **(headers or {})))
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = r.read(max_bytes + 1) if max_bytes else r.read()
                    if max_bytes and len(data) > max_bytes:
                        return 413, b''
                    return r.status, data
            except urllib.error.HTTPError as e:
                if e.code < 500 and e.code != 429:
                    return e.code, b''
                err = e
                if e.code == 429 and n < tries - 1:    # asked to slow down: wait what the server says, then the usual pause
                    time.sleep(retry_after(e.headers.get('Retry-After') if e.headers else None))
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                err = e
            if n == tries - 1:
                raise RuntimeError(f'GET {url}: {err}')
            time.sleep(5 * (n + 1))

    def json(self, url, **kw):
        status, data = self.get(url, **kw)
        if status != 200:
            raise RuntimeError(f'GET {url}: HTTP {status}')
        return json.loads(data.decode('utf-8'))


# ---------------------------------------------------------------- text

def norm(s):
    """Lowercase Greek/Latin without accents, final sigma as σ, single spaces: «Εισαγωγής» → «εισαγωγησ»."""
    s = unicodedata.normalize('NFD', str(s or ''))
    s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn').lower().replace('ς', 'σ')
    return re.sub(r'\s+', ' ', s).strip()


def hits(text_norm, stems):
    """Which of the stems appear in the (already normalised) text. A stem may be a tuple: all its parts must appear."""
    out = []
    for st in stems:
        parts = st if isinstance(st, (tuple, list)) else (st,)
        if all(p in text_norm for p in parts):
            out.append(' + '.join(parts))
    return out


def squeeze(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def clip(s, n=EXCERPT_MAX):
    s = squeeze(s)
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'


def item(source, title, url, published_at='', excerpt='', category='', ref=''):
    """One Inbox row, in the shape the gateway expects."""
    it = {'source': source, 'title': clip(title, 300), 'url': url, 'published_at': published_at or '',
          'excerpt': clip(excerpt)}
    if category:
        if category not in CATEGORIES:
            raise ValueError('unknown category: ' + category)
        it['category'] = category
    if ref:
        it['ref'] = ref
    return it


# ---------------------------------------------------------------- run context

class Run:
    """What a collector gets: the cursors, and `post` to deliver items and new cursors.

    --dry-run: nothing is sent; items are printed as JSON. With --state-file <path> the cursors are read from and
    written to that file, so two dry runs in a row behave like two real days.
    """

    def __init__(self, dry=False, state_file=None, limit=None):
        self.dry, self.state_file, self.limit = dry, state_file, limit
        self.added = self.duplicates = self.sent = 0
        self.notes = []
        if dry:
            self.cursors = {}
            if state_file and os.path.exists(state_file):
                with open(state_file) as f:
                    self.cursors = json.load(f)
        else:
            self.cursors = gateway('collector_state').get('cursors') or {}

    def cursor(self, key, default=None):
        v = self.cursors.get(key)
        return default if v in (None, '') else v

    def note(self, text):
        self.notes.append(text)
        log('  ' + text)

    def post(self, source, items, cursors=None, seed=False, error=''):
        cursors = {k: v for k, v in (cursors or {}).items()}
        self.sent += len(items)
        if self.dry:
            print(json.dumps({'source': source, 'seed': seed, 'error': error, 'cursors': cursors, 'items': items},
                             ensure_ascii=False, indent=2))
            self.cursors.update(cursors)
            if self.state_file:
                with open(self.state_file, 'w') as f:
                    json.dump(self.cursors, f, ensure_ascii=False, indent=2)
            if not seed:
                self.added += len(items)
            return {'added': 0 if seed else len(items), 'duplicates': 0}
        r = {'added': 0, 'duplicates': 0}
        # In batches, so one request never grows too large for Apps Script; cursors go with the last batch only.
        batches = [items[i:i + 100] for i in range(0, len(items), 100)] or [[]]
        for i, b in enumerate(batches):
            last = i == len(batches) - 1
            x = gateway('inbox_add', source=source, items=b, cursors=cursors if last else {}, seed=bool(seed),
                        error=error if last else '')
            r['added'] += x.get('added', 0)
            r['duplicates'] += x.get('duplicates', 0)
        self.cursors.update(cursors)
        self.added += r['added']
        self.duplicates += r['duplicates']
        return r


def report_error(source, error, dry=False):
    """A collector that failed tells the gateway, so the research run can say «the ΦΕΚ collector is down»."""
    if dry:
        print(json.dumps({'source': source, 'error': error, 'items': []}, ensure_ascii=False))
        return
    try:
        gateway('inbox_add', source=source, items=[], cursors={}, seed=False, error=str(error)[:500])
    except Exception as e:  # the gateway itself is down: nothing more to do
        log(f'  could not report the error of {source}: {e}')
