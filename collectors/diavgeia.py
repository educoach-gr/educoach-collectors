"""Διαύγεια (diavgeia.gov.gr): the acts of the bodies that decide on education, through the open data API.

The API works (https://diavgeia.gov.gr/api/help: /opendata/search.json, organizations.json, types.json) but a body
like the Ministry of Education posts ~300 acts a day, nearly all routine (appointments, travel, payments). This
collector asks for the few act types that can carry policy or an opportunity and then keeps an act only when its
subject is about one of our topics and is not a routine act.

Cursor per body: `diavgeia_<uid>` = the day (YYYY-MM-DD) up to which posted acts have been read. The search is by
POSTING date (`from_date`), not issue date: an act issued last week and posted today is new today. The cursor day
is read again on the next run; the gateway drops what it already has.

Organisation ids were looked up in /opendata/organizations.json (19/11/2026). The State Scholarships Foundation
(ΙΚΥ) has no record of its own in Διαύγεια: its scholarship decisions appear under the Ministry and are caught by
the «υποτροφ» topic.
"""
import datetime, urllib.parse

from .common import Http, clip, hits, item, log, norm, squeeze
from .relevance import judge

API = 'https://diavgeia.gov.gr/opendata/'
VIEW = 'https://diavgeia.gov.gr/decision/view/'
# uid → (short name, act types to read)
REGULATORY = ['Α.1.1', 'Α.1.2', 'Α.2', 'Α.3', 'Γ.3.1', '2.4.7.1']
ORGS = {
    '100081880': ('Υπουργείο Παιδείας', REGULATORY),
    '99219675': ('ΔΥΠΑ', REGULATORY),
    '54730': ('ΙΕΠ', REGULATORY),
    '50229': ('ΕΟΠΠΕΠ', REGULATORY),
    '100063848': ('ΕΘΑΑΕ', REGULATORY),
    '99203024': ('ΙΝΕΔΙΒΙΜ', REGULATORY),
}
TYPE_LABEL = {'Α.1.1': 'Νόμος', 'Α.1.2': 'ΠΝΠ', 'Α.2': 'Κανονιστική πράξη', 'Α.3': 'Εγκύκλιος',
              'Γ.3.1': 'Προκήρυξη θέσεων', '2.4.7.1': 'Λοιπή πράξη'}
MINISTRY = '100081880'
FIRST_RUN_DAYS = 3        # no cursor: read the last days
ISSUE_WINDOW_DAYS = 150   # an act posted now may have been issued long ago
PAGE = 500
MAX_PAGES = 12

# Routine acts of these bodies, on top of relevance.HARD: always dropped.
HARD = ['αναπληρωτ', 'ωρομισθι', 'διαθεση', 'τοποθετησ', 'εγκριση μεταβασησ', 'γενομενησ', 'αδεια', 'αδειασ', 'αδειων', 'απαλλαγ',
        ' ορισμοσ', ' ορισμου', 'αντικαταστασ', 'διαγωνιστικ', 'ηλεκτρονικου διαγωνισμου', 'ανοικτου διαγωνισμου',
        'ανοιχτου διαγωνισμου', 'αναθεσ', 'πληρωμ', 'προυπολογισμ', ' πιστωσ', 'παραλαβ', 'παραδοτε', 'πρακτικου', 'πρακτικο ', 'πρακτικα ',
        'εισηγησ', 'διαβιβασ', 'προσφυγ', 'αντιρρησ', 'ενστασ', 'αιτηματοσ θεραπειασ', 'μητρωο', 'μητρωου',
        'χορηγηση μκ', 'μισθολογικ', 'κλιμακι', 'συνταξ', 'πειθαρχικ', 'εκδρομ', 'επισκεψ', 'μετακινηση μαθητων',
        'εγκριση συμμετοχησ', 'εγκριση πραγματοποιησησ', 'συναφειασ', 'προυπηρεσι', 'υποδιευθυντ', 'προισταμεν',
        'υπευθυν', 'στελεχ', 'εκπαιδευτικου προσωπικου', 'εκπαιδευτικου κλ', 'ωραριο', 'σχολικων τροχονομων',
        'καθαριοτητ', 'μισθωσ', 'κτιριολογικ', 'ελεγχων', 'επικυρωσ', 'ενταξη πραξησ', 'τεχνικου δελτιου', 'υποεργ',
        'ισολογισμ', 'απολογισμ', 'τμηματων ενταξησ', 'ολιγομελ', 'λειτουργια τμηματ', 'λειτουργιασ τμηματ', 'συμπτυξ',
        'εγγραφη μαθητ', 'μετεγγραφη μαθητ', 'κατ οικον', 'κατ΄ οικον', 'κατ’ οικον', 'ισοτιμια', 'αντιστοιχια',
        'αποδεικτικ', 'πιστοποιητικου σπουδων', 'ομοσπονδι', 'αθλητικ', 'σωματει', 'μητροπολ', 'εκκλησι', 'ναου',
        'κληρικ', 'εφορειασ', 'σχολικησ εφορειασ', 'εποπτει', 'ιδια μεσα', 'συνδιοργανωσ', 'αιγιδα', 'βρεφονηπιακ',
        'εξεταση αιτημ', 'επιδοματοσ στεγασησ', 'επιδοματοσ σιτισησ']
# Dropped unless a STRONG topic (Πανελλαδικές, υποτροφίες, μετεγγραφές…) is in the subject too.
SOFT = ['τροποποιησ', 'συμπληρωσ', 'ανακλησ', 'ορθη επαναληψη', 'διορθωσ', 'εγκριση', 'εγκρισησ', 'κατανομη',
        'πινακ', 'αποτελεσματ', 'χρηματοδοτησ', 'στεγασ', 'δικαιουχ', 'απολυτηρι', 'διευθυντ', 'εκπαιδευτικων']
