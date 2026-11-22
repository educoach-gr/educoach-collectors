"""Parsers and merging of the trends collector: python3 -m unittest collectors.test_trends"""
import unittest

from . import trends as t

TWITCH = """<div class="ranked-item-header"></div>
<div class="ranked-item">
  <div class="ri-position hidden-xxs" title="Ranking Position">#1</div>
  <div class="ri-name"><a href="/games/1867279146">Grand Theft Auto VI</a></div>
  <div class="ri-value" title="Value"><div class="to-number-lg">774422</div></div>
  <div class="ri-change hidden-xs" title="Change, %"><span class="diff text-success">
    <i class="fas fa-caret-up"></i>&nbsp;<span class="to-number-lg">101530.2</span>%</span></div>
</div>
<div class="ranked-item">
  <div class="ri-position hidden-xxs" title="Ranking Position">#2</div>
  <div class="ri-name"><a href="/games/509658">Just Chatting</a></div>
  <div class="ri-value" title="Value"><div class="to-number-lg">257050</div></div>
  <div class="ri-change hidden-xs" title="Change, %"><span class="diff text-danger">
    <i class="fas fa-caret-down"></i>&nbsp;<span class="to-number-lg">10.8</span>%</span></div>
</div>"""

SPOTIFY = """<table><tr><th>Pos</th></tr>
<tr><td>1</td><td>+79</td><td><a>Light</a> - <a>ZORO (w/ Bobby Shmurda)</a></td><td>2</td><td>1</td></tr>
<tr><td>2</td><td>-1</td><td><a>LEX</a> - <a>DRAKOS</a></td><td>9</td><td>1</td></tr></table>"""


def day(*names):
    return {"items": [{"articles": [{"article": n, "views": 1, "rank": i + 1} for i, n in enumerate(names)]}]}


class Parsers(unittest.TestCase):
    def test_twitch(self):
        g = t.twitch_games(TWITCH)
        self.assertEqual(g[0][:3], (1, 'Grand Theft Auto VI', '1867279146'))
        self.assertAlmostEqual(g[0][4], 101530.2)
        self.assertLess(g[1][4], 0)

    def test_spotify(self):
        r = t.spotify_rows(SPOTIFY)
        self.assertEqual(r[0], (1, '+79', 'Light', 'ZORO (w/ Bobby Shmurda)', 2))
        self.assertEqual(len(r), 2)

    def test_google(self):
        feed = '<rss><item><title>gta 6</title><ht:approx_traffic>5000+</ht:approx_traffic></item></rss>'
        self.assertEqual(t.google_titles(feed), [('gta 6', '5000+')])

    def test_wiki_rank_skips_namespaces_and_dates(self):
        r = t.wiki_rank(day('Πύλη:Κύρια', 'Ειδικό:Αναζήτηση', '22_Νοεμβρίου', 'Grand_Theft_Auto_VI', '2026'))
        self.assertEqual(r, {'Grand_Theft_Auto_VI': 1})


class Trends(unittest.TestCase):
    def test_wiki_trend_needs_three_days_and_a_rise(self):
        recent = [{'A': 4, 'B': 2, 'C': 3}, {'A': 9, 'C': 4}, {'A': 2, 'B': 50, 'C': 3}, {'A': 12, 'C': 5}]
        base = [{'A': 80, 'C': 6}, {'C': 4}]
        got = dict(t.wiki_trends(recent, base))
        self.assertIn('A', got)        # high for four days, far lower before
        self.assertNotIn('B', got)     # one good day only
        self.assertNotIn('C', got)     # always up there

    def test_same_trend_across_sources(self):
        k = t.key_of
        self.assertTrue(t.same(k('Grand_Theft_Auto_VI'), k('Grand Theft Auto VI')))
        self.assertTrue(t.same(k('Grand Theft Auto'), k('Grand Theft Auto V Enhanced')))
        self.assertFalse(t.same(k('Light'), k('Light: ZORO')))            # too short to stand for another name
        self.assertFalse(t.same(k('Fortnite'), k('Counter-Strike')))

    def test_merge_names_the_trend_after_the_strongest_signal(self):
        g = t.merge([{'name': 'Grand Theft Auto', 'url': 'u1', 'signal': 'Wikipedia (el): x', 'strength': 20},
                     {'name': 'Grand Theft Auto VI', 'url': 'u2', 'signal': 'Twitch (παγκόσμια): y', 'strength': 29},
                     {'name': 'Fortnite', 'url': 'u3', 'signal': 'Twitch (παγκόσμια): z', 'strength': 25}])
        self.assertEqual(g[0]['name'], 'Grand Theft Auto VI')
        self.assertEqual(len(g[0]['sources']), 2)
        self.assertEqual(g[1]['name'], 'Fortnite')


if __name__ == '__main__':
    unittest.main()
