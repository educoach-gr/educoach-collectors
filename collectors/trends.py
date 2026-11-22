"""Trends: what people in Greece (mostly the young) are paying attention to this week.

Not an education source. It gives the research run its leads for «trend» ideas: a game, a series, an artist, a moment
that many people follow right now. The research decides whether a trend has an honest link to an education topic.

Signals (all plain requests to pages whose robots.txt allows them):
  wikipedia   Greek Wikipedia, most viewed articles per day (Wikimedia REST API). A trend is an article that stays in
              the top 30 for three of the last four days and was clearly lower ten days ago.
  twitch      Most watched games on Twitch with their 7-day change (twitchtracker.com). Worldwide, not Greece.
  steam       Top sellers of the Steam store for Greece. New names only (the names seen before are kept as a cursor).
  spotify     Spotify daily chart for Greece (kworb.net): songs that entered high in the last three days.
  google      Google Trends daily searches for Greece (RSS). Only as a second signal next to the others: on its own
              the list is news, sport and people of the day.

One Inbox item per trend, with every signal that shows it in the excerpt. A trend is sent once a month (the ref holds
the month), so a trend that stays for weeks does not come back every day.

Not read: TikTok Creative Center (refuses a headless browser), YouTube trending (behind a consent page), Netflix
Top 10 and Box Office Mojo (their robots.txt disallows everything).
"""
import datetime, html, json, re, urllib.parse

from .common import Http, clip, item, log, norm

MAX_ITEMS = 8                 # per run, strongest first
WIKI = 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/el.wikipedia/all-access/{y}/{m:02d}/{d:02d}'
WIKI_TOP, WIKI_DAYS, WIKI_NEED = 30, 4, 3     # in the top 30 on 3 of the last 4 days
WIKI_BASE_DAYS = (9, 10, 11)                    # … and clearly lower on these days before (see wiki_trends)
WIKI_SKIP = re.compile(r'^(Πύλη|Ειδικό|Βικιπαίδεια|Αρχείο|Κατηγορία|Βοήθεια|Πρότυπο|Χρήστης|Special|Wikipedia|Portal|File):|'
                       r'^\d{1,2}_[Α-ΩA-Z]|^\d{3,4}$|^Κύρια_Σελίδα$', re.I)
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
GOOGLE = 'https://trends.google.com/trending/rss?geo=GR'


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


def google_titles(feed):
    """[(search term, approximate traffic text)]"""
    items = re.findall(r'<item>(.*?)</item>', feed, re.S)
    out = []
    for it in items:
        t = re.search(r'<title>(.*?)</title>', it, re.S)
        tr = re.search(r'<ht:approx_traffic>(.*?)</ht:approx_traffic>', it, re.S)
        if t:
            out.append((html.unescape(t.group(1)).strip(), tr.group(1).strip() if tr else ''))
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


def merge(cands):
    """cands: [{name, url, signal, strength}] → one entry per trend, signals joined, strongest first."""
    groups = []
    for c in cands:
        k = key_of(c['name'])
        g = next((g for g in groups if any(same(k, x) for x in g['keys'])), None)
        if not g:
            g = {'name': c['name'], 'url': c['url'], 'keys': [], 'signals': [], 'strength': 0, 'sources': set(), 'top': -1}
            groups.append(g)
        if c['strength'] > g['top']:      # the trend is named after its strongest signal
            g['top'], g['name'], g['url'] = c['strength'], c['name'], c['url']
        g['keys'].append(k)
        g['signals'].append(c['signal'])
        g['strength'] += c['strength']
        g['sources'].add(c['signal'].split(':')[0])
    return sorted(groups, key=lambda g: (-len(g['sources']), -g['strength']))


# ---------------------------------------------------------------- collect