# Words that mark an act as addressed to everyone (a rule, a call, a programme), not to one person or school.
GENERAL = ['εγκυκλιοσ', 'οδηγιεσ', 'καθορισμοσ', 'ρυθμιση', 'προκηρυξη', 'προσκληση', 'προγραμμα', 'προγραμματοσ',
           'υλη', 'εξετασ', 'ανακοινωση', 'προθεσμι', 'υποβολη αιτησ', 'ημερομηνι', 'οροι και προυποθεσεισ',
           'διαδικασια', 'κανονισμοσ', 'εφαρμογη', 'εναρξη', 'πλαισιο', 'σχολικο ετοσ', 'ακαδημαικο ετοσ', 'τροποσ']
# Topics that only make sense for one body.
EXTRA = {
    '99219675': [(('δημοσια προσκληση',), 'opportunities'), (('προγραμμα', 'ανεργ'), 'skills'),
                 (('προγραμμα', 'νεων'), 'opportunities'), (('επιδοτουμεν', 'καταρτισ'), 'skills'),
                 (('προεργασια',), 'opportunities'), (('ημερ', 'καριερασ'), 'careers'), (('επασ', 'εισαγωγ'), 'skills'),
                 (('επασ', 'ειδικοτητ'), 'skills')],
    '50229': [(('πιστοποιησ', 'εξετασ'), 'opportunities'), (('επαγγελματικ', 'περιγραμμα'), 'careers')],
    '100063848': [(('πιστοποιησ', 'προγραμμ', 'σπουδων'), 'university'), (('εκθεση', 'ποιοτητ'), 'university')],
}
CIRCULAR = ('Α.3', 'Α.1.1', 'Α.1.2')


def classify(subject, type_id, uid=''):
    """(category, reason) to keep an act, (None, reason) to drop it."""
    s = norm(subject)
    if len(s) < 25:
        return None, 'subject too short to judge'
    cat, why = judge(s, soft=SOFT, hard=HARD, extra_topics=EXTRA.get(uid, ()))
    if not cat:
        # A circular or a law of the Ministry is content even without one of our topic words.
        if why == 'no education topic' and type_id in CIRCULAR and uid == MINISTRY and judge(s + ' πανελλαδικ', soft=SOFT, hard=HARD)[0]:
            return 'policy', 'circular of the Ministry'
        return None, why
    if type_id not in CIRCULAR and not hits(s, GENERAL):
        return None, 'not a general act: ' + why
    return cat, why


def search(http, **q):
    return http.json(API + 'search.json?' + urllib.parse.urlencode(q), timeout=120, headers={'Accept': 'application/json'})


def collect_org(run, http, uid, today, explain=False):
    name, types = ORGS[uid]
    key = f'diavgeia_{uid}'
    since = run.cursor(key) or (today - datetime.timedelta(days=FIRST_RUN_DAYS)).isoformat()
    issue_from = (datetime.date.fromisoformat(since) - datetime.timedelta(days=ISSUE_WINDOW_DAYS)).isoformat()
    items, read = [], 0
    for t in types:
        for page in range(MAX_PAGES):
            r = search(http, org=uid, type=t, from_date=since, from_issue_date=issue_from, size=PAGE, page=page, sort='recent')
            for d in r.get('decisions', []):
                read += 1
                subject = squeeze(d.get('subject'))
                cat, why = classify(subject, t, uid)
                if explain and (cat or not why.startswith('no education')):
                    log(f'  {"KEEP" if cat else "drop"} {name} {t}: [{why}] {clip(subject, 160)}')
                if not cat:
                    continue
                ada = d.get('ada') or ''
                day = datetime.datetime.fromtimestamp((d.get('issueDate') or 0) / 1000, datetime.timezone.utc).date().isoformat() if d.get('issueDate') else ''
                items.append(item('Διαύγεια · ' + name, subject, VIEW + urllib.parse.quote(ada), day,
                                  f'{TYPE_LABEL.get(t, t)} · {name} · ΑΔΑ {ada}' + (f' · αρ. πρωτ. {d["protocolNumber"]}' if d.get('protocolNumber') else ''),
                                  cat, ref='diavgeia-' + ada))
            info = r.get('info') or {}
            if (page + 1) * PAGE >= (info.get('total') or 0):
                break
        else:
            run.note(f'Διαύγεια {name} {t}: more than {MAX_PAGES * PAGE} acts since {since}, the rest were not read')
    return items, {key: today.isoformat()}, read


def collect(run, explain=False):
    http = Http(gap=0.6)
    today = datetime.date.today()
    items, cursors, read, failed = [], {}, 0, []
    for uid in ORGS:
        try:
            its, cur, n = collect_org(run, http, uid, today, explain)
        except Exception as e:      # one body failing must not lose the others; its cursor stays where it was
            failed.append(f'{ORGS[uid][0]}: {e}')
            continue
        items += its; cursors.update(cur); read += n
    seen, uniq = set(), []
    for it in items:
        if it['ref'] not in seen:
            seen.add(it['ref']); uniq.append(it)
    run.note(f'Διαύγεια: {read} acts read, {len(uniq)} kept, {http.count} requests' + (f', failed: {"; ".join(failed)}' if failed else ''))
    run.post('diavgeia', uniq, cursors, error='; '.join(failed)[:500] if failed else '')
    if failed and len(failed) == len(ORGS):
        raise RuntimeError('Διαύγεια: no body could be read: ' + failed[0])
    return len(uniq)
