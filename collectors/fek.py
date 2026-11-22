"""ΦΕΚ (Εφημερίδα της Κυβερνήσεως), τεύχη Α και Β, through the documented API of the National Printing House.

The API (https://search.et.gr/el/ypiresia-api-fek/) returns one sheet when you know issue, year and number; it has
no search. So this collector walks the numbers: it remembers the last number it processed per issue and year
(cursor `fek_<issue>_<year>`), asks for the next ones until several in a row do not exist, reads the first pages of
each new PDF and keeps the sheets that concern education.

Numbers that did not exist yet while later ones did (sheets are not always released in order) are kept in
`fek_<issue>_<year>_gaps` and tried again on the next runs.

Limits of the API: GET only (HEAD answers 404), 100 requests a minute per IP. We stay near 50.
"""
import datetime, os, re, subprocess, tempfile

from .common import Http, clip, hits, item, log, norm, squeeze
from .relevance import STRONG, judge

API = 'https://api.et.gr/apiFEK/{issue}/{year}/{n}/{fmt}'
ISSUES = {1: 'Α', 2: 'Β'}
BACKFILL = {1: 10, 2: 40}        # first run: how many sheets back from the latest
MISS_LIMIT = 8                   # consecutive missing numbers that end the walk
MAX_NEW = {1: 40, 2: 150}        # sheets per run and issue (a normal day: Α 0-3, Β 20-40)
GAP_KEEP = 40                    # missing numbers we keep retrying
GAP_MAX_BEHIND = {1: 30, 2: 400}  # ... until the cursor is this far ahead of them
PDF_MAX = 60 * 1024 * 1024
MONTHS = {'ιανουαριου': 1, 'φεβρουαριου': 2, 'μαρτιου': 3, 'απριλιου': 4, 'μαιου': 5, 'ιουνιου': 6, 'ιουλιου': 7,
          'αυγουστου': 8, 'σεπτεμβριου': 9, 'οκτωβριου': 10, 'νοεμβριου': 11, 'δεκεμβριου': 12}

# ---- relevance (topics are shared with Διαύγεια: relevance.py) ---------------------------------------------
# The act is of national reach when a minister (or the President, a law) issues it. Acts of one university,
# one hospital, one municipality are local.
NATIONAL = ['ο υπουργοσ', 'η υπουργοσ', 'οι υπουργοι', 'ο υφυπουργοσ', 'η υφυπουργοσ', 'οι υφυπουργοι',
            'ο αναπληρωτησ υπουργοσ', 'η αναπληρωτρια υπουργοσ', 'ο προεδροσ τησ ελληνικησ δημοκρατιασ',
            'η προεδροσ τησ ελληνικησ δημοκρατιασ', 'νομοσ υπ', 'προεδρικο διαταγμα υπ', 'ο διοικητησ τησ δημοσιασ υπηρεσιασ απασχολησησ',
            'ο γενικοσ γραμματεασ', 'η γενικη γραμματεασ', 'ο υπηρεσιακοσ γραμματεασ', 'η υπηρεσιακη γραμματεασ']
# A title that names one institution is a local act (a university's regulation, a municipality's chart).
LOCAL = ['πανεπιστημιου ', 'πανεπιστημιο ', 'πολυτεχνειου', 'συγκλητου', 'πρυταν', ' δημου ', 'νοσοκομει', 'κολλεγιου',
         'κδβμ', 'κεντρου δια βιου μαθησησ', 'ιδιωτικου σχολειου', 'ιδιωτικου γυμνασιου', 'ιδιωτικου λυκειου',
         'του τμηματοσ ', 'τησ σχολησ ', 'περιφερειασ ', 'ιερασ ', 'επιμελητηρι', 'φροντιστηριου', 'τησ εταιρειασ',
         'α.ε.»', 'ε.π.ε.»']
# ... and so is one that names one school («14ου Γενικού Λυκείου Θεσσαλονίκης»).
LOCAL_SCHOOL = re.compile(r'\b\d+ου? (γενικου |ημερησιου |εσπερινου |πειραματικου |προτυπου )?(λυκειου|γυμνασιου|δημοτικου|νηπιαγωγειου|επαλ|εκ)\b')
# Topics that are worth an item even from a single institution.
LOCAL_OK = [('υποτροφ',), ('ιδρυση', 'τμηματοσ', 'πανεπιστημι'), ('ξενογλωσσ', 'προγραμμα', 'σπουδων'), ('βραβει',), ('αριστει',),
            ('μη κρατικ', 'πανεπιστημι'), ('νομικα προσωπα πανεπιστημιακησ εκπαιδευσησ',)]