def collect(run, explain=False):
    http = Http(gap=1.0)
    today = datetime.date.today()
    cands, errors, cursors = [], [], {}

    def source(name, fn):
        try:
            fn()
        except Exception as e:  # one source down must not lose the others
            errors.append(f'{name}: {type(e).__name__}: {str(e)[:120]}')
            log(f'  FAIL {name}: {e}')

    def wikipedia():
        def day(back):
            d = today - datetime.timedelta(days=back)
            status, data = http.get(WIKI.format(y=d.year, m=d.month, d=d.day), timeout=40)
            return wiki_rank(json.loads(data.decode('utf-8'))) if status == 200 else None
        start = 1 if day(1) is not None else 2            # yesterday is published some hours after midnight UTC
        days = [x for x in (day(start + i) for i in range(WIKI_DAYS)) if x]
        base = [x for x in (day(start - 1 + b) for b in WIKI_BASE_DAYS) if x]
        if len(days) < WIKI_NEED or not base:
            raise RuntimeError(f'only {len(days)} recent and {len(base)} older days available')
        for art, ranks in wiki_trends(days, base):
            name = art.replace('_', ' ')
            shown = ', '.join('–' if not r else f'{r}η' for r in ranks)
            cands.append({'name': name, 'url': 'https://el.wikipedia.org/wiki/' + urllib.parse.quote(art),
                          'signal': f'Wikipedia (el), «{name}»: θέση στις προβολές, {len(ranks)} μέρες, νεότερη πρώτη: {shown}',
                          'strength': 40 - min(r for r in ranks if r)})

    def twitch():
        status, data = http.get(TWITCH, timeout=40)
        if status != 200:
            raise RuntimeError(f'HTTP {status}')
        games = twitch_games(data.decode('utf-8', 'replace'))
        if not games:
            raise RuntimeError('no games parsed (page changed?)')
        for pos, name, gid, viewers, change in games:
            if pos > TWITCH_TOP or name.lower() in TWITCH_NOT_GAMES or viewers < TWITCH_VIEWERS or change < TWITCH_RISE:
                continue
            cands.append({'name': name, 'url': f'https://twitchtracker.com/games/{gid}',
                          'signal': f'Twitch (παγκόσμια): #{pos} σε θεατές, {gr(viewers)} κατά μέσο όρο, +{gr(change)}% σε 7 μέρες',
                          'strength': 30 - pos})

    def steam():
        d = http.json(STEAM, timeout=40)
        names, seen_now = [], []
        for it in d.get('top_sellers', {}).get('items', []):
            n = (it.get('name') or '').strip()
            if n and n not in seen_now and not STEAM_SKIP.search(n):
                seen_now.append(n)
                names.append((n, it.get('id')))
        top = names[:STEAM_TOP]
        if not top:
            raise RuntimeError('no top sellers parsed')
        raw = run.cursor('trends_steam_top')
        known = json.loads(raw) if raw else None
        if known is not None:
            for i, (n, app) in enumerate(top):
                if n not in known:
                    cands.append({'name': n, 'url': f'https://store.steampowered.com/app/{app}' if app else STEAM,
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
        new = [r for r in rows if r[0] <= SPOTIFY_TOP and r[4] <= SPOTIFY_DAYS][:SPOTIFY_MAX]
        for pos, _, artist, title, days in new:
            cands.append({'name': f'{artist}: {title}', 'url': SPOTIFY,
                          'signal': f'Spotify Ελλάδα: #{pos}, {days} {"μέρα" if days == 1 else "μέρες"} στο chart',
                          'strength': 16 - pos})

    google = []

    def google_feed():
        status, data = http.get(GOOGLE, timeout=40)
        if status != 200:
            raise RuntimeError(f'HTTP {status}')
        google.extend(google_titles(data.decode('utf-8', 'replace')))

    source('wikipedia', wikipedia)
    source('twitch', twitch)
    source('steam', steam)
    source('spotify', spotify)
    source('google', google_feed)

    trends = merge(cands)
    gkeys = [(key_of(t), tr) for t, tr in google]
    items = []
    for g in trends:
        hit = next((tr for k, tr in gkeys if k and any(same(k, x) for x in g['keys'])), None)
        if hit is not None:
            g['signals'].append('Google Trends Ελλάδα: στις ημερήσιες αναζητήσεις' + (f' ({hit})' if hit else ''))
            g['sources'].add('Google Trends Ελλάδα')
    trends.sort(key=lambda g: (-len(g['sources']), -g['strength']))
    for g in trends[:MAX_ITEMS]:
        slug = re.sub(r'\s+', '-', min(g['keys'], key=len))[:60]
        ref = f'trend-{slug}-{today:%Y-%m}'
        excerpt = ' · '.join(g['signals']) + ' · Ένδειξη για την ομάδα trends: δεν είναι θέμα εκπαίδευσης από μόνο του.'
        items.append(item('trends', 'Trend: ' + g['name'], g['url'], today.isoformat(), clip(excerpt), ref=ref))
        if explain:
            log(f'  KEEP {g["name"]}: {len(g["sources"])} signal source(s), strength {g["strength"]}')
    if explain:
        for g in trends[MAX_ITEMS:]:
            log(f'  drop (over {MAX_ITEMS}) {g["name"]}')
    run.note(f'trends: {len(cands)} signals, {len(trends)} trends, {len(items)} sent, {http.count} requests'
             + (f'; failed: {"; ".join(errors)}' if errors else ''))
    if len(errors) == 5:
        raise RuntimeError('; '.join(errors))
    run.post('trends', items, cursors, error='; '.join(errors))
    return len(items)
