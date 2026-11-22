"""Trends: what people in Greece are paying attention to this week.

Not an education source. It gives the research run its leads for «trend» ideas: a game, a film, a product, a person,
a statement, an event, a song, anything many people follow right now. The research decides whether a trend has an
honest link to an education topic.

Signals (all plain requests to pages whose robots.txt allows them):
  wikipedia   Greek Wikipedia, most viewed articles per day (Wikimedia REST API). A trend is an article that stays in
              the top 30 for three of the last four days and was clearly lower ten days ago. Any subject.
  wiki-gr     Most viewed Wikipedia articles from Greece in any language (Wikimedia, top per country). Catches what
              Greeks look up in English: films, series, products, people. In the list on two of the last three days.
  google      Google Trends daily searches for Greece (RSS), with the headline Google attaches. A search that comes
              back on a second day, or a very large one, is a trend on its own; the rest only confirm other signals.
  x           Trending on X in Greece (getdaytrends.com). What is being argued about: television, statements, sport,
              events. A term that is still there on a second day is a trend; the rest only confirm other signals.
  twitch      Most watched games on Twitch with their 7-day change (twitchtracker.com). Worldwide, not Greece.
              Skipped on the days the site refuses the runner's address.
  steam       Top sellers of the Steam store for Greece. New names only.
  spotify     Spotify daily chart for Greece (kworb.net): songs that entered high in the last three days.

Signals about the same name are merged into one Inbox item, with every signal in the excerpt. A trend is sent once a
month (the ref holds the month), so a trend that stays for weeks does not come back every day. What Google and X
showed on the previous days, and the Steam names already seen, are kept as cursors in the gateway.

Not read: TikTok Creative Center (refuses a headless browser), YouTube trending (behind a consent page), Netflix
Top 10 and Box Office Mojo (their robots.txt disallows everything), trends24.in (asks AI tools to stay out).
"""
import datetime, html, json, re, urllib.parse

from .common import Http, clip, item, log, norm

MAX_ITEMS, MAX_WIKI_ONLY = 10, 4     # per run, strongest first; at most 4 that only Wikipedia shows
WIKI = 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/el.wikipedia/all-access/{y}/{m:02d}/{d:02d}'
WIKI_TOP, WIKI_DAYS, WIKI_NEED = 30, 4, 3     # in the top 30 on 3 of the last 4 days
WIKI_BASE_DAYS = (9, 10, 11)                    # … and clearly lower on these days before (see wiki_trends)
WIKI_SKIP = re.compile(r'^(Πύλη|Ειδικό|Βικιπαίδεια|Αρχείο|Κατηγορία|Βοήθεια|Πρότυπο|Χρήστης|Special|Wikipedia|Portal|File|'
                       r'Help|Category|Template|User):|^\d{1,2}_[Α-ΩA-Z]|^\d{3,4}$|^Κύρια_Σελίδα$|^Main_Page$', re.I)
WIKI_GR = 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top-per-country/GR/all-access/{y}/{m:02d}/{d:02d}'
WIKI_GR_DAYS, WIKI_GR_NEED = 3, 2
GOOGLE = 'https://trends.google.com/trending/rss?geo=GR'
GOOGLE_BIG = 5000             # searches: a term this large is a trend on its first day
X = 'https://getdaytrends.com/greece/'
X_TOP = 25
MEMORY_DAYS = 5               # how many days of Google and X terms are remembered
TWITCH = 'https://twitchtracker.com/games'
TWITCH_TOP, TWITCH_RISE, TWITCH_VIEWERS = 15, 100.0, 20000
TWITCH_NOT_GAMES = {'just chatting', 'irl', 'music', 'sports', 'asmr', 'slots', 'virtual casino', 'special events',
                    'talk shows & podcasts', 'art', 'pools, hot tubs, and beaches', 'i\'m only sleeping', 'chess',
                    'software and game development', 'travel & outdoors', 'food & drink', 'politics'}