# Routine acts of ΦΕΚ on top of relevance.HARD. Dropped unless a STRONG topic is in the subject too.
SOFT = ['οργανισμοσ του', 'οργανισμου του',
        'μετονομασια', 'συγχωνευσ', 'υποβιβασμ', 'ιδρυση εργαστηριου', 'ιδρυση ερευνητικου', 'διαπιστωση', 'ανακληση',
        'διορθωση σφαλμ', 'προγραμμα μεταπτυχιακων σπουδων', 'προγραμματοσ μεταπτυχιακων σπουδων', 'πμσ', 'δπμσ',
        'διιδρυματικ', 'μεταπτυχιακ', 'διδακτορικ', 'ωραριο λειτουργιασ', 'χορηγηση αδειασ', 'αδεια ιδρυσησ',
        'αδεια λειτουργιασ', 'τροποποιηση αδειασ', 'ιδρυση σχολικ', 'ιδρυση νηπιαγωγει', 'ιδρυση δημοτικ',
        'προαγωγη σχολικ', 'υπαγωγη', 'απονομη', 'ισοτιμια', 'αναγνωριση', 'καθορισμοσ αμοιβ', 'τμηματων ενταξησ',
        'αστυφυλακ', 'αστυνομ', 'πυροσβεστ', 'λιμενικ', 'ενοπλων δυναμεων']


def classify(title, head='', national_issue=False):
    """Returns (category, reason) to keep an act, or (None, reason) to drop it.
    title = the title of the act. head = the text that follows it up to the signer («Ο ΥΠΟΥΡΓΟΣ ΠΑΙΔΕΙΑΣ…»), when
    the sheet has this one act; a minister's act is national whatever its title names."""
    s = norm(title)
    cat, why = judge(s, soft=SOFT)
    if not cat:
        return None, why
    s2 = ' ' + s.replace('.', '') + ' '
    local = any(w in s2 or w in ' ' + s + ' ' for w in LOCAL) or bool(LOCAL_SCHOOL.search(s))
    # Πανελλαδικές, μηχανογραφικό, εισακτέοι… concern everyone, whoever signs and whatever the title names.
    national = national_issue or bool(head and hits(norm(head), NATIONAL)) or not local or bool(hits(s, STRONG))
    if not national and not hits(s, LOCAL_OK):
        return None, 'local act (one institution): ' + why
    return cat, why


# ---- reading a sheet -------------------------------------------------------------------------------------------

def pdf_text(data, pages=3):
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(data)
        path = f.name
    try:
        r = subprocess.run(['pdftotext', '-l', str(pages), '-enc', 'UTF-8', path, '-'], capture_output=True, timeout=120)
        return r.stdout.decode('utf-8', 'ignore')
    finally:
        os.unlink(path)


END_OF_SUBJECT = re.compile(r'(Έχοντας\s+υπ[όο]ψη|Έχοντας\s+υπ[’\']\s*όψη|Αφού\s+έλαβε|Λαμβάνοντας\s+υπόψη|'
                            r'^\s*Άρθρο\s+(1|πρώτο)\b|^\s*ΜΕΡΟΣ\s+Α|^\s*ΚΕΦΑΛΑΙΟ\s+Α|Εκδίδομε\s+τον\s+ακόλουθο)',
                            re.M | re.I)
SIGNER = re.compile(r'^\s*(Ο|Η|ΟΙ|ΤΟ)\s+[Α-ΩΆ-Ώ.\s\-,–]{6,}$', re.M)
CONTENTS = re.compile(r'ΠΕΡ\s?ΙΕΧΟΜΕΝΑ')
SECTION = r'(?:ΑΠΟΦΑΣΕΙΣ|ΠΡΟΕΔΡΙΚΑ ΔΙΑΤΑΓΜΑΤΑ|ΠΡΑΞΕΙΣ[^0-9]{0,60}?|ΔΙΟΡΘΩΣΕΙΣ ΣΦΑΛΜΑΤΩΝ|ΚΑΝΟΝΙΣΜΟΙ|ΕΓΚΥΚΛΙΟΙ)'
# One line of «ΠΕΡΙΕΧΟΜΕΝΑ»: running number, title, page number.
ENTRY = re.compile(r'(?:^|\s)(\d{1,2})\s+(\S.{12,900}?)\s+\.{0,}\s*(\d{3,6})(?=\s+\d{1,2}\s+\S|\s+' + SECTION + r'|\s*$)')


