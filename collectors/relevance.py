"""What counts as «about education» for the collectors that read official registers (ΦΕΚ, Διαύγεια).

Everything is matched on common.norm() text: lowercase, no accents, ς → σ. A tuple means «all of these parts appear».
TOPICS gives the category of an item (the first match names it). STRONG are the topics that keep an act even when
its subject also carries a routine word (e.g. «Τροποποίηση … μετεγγραφών»).
"""

import re

TOPICS = [
    (('πανελλαδικ',), 'admission'), (('πανελληνι', 'εξετασ'), 'admission'), (('μηχανογραφικ',), 'admission'),
    (('ελαχιστ', 'βασ', 'εισαγωγ'), 'admission'), (('εισαγωγ', 'τριτοβαθμι'), 'admission'),
    (('εισαγωγ', 'ανωτατ', 'εκπαιδευσ'), 'admission'), (('αριθμ', 'εισακτε'), 'admission'),
    (('ειδικ', 'μαθημα', 'εξετασ'), 'admission'), (('συντελεστ', 'βαρυτητασ'), 'admission'),
    (('κατατακτηρι',), 'university'), (('μετεγγραφ',), 'university'), (('στεγαστικ', 'επιδομα'), 'university'),
    (('φοιτητικ', 'επιδομα'), 'university'), (('σιτισ', 'φοιτητ'), 'university'), (('φοιτητικ', 'εστι'), 'university'),
    (('ιδρυση', 'τμηματοσ', 'πανεπιστημι'), 'university'), (('ξενογλωσσ', 'προγραμμα', 'σπουδων'), 'university'),
    (('μη κρατικ', 'πανεπιστημι'), 'university'), (('νομικα προσωπα πανεπιστημιακησ εκπαιδευσησ',), 'university'),
    (('διδακτρ',), 'university'), (('ανωτατο οριο φοιτησησ',), 'university'), (('διαγραφ', 'φοιτητ'), 'university'),
    (('επαγγελματικα δικαιωματα',), 'careers'), (('επαγγελματικων δικαιωματων',), 'careers'),
    (('προγραμμα σπουδων', 'λυκει'), 'school'), (('προγραμματα σπουδων', 'λυκει'), 'school'),
    (('προγραμμα σπουδων', 'γυμνασι'), 'school'), (('προγραμματα σπουδων', 'γυμνασι'), 'school'),
    (('ωρολογι', 'προγραμμα'), 'school'), (('εθνικο απολυτηριο',), 'school'), (('τραπεζα θεματων',), 'school'),
    (('αξιολογηση', 'μαθητ'), 'school'), (('εξετασ', 'λυκει'), 'school'), (('εξετασ', 'γυμνασι'), 'school'),
    (('εξεταστεα υλη',), 'school'), (('διδακτεα υλη',), 'school'), (('προτυπα', 'σχολει'), 'school'),
    (('πειραματικ', 'σχολει'), 'school'), (('ωνασει', 'σχολει'), 'school'), (('κινητ', 'τηλεφων', 'σχολ'), 'school'),
    (('σχολικ', 'εκφοβισμ'), 'school'), (('ενδοσχολικ', 'βια'), 'school'), (('φοιτηση', 'μαθητ'), 'school'),
    (('απουσι', 'μαθητ'), 'school'), (('διεθνεσ απολυτηριο',), 'school'), (('international baccalaureate',), 'school'),
    (('εγγραφ', 'μαθητ'), 'school'), (('μεταφορα μαθητων',), 'school'),
    (('σχολ', 'ανωτερησ επαγγελματικησ καταρτισησ'), 'skills'),
    (('επαγγελματικ', 'λυκει'), 'skills'), (('μαθητεια',), 'skills'),
    (('επαγγελματικ', 'εκπαιδευσ', 'καταρτισ'), 'skills'), (('προγραμμα', 'καταρτισησ', 'ανεργ'), 'skills'),
    (('δυπα', 'καταρτισ'), 'skills'), (('επιταγ', 'καταρτισησ'), 'skills'),
    (('ψηφιακ', 'δεξιοτητ'), 'skills'), (('δια βιου μαθησ',), 'skills'), (('εθνικο πλαισιο προσοντων',), 'skills'),
    (('υποτροφ',), 'opportunities'), (('ιδρυμα κρατικων υποτροφιων',), 'opportunities'),
    (('βραβει', 'μαθητ'), 'opportunities'), (('βραβει', 'φοιτητ'), 'opportunities'), (('αριστει',), 'opportunities'),
    (('erasmus',), 'opportunities'), (('μαθητικ', 'διαγωνισμ'), 'opportunities'), (('ολυμπιαδ',), 'opportunities'),
    (('κρατικο πιστοποιητικο γλωσσομαθειασ',), 'opportunities'),
    (('πιστοποιησ', 'γλωσσομαθει'), 'opportunities'), (('πιστοποιησ', 'πληροφορικησ'), 'opportunities'),
    (('πιστοποιησ', 'επαγγελματικ', 'καταρτισησ'), 'opportunities'), (('εργασιακ', 'εμπειρι', 'νε'), 'opportunities'),
    (('πρακτικ', 'ασκησ', 'φοιτητ'), 'opportunities'), (('πρακτικ', 'ασκησ', 'σπουδαστ'), 'opportunities'),
    (('ειδικ', 'αγωγ', 'εκπαιδευσ'), 'inclusion'), (('μαθησιακ', 'δυσκολι'), 'inclusion'),
    (('κεδασυ',), 'inclusion'), (('παραλληλ', 'στηριξ'), 'inclusion'), (('μαθητ', 'αναπηρι'), 'inclusion'),
    (('φοιτητ', 'αναπηρι'), 'inclusion'), (('ζωνεσ εκπαιδευτικησ προτεραιοτητασ',), 'inclusion'),
    (('επαγγελματικ', 'προσανατολισμ'), 'careers'), (('συμβουλευτικ', 'προσανατολισμ'), 'careers'),
    (('τεχνητ', 'νοημοσυν', 'εκπαιδευσ'), 'edtech'), (('ψηφιακ', 'φροντιστηρι'), 'edtech'),
    (('ψηφιακ', 'σχολει'), 'edtech'), (('φροντιστηρι',), 'parents'), (('επιδομα', 'μαθητ'), 'parents'),
    (('σχολικ', 'γευματα'), 'parents'), (('υπουργ', 'παιδειασ'), 'policy'),
]