STEAM = 'https://store.steampowered.com/api/featuredcategories?cc=gr&l=english'
STEAM_TOP, STEAM_KEEP = 10, 60
STEAM_SKIP = re.compile(r'^(Steam (Deck|Machine|Frame|Controller|Link)|Valve Index)', re.I)
SPOTIFY = 'https://kworb.net/spotify/country/gr_daily.html'
SPOTIFY_TOP, SPOTIFY_DAYS, SPOTIFY_MAX = 15, 3, 3
# Daily greetings and calendar terms that trend on X every day and mean nothing.
X_SKIP = re.compile(r'^(καλη ?μερα|καλημερα|καλη (κυριακη|εβδομαδα|δευτερα|τριτη|τεταρτη|πεμπτη|παρασκευη)|καλο (σαββατοκυριακο|μηνα|βραδυ)|'
                    r'καληνυχτα|καλησπερα|χρονια πολλα|\d{1,2} [α-ω]+)$')


# ---------------------------------------------------------------- parsers (pure functions, tested)

def wiki_rank(payload):
    """{article: rank} of one day, without namespaces, date pages and the main page; ranks renumbered."""
    out, n = {}, 0
    for a in payload['items'][0]['articles']:
        name = a['article']
        if WIKI_SKIP.search(name):
            continue
        n += 1
        out[name] = n
    return out


def wiki_trends(days, base):
    """days: newest first, [{article: rank}]. base: older days. → [(article, [rank or None per day])]"""
    out = []
    for art in days[0] if days else {}:
        ranks = [d.get(art) for d in days]
        if sum(1 for r in ranks if r and r <= WIKI_TOP) < WIKI_NEED:
            continue
        best, before = min(r for r in ranks if r), min(b.get(art, 10 ** 6) for b in base)
        if before <= 2 * best + 5:
            continue                      # it was about as high ten days ago: a resident, not a trend
        out.append((art, ranks))
    return sorted(out, key=lambda x: min(r for r in x[1] if r))


def wiki_gr_day(payload):
    """{(project, article): views} of one day of «top per country», without files and special pages."""
    out = {}
    for a in payload['items'][0]['articles']:
        if WIKI_SKIP.search(a['article']) or not a.get('project', '').endswith('.wikipedia'):
            continue
        out[(a['project'], a['article'])] = a.get('views_ceil', 0)
    return out


def twitch_games(page):
    """[(position, name, game id, average viewers, 7-day change in % (signed))]"""
    out = []
    for block in page.split('<div class="ranked-item">')[1:]:
        pos = re.search(r'ri-position[^>]*>#(\d+)<', block)
        name = re.search(r'<a href="/games/(\d+)">([^<]+)</a>', block)
        val = re.search(r'ri-value.*?to-number-lg">([\d.]+)<', block, re.S)
        chg = re.search(r'ri-change.*?(caret-up|caret-down).*?to-number-lg">([\d.]+)<', block, re.S)
        if not (pos and name and val):
            continue
        change = (float(chg.group(2)) * (1 if chg.group(1) == 'caret-up' else -1)) if chg else 0.0
        out.append((int(pos.group(1)), html.unescape(name.group(2)).strip(), name.group(1), float(val.group(1)), change))
    return out