def split_acts(body):
    """The titles of the acts of a sheet. A sheet with several acts opens with «ΠΕΡΙΕΧΟΜΕΝΑ» (number, title, page);
    a sheet with one act has its title between the masthead and the signer."""
    flat = squeeze(body)
    m = CONTENTS.search(flat[:400])
    if m:
        rest = flat[m.end():]
        # The list ends where the first act begins: «ΑΠΟΦΑΣΕΙΣ (1)» or «ΑΠΟΦΑΣΕΙΣ Αριθμ.».
        end = re.search(SECTION + r'\s+(\(1\)|Αριθμ)', rest)
        block = rest[:end.start()] if end else rest[:3000]
        block = re.sub(r'^\s*' + SECTION + r'\s+', '', block)
        block = re.sub(r'\s+' + SECTION + r'\s+(?=\d{1,2}\s)', ' ', block)      # section headings inside the list
        acts = [squeeze(t).strip(' .') for _, t, _ in ENTRY.findall(block)]
        if acts:
            return acts
    m = END_OF_SUBJECT.search(body)
    subject = body[:m.start()] if m and m.start() > 20 else body[:900]
    ms = list(SIGNER.finditer(subject))          # the signer line («Ο ΥΠΟΥΡΓΟΣ ΠΑΙΔΕΙΑΣ…») closes the title
    if ms and ms[-1].start() > 40:
        subject = subject[:ms[-1].start()]
    subject = squeeze(subject)
    subject = re.sub(r'^(ΠΕΡ\s?ΙΕΧΟΜΕΝΑ\s+)?(' + SECTION + r'\s+)?', '', subject)
    subject = re.sub(r'^(\(\d+\)\s+)?Αριθμ\.?\s*\S+(\s+οικ\.?\s*\S+)?\s+', '', subject).strip(' ·')
    return [subject] if subject else []


def parse_sheet(text, number):
    """From the text of the first pages: (date 'YYYY-MM-DD' or '', [titles of the acts], head)."""
    lines = [squeeze(l) for l in text.split('\n')]
    # Everything before «Αρ. Φύλλου» is the signature stamp and the masthead.
    start = next((i for i, l in enumerate(lines) if re.search(r'Αρ\.\s*Φύλλου', l)), None)
    date = ''
    for l in lines[:(start or 40) + 2]:
        m = re.search(r'(\d{1,2})\s+([Α-Ωα-ωά-ώ]+)\s+(20\d\d)', l)
        if m and norm(m.group(2)) in MONTHS:
            date = '%s-%02d-%02d' % (m.group(3), MONTHS[norm(m.group(2))], int(m.group(1)))
            break
    body = '\n'.join(l for l in lines[(start + 1 if start is not None else 0):] if l)
    body = re.sub(r'(?m)^ΕΦΗΜΕΡΙ[∆Δ]Α T?Τ?ΗΣ ΚΥΒΕΡΝΗΣΕΩΣ.*$', '', body)      # running header of the next pages
    return date, split_acts(body), body[:2500]


def stub(http, issue, year, n):
    """The small HTML answer: (exists, viewer url)."""
    status, data = http.get(API.format(issue=issue, year=year, n=n, fmt='html'), timeout=30)
    if status == 404:
        return False, ''
    if status != 200:
        raise RuntimeError(f'ΦΕΚ {ISSUES[issue]} {n}/{year}: HTTP {status}')
    m = re.search(r'href="(https://search\.et\.gr/[^"]+)"', data.decode('utf-8', 'ignore'))
    return True, (m.group(1) if m else API.format(issue=issue, year=year, n=n, fmt='pdf'))


def latest(http, issue, year):
    """The highest number that exists, or 0. Doubling then bisection; a hole is stepped over by looking a few ahead."""
    def around(n):
        return any(stub(http, issue, year, k)[0] for k in (n, n + 1, n + 3))
    if not around(1):
        return 0
    lo, hi = 1, 64
    while around(hi):
        lo, hi = hi, hi * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if around(mid):
            lo = mid
        else:
            hi = mid
    return lo


def read_sheet(http, issue, year, n, url):
    """(item or None, reason, titles) for one sheet. A sheet with several acts gives one item naming the acts kept."""
    status, data = http.get(API.format(issue=issue, year=year, n=n, fmt='pdf'), timeout=180, max_bytes=PDF_MAX)
    if status != 200:
        return None, f'pdf HTTP {status}', ''
    text = pdf_text(data, pages=8 if issue == 1 else 3)
    if len(text.strip()) < 200:
        return None, 'no text in the PDF (scanned?)', ''
    date, acts, head = parse_sheet(text, n)
    kept, why = judge_sheet(acts, head, issue)
    if not kept and issue == 1:
        # A law's title often hides what it holds («… και άλλες διατάξεις»): look at the titles of its articles.
        arts = law_articles(text)
        if arts:
            kept = [('policy', clip(acts[0] if acts else '', 200) + ' | Άρθρα: ' + ' · '.join(arts[:6]))]
            why = 'articles of the law'
    if not kept:
        return None, why, ' · '.join(acts)
    name = f'ΦΕΚ {ISSUES[issue]}΄ {n}/{year}'
    title = kept[0][1] if len(kept) == 1 else f'{len(kept)} πράξεις: ' + ' · '.join(clip(t, 110) for _, t in kept)
    more = f' (το φύλλο έχει {len(acts)} πράξεις)' if len(acts) > len(kept) else ''
    it = item('ΦΕΚ', f'{name}: {clip(title, 240)}', url, date, ' · '.join(t for _, t in kept) + more, kept[0][0],
              ref=f'fek-{issue}-{year}-{n}')
    return it, why, ' · '.join(acts)