STRONG = [('πανελλαδικ',), ('μηχανογραφικ',), ('εθνικο απολυτηριο',), ('τραπεζα θεματων',), ('αριθμ', 'εισακτε'),
          ('ελαχιστ', 'βασ', 'εισαγωγ'), ('μετεγγραφ',), ('στεγαστικ', 'επιδομα'), ('υποτροφ',), ('κατατακτηρι',),
          ('εξεταστεα υλη',), ('ωρολογι', 'προγραμμα')]


# Topics written without dots: judge() also looks at the text with its dots removed («Σ.Α.Ε.Κ.» → «σαεκ»).
TOPICS += [
    (('σαεκ', 'ειδικοτητ'), 'skills'), (('σαεκ', 'εγγραφ'), 'skills'), (('σαεκ', 'εισαγωγ'), 'skills'),
    (('σαεκ', 'οδηγ', 'καταρτισησ'), 'skills'), (('σαεκ', 'πιστοποιησ'), 'skills'), (('σαεκ', 'επιλογ', 'υποψηφι'), 'skills'),
    (('επαλ', 'ειδικοτητ'), 'skills'), (('επαλ', 'τομε', 'ειδικοτητ'), 'skills'), (('επαλ', 'υλη'), 'skills'),
    (('επαλ', 'πιστοποιησ'), 'skills'), (('μεταλυκειακ', 'ετοσ'), 'skills'),
    (('εξετασ', 'πιστοποιησ'), 'opportunities'), (('κπγ',), 'opportunities'), (('κεδασυ',), 'inclusion'),
    (('ενισχυτικ', 'διδασκαλι'), 'school'), (('ταξ', 'υποδοχησ'), 'inclusion'), (('παιδεια στα μεσα',), 'school'),
    (('σχολικ', 'επαγγελματικ', 'προσανατολισμ'), 'careers'), (('δεξιοτητ', 'μαθητ'), 'school'),
    (('ψηφιακ', 'ζωη', 'μαθητ'), 'school'), (('εργαστηρια δεξιοτητων',), 'school'),
]

# Acts that are never content, whatever topic word they carry: staff, money, committees, procurement, sports fixtures.
HARD = ['υπερωρι', 'προσληψ', 'διορισμ', 'μεταταξ', 'μεταθεσ', 'αποσπασ', 'μετακινησ', 'εφημερι', 'αποζημιωσ',
        'συγκροτησ', 'ανασυγκροτησ', 'ορισμοσ μελ', 'ορισμοσ επιτροπ', 'ορισμοσ υπολογου', 'παραιτησ', 'προαγωγ',
        'μονιμοποιησ', 'εκλογη', 'εξελιξη', 'προμηθει', 'συμβασ', 'αναληψη υποχρεωσ', 'δαπαν', 'κατανομη πιστωσ',
        'επιχορηγησ', ' αγων', 'πρωταθλημα', 'ωρεσ νυχτερινησ', 'νυχτερινησ εργασιασ', 'καθ υπερβαση',
        'λυση υπαλληλικησ', 'λυση τησ υπαλληλικησ', 'συσταση θεσ', 'συσταση προσωποπαγ', 'καταργηση θεσ',
        'μεταφορα θεσ', 'κατανομη θεσ', 'αναθεση καθηκοντων', 'μεταβιβαση αρμοδιοτητ', 'εξουσιοδοτηση υπογραφησ',
        'παραταση θητειασ', 'ληξη θητειασ', 'αποδοχη δωρεασ', 'τιτλου ομοτιμου', 'επιτιμου διδακτορα',
        'αναγορευσ']


def judge(subject_norm, soft=(), hard=(), extra_topics=()):
    """The common decision on a subject: (category, label) to keep, (None, reason) to drop.
    hard/soft are the caller's own routine words on top of HARD; a soft one is forgiven when a STRONG topic is there."""
    # Padded and without quotes or brackets, so a stem that starts with a space matches only at the start of a word
    # (« ορισμοσ» must not catch «καθορισμός»). Then once more without dots, for «Σ.Α.Ε.Κ.» → «σαεκ».
    s = ' ' + re.sub(r'\s+', ' ', re.sub(r'[«»"“”\'’‘()\[\]:;,]', ' ', subject_norm)).strip() + ' '
    s = s + s.replace('.', '')
    found = [(' + '.join(st), cat) for st, cat in list(TOPICS) + list(extra_topics) if all(p in s for p in st)]
    found = [t for t in found if t[0] != 'υπουργ + παιδειασ']
    if not found:
        return None, 'no education topic'
    for w in list(HARD) + list(hard):
        if w in s:
            return None, 'routine act: ' + w.strip()
    if not any(all(p in s for p in st) for st in STRONG):
        for w in soft:
            if w in s:
                return None, 'routine act: ' + w.strip()
    return found[0][1], found[0][0]