def spotify_rows(page):
    """[(position, change text, artist, title, days on the chart)]"""
    out = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', page, re.S)[1:]:
        cells = [html.unescape(re.sub(r'<[^>]+>', '', c)).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
        if len(cells) < 4 or not cells[0].isdigit() or not cells[3].isdigit():
            continue
        artist, _, title = cells[2].partition(' - ')
        out.append((int(cells[0]), cells[1], artist.strip(), title.strip(), int(cells[3])))
    return out


def google_items(feed):
    """[(search term, searches as a number, the first headline Google attaches)]"""
    out = []
    for it in re.findall(r'<item>(.*?)</item>', feed, re.S):
        def tag(name):
            m = re.search(r'<' + name + r'>(.*?)</' + name + '>', it, re.S)
            return html.unescape(m.group(1)).strip() if m else ''
        if tag('title'):
            digits = re.sub(r'\D', '', tag('ht:approx_traffic'))
            out.append((tag('title'), int(digits) if digits else 0, tag('ht:news_item_title')))
    return out


def x_terms(page):
    """Trending terms on X, in page order, without repeats."""
    out = []
    for t in re.findall(r'<a[^>]*href="[^"]*/trend/[^"]*"[^>]*>([^<]{2,60})</a>', page):
        t = html.unescape(t).strip()
        if t and t not in out:
            out.append(t)
    return out


def gr(n):
    """1234567.8 → «1.234.568» (Greek thousands separator)."""
    return f'{n:,.0f}'.replace(',', '.')


def key_of(name):
    """What two sources must share to be the same trend: letters and digits only."""
    return re.sub(r'[^0-9a-zα-ω]+', ' ', norm(name.replace('_', ' '))).strip()


def same(a, b):
    """Same trend: equal keys, or one name inside the other as whole words («grand theft auto» in «grand theft auto vi»)."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 6 and re.search(r'(^| )' + re.escape(short) + r'( |$)', long_) is not None


def remember(memory, today, keys):
    """memory: {iso day: [keys]}. Adds today, drops old days. → (new memory, how many EARLIER days each key was seen)"""
    days = {d: v for d, v in (memory or {}).items() if d != today}
    seen = {k: sum(1 for v in days.values() if k in v) for k in keys}
    days[today] = list(keys)
    return {d: days[d] for d in sorted(days)[-MEMORY_DAYS:]}, seen


def merge(cands):
    """cands: [{name, url, src, signal, strength, weak?}] → one entry per trend, strongest first.

    A weak signal (a single day on Google or X) cannot start a trend; it is added to a trend another source shows.
    """
    groups = []
    for c in sorted(cands, key=lambda c: bool(c.get('weak'))):
        k = key_of(c['name'])
        g = next((g for g in groups if any(same(k, x) for x in g['keys'])), None)
        if not g:
            if c.get('weak'):
                continue
            g = {'name': c['name'], 'url': c['url'], 'keys': [], 'signals': [], 'strength': 0, 'sources': set(), 'top': -1}
            groups.append(g)
        if c['strength'] > g['top'] and not c.get('weak'):      # the trend is named after its strongest signal
            g['top'], g['name'], g['url'] = c['strength'], c['name'], c['url']
        g['keys'].append(k)
        g['signals'].append(c['signal'])
        g['strength'] += c['strength']
        g['sources'].add(c['src'])
    return sorted(groups, key=lambda g: (-len(g['sources']), -g['strength']))


def pick(trends):
    """The items of one run: at most MAX_ITEMS, of which at most MAX_WIKI_ONLY that only Greek Wikipedia shows."""
    out, wiki_only = [], 0
    for g in trends:
        if g['sources'] == {'wikipedia'}:
            if wiki_only >= MAX_WIKI_ONLY:
                continue
            wiki_only += 1
        out.append(g)
        if len(out) == MAX_ITEMS:
            break
    return out


# ---------------------------------------------------------------- collect

def collect(run, explain=False):
    http = Http(gap=1.0)
    today = datetime.date.today()
    iso = today.isoformat()
    cands, errors, cursors = [], [], {}
    try:
        memory = json.loads(run.cursor('trends_memory') or '{}')
    except ValueError:
        memory = {}

    def source(name, fn):
        try:
            fn()
        except Exception as e:  # one source down must not lose the others
            errors.append(f'{name}: {type(e).__name__}: {str(e)[:120]}')
            log(f'  FAIL {name}: {e}')

    def get_day(url, back):
        d = today - datetime.timedelta(days=back)
        status, data = http.get(url.format(y=d.year, m=d.month, d=d.day), timeout=40)
        return json.loads(data.decode('utf-8')) if status == 200 else None

    def wikipedia():
        def day(back):
            p = get_day(WIKI, back)
            return wiki_rank(p) if p else None
        start = 1 if day(1) is not None else 2            # yesterday is published some hours after midnight UTC
        days = [x for x in (day(start + i) for i in range(WIKI_DAYS)) if x]
        base = [x for x in (day(start - 1 + b) for b in WIKI_BASE_DAYS) if x]
        if len(days) < WIKI_NEED or not base:
            raise RuntimeError(f'only {len(days)} recent and {len(base)} older days available')
        for art, ranks in wiki_trends(days, base):
            name = art.replace('_', ' ')
            shown = ', '.join('–' if not r else f'{r}η' for r in ranks)
            cands.append({'name': name, 'url': 'https://el.wikipedia.org/wiki/' + urllib.parse.quote(art), 'src': 'wikipedia',
                          'signal': f'Wikipedia (el), «{name}»: θέση στις προβολές, {len(ranks)} μέρες, νεότερη πρώτη: {shown}',
                          'strength': 40 - min(r for r in ranks if r)})

    def wiki_gr():
        days = []
        for back in range(1, WIKI_GR_DAYS + 2):
            p = get_day(WIKI_GR, back)
            if p:
                days.append(wiki_gr_day(p))
        days = days[:WIKI_GR_DAYS]
        if len(days) < WIKI_GR_NEED:
            raise RuntimeError(f'only {len(days)} days available')
        for (project, art), views in sorted(days[0].items(), key=lambda kv: -kv[1]):
            n = sum(1 for d in days if (project, art) in d)
            if n < WIKI_GR_NEED or project == 'el.wikipedia':     # the Greek edition is covered, more finely, above
                continue
            name = re.sub(r'\s*\([^)]*\)$', '', art.replace('_', ' '))
            cands.append({'name': name, 'url': f'https://{project}.org/wiki/' + urllib.parse.quote(art), 'src': 'wiki-gr',
                          'signal': f'Wikipedia από Ελλάδα ({project.split(".")[0]}), «{art.replace("_", " ")}»: '
                                    f'στα πιο διαβασμένα {n} από τις {len(days)} τελευταίες μέρες, έως {gr(views)} προβολές χθες',
                          'strength': 22})

    def google():
        status, data = http.get(GOOGLE, timeout=40)
        if status != 200:
            raise RuntimeError(f'HTTP {status}')
        rows = google_items(data.decode('utf-8', 'replace'))
        if not rows:
            raise RuntimeError('no items parsed (feed changed?)')
        memory['google'], seen = remember(memory.get('google'), iso, [key_of(t) for t, _, _ in rows])
        for term, n, headline in rows:
            before = seen.get(key_of(term), 0)
            strong = before >= 1 or n >= GOOGLE_BIG
            what = f'Google Trends Ελλάδα, «{term}»: {gr(n)}+ αναζητήσεις σήμερα' + (f', και {before} από τις προηγούμενες μέρες' if before else '')
            cands.append({'name': term, 'url': 'https://trends.google.com/trending?geo=GR', 'src': 'google', 'weak': not strong,
                          'signal': what + (f' («{clip(headline, 110)}»)' if headline else ''), 'strength': 12 + min(10, n // 1000)})

    def x():
        status, data = http.get(X, timeout=40)
        if status != 200:
            raise RuntimeError(f'HTTP {status}')
        terms = [t for t in x_terms(data.decode('utf-8', 'replace'))[:X_TOP] if not X_SKIP.search(key_of(t))]
        if not terms:
            raise RuntimeError('no terms parsed (page changed?)')
        memory['x'], seen = remember(memory.get('x'), iso, [key_of(t) for t in terms])
        for i, term in enumerate(terms):
            before = seen.get(key_of(term), 0)
            cands.append({'name': term, 'url': X, 'src': 'x', 'weak': before < 1,
                          'signal': f'X Ελλάδα, «{term}»: στα trends σήμερα (#{i + 1})' + (f', και {before} από τις προηγούμενες μέρες' if before else ''),
                          'strength': 14 - min(10, i // 3)})

    def twitch():
        status, data = http.get(TWITCH, timeout=40)
        if status in (403, 429):          # the site turns some data-centre addresses away: a refusal, not a fault
            log(f'  twitch: HTTP {status}, skipped today')
            return
        if status != 200:
            raise RuntimeError(f'HTTP {status}')
        games = twitch_games(data.decode('utf-8', 'replace'))
        if not games:
            raise RuntimeError('no games parsed (page changed?)')
        for pos, name, gid, viewers, change in games:
            if pos > TWITCH_TOP or name.lower() in TWITCH_NOT_GAMES or viewers < TWITCH_VIEWERS or change < TWITCH_RISE:
                continue
            cands.append({'name': name, 'url': f'https://twitchtracker.com/games/{gid}', 'src': 'twitch',
                          'signal': f'Twitch (παγκόσμια): #{pos} σε θεατές, {gr(viewers)} κατά μέσο όρο, +{gr(change)}% σε 7 μέρες',
                          'strength': 30 - pos})

    def steam():
        d = http.json(STEAM, timeout=40)
        names = []
        for it in d.get('top_sellers', {}).get('items', []):
            n = (it.get('name') or '').strip()
            if n and n not in [x for x, _ in names] and not STEAM_SKIP.search(n):
                names.append((n, it.get('id')))
        top = names[:STEAM_TOP]
        if not top:
            raise RuntimeError('no top sellers parsed')
        raw = run.cursor('trends_steam_top')
        known = json.loads(raw) if raw else None
        if known is not None:
            for i, (n, app) in enumerate(top):
                if n not in known:
                    cands.append({'name': n, 'url': f'https://store.steampowered.com/app/{app}' if app else STEAM, 'src': 'steam',
                                  'signal': f'Steam Ελλάδα: νέο στις 10 πρώτες πωλήσεις (#{i + 1})', 'strength': 20 - i})
        keep = [n for n, _ in top] + [n for n in (known or []) if n not in [x for x, _ in top]]
        cursors['trends_steam_top'] = json.dumps(keep[:STEAM_KEEP], ensure_ascii=False)

    def spotify():
        status, data = http.get(SPOTIFY, timeout=40)
        if status != 200:
            raise RuntimeError(f'HTTP {status}')
        rows = spotify_rows(data.decode('utf-8', 'replace'))
        if not rows:
            raise RuntimeError('no rows parsed (page changed?)')
        for pos, _, artist, title, days in [r for r in rows if r[0] <= SPOTIFY_TOP and r[4] <= SPOTIFY_DAYS][:SPOTIFY_MAX]:
            cands.append({'name': f'{artist}: {title}', 'url': SPOTIFY, 'src': 'spotify',
                          'signal': f'Spotify Ελλάδα: #{pos}, {days} {"μέρα" if days == 1 else "μέρες"} στο chart',
                          'strength': 16 - pos})

    sources = [('wikipedia', wikipedia), ('wiki-gr', wiki_gr), ('google', google), ('x', x), ('twitch', twitch),
               ('steam', steam), ('spotify', spotify)]
    for name, fn in sources:
        source(name, fn)
    cursors['trends_memory'] = json.dumps(memory, ensure_ascii=False)

    trends = merge(cands)
    chosen = pick(trends)
    items = []
    for g in chosen:
        slug = re.sub(r'\s+', '-', min(g['keys'], key=len))[:60]
        excerpt = ' · '.join(g['signals']) + ' · Ένδειξη για την ομάδα trends, όχι θέμα εκπαίδευσης από μόνο του.'
        items.append(item('trends', 'Trend: ' + g['name'], g['url'], iso, clip(excerpt), ref=f'trend-{slug}-{today:%Y-%m}'))
    if explain:
        for g in trends:
            log(f'  {"KEEP" if g in chosen else "drop"} {g["name"]}: {sorted(g["sources"])}, strength {g["strength"]}')
    run.note(f'trends: {len(cands)} signals, {len(trends)} trends, {len(items)} sent, {http.count} requests'
             + (f'; failed: {"; ".join(errors)}' if errors else ''))
    if len(errors) == len(sources):
        raise RuntimeError('; '.join(errors))
    run.post('trends', items, cursors, error='; '.join(errors))
    return len(items)