def law_articles(text):
    """Titles of a law's articles (from its table of contents on the first pages) that are about education.
    Kept only when one of them is a nationwide topic (STRONG), or at least three are on topic."""
    out, strong = [], False
    for m in re.finditer(r'(?m)^\s*Άρθρο\s+\d+[Α-Ω]?\s*[:.–-]?\s+(\S.{15,260}?)\s*$', text):
        t = squeeze(m.group(1))
        s = norm(t)
        cat, _ = judge(s, soft=SOFT)
        if cat and t not in out:
            out.append(t)
            strong = strong or bool(hits(s, STRONG))
    return out if strong or len(out) >= 3 else []


def judge_sheet(acts, head, issue):
    """([(category, title)] of the acts kept, reason). With several acts the head names only the first signer, so
    for them «local» is read from the title alone."""
    kept, why = [], 'no acts found'
    for t in acts:
        cat, why1 = classify(t, head if len(acts) == 1 else '', national_issue=(issue == 1))
        if cat:
            kept.append((cat, t))
        if cat or why == 'no acts found' or why.startswith('no education'):
            why = why1
    return kept, why


def collect_issue(run, http, issue, year, explain=False):
    key, gkey = f'fek_{issue}_{year}', f'fek_{issue}_{year}_gaps'
    last = run.cursor(key)
    if last is None:
        top = latest(http, issue, year)
        if not top:
            return [], {}, 0
        last = max(0, top - BACKFILL[issue])
        log(f'  ΦΕΚ {ISSUES[issue]} {year}: no cursor, latest is {top}, starting after {last}')
    last = int(last)
    gaps = [int(x) for x in str(run.cursor(gkey, '')).split(',') if x.strip().isdigit()]
    todo, found, misses, n = [], [], 0, last
    for g in gaps:                                   # numbers that were missing last time
        ok, url = stub(http, issue, year, g)
        if ok:
            todo.append((g, url))
    still = [g for g in gaps if g not in [t[0] for t in todo]]
    while misses < MISS_LIMIT and len(todo) < MAX_NEW[issue]:
        n += 1
        ok, url = stub(http, issue, year, n)
        if ok:
            todo.append((n, url)); found.append(n); misses = 0
        else:
            misses += 1
    new_last = max(found) if found else last
    still += [k for k in range(last + 1, new_last) if k not in found]
    still = [g for g in sorted(set(still)) if new_last - g <= GAP_MAX_BEHIND[issue]][-GAP_KEEP:]
    items = []
    for k, url in todo:
        try:
            it, why, subject = read_sheet(http, issue, year, k, url)
        except Exception as e:  # one bad PDF must not lose the day
            run.note(f'ΦΕΚ {ISSUES[issue]} {k}/{year}: {e}')
            continue
        if explain:
            log(f'  {"KEEP" if it else "drop"} {ISSUES[issue]} {k}: [{why}] {clip(subject, 150)}')
        if it:
            items.append(it)
    return items, {key: new_last, gkey: ','.join(map(str, still))}, len(todo)


def collect(run, explain=False):
    http = Http(gap=1.2)
    year = datetime.date.today().year
    items, cursors, seen = [], {}, 0
    years = [year]
    # January: the last sheets of the previous year still come out for some days. Follow it while it has a cursor.
    if datetime.date.today().month == 1 and any(run.cursor(f'fek_{i}_{year - 1}') is not None for i in ISSUES):
        years.insert(0, year - 1)
    for y in years:
        for issue in ISSUES:
            if y != year and run.cursor(f'fek_{issue}_{y}') is None:
                continue
            its, cur, n = collect_issue(run, http, issue, y, explain)
            items += its; cursors.update(cur); seen += n
    run.note(f'ΦΕΚ: {seen} new sheets read, {len(items)} about education, {http.count} requests')
    run.post('fek', items, cursors)
    return len(items)
