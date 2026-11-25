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
        feed = ('<rss><item><title>gta 6</title><ht:approx_traffic>5,000+</ht:approx_traffic>'
                '<ht:news_item><ht:news_item_title>GTA VI: ρεκόρ πωλήσεων</ht:news_item_title></ht:news_item></item></rss>')
        self.assertEqual(t.google_items(feed), [('gta 6', 5000, 'GTA VI: ρεκόρ πωλήσεων')])

    def test_x(self):
        page = '<a class="string" href="/greece/trend/%23TheVoiceGR/">#TheVoiceGR</a><a href="/greece/trend/Oscar/">Oscar</a><a href="/greece/trend/Oscar/">Oscar</a>'
        self.assertEqual(t.x_terms(page), ['#TheVoiceGR', 'Oscar'])
        self.assertTrue(t.X_SKIP.search(t.key_of('Καλή Κυριακή')))
        self.assertTrue(t.X_SKIP.search(t.key_of('21 Νοεμβρίου')))
        self.assertFalse(t.X_SKIP.search(t.key_of('#TheVoiceGR')))

    def test_wiki_from_greece_keeps_articles_only(self):
        p = {"items": [{"articles": [{"article": "File:Sign.svg", "project": "en.wikipedia", "views_ceil": 9000, "rank": 1},
                                     {"article": "Fall_(2022_film)", "project": "en.wikipedia", "views_ceil": 1900, "rank": 2}]}]}
        self.assertEqual(t.wiki_gr_day(p), {('en.wikipedia', 'Fall_(2022_film)'): 1900})

    def test_recent_days_stops_when_it_has_enough_and_skips_a_refused_day(self):
        asked = []

        def fetch(back, have={1: 'a', 2: 'b', 3: 'c', 4: 'd'}):
            asked.append(back)
            return have.get(back)
        self.assertEqual(t.recent_days(fetch, 3), ['a', 'b', 'c'])
        self.assertEqual(asked, [1, 2, 3])                       # the fourth day is never requested
        asked.clear()
        self.assertEqual(t.recent_days(lambda b: None if b == 1 else fetch(b), 3), ['b', 'c', 'd'])
        self.assertEqual(t.recent_days(lambda b: None if b in (2, 3) else fetch(b), 3), ['a', 'd'])

    def test_retry_after(self):
        from .common import retry_after
        self.assertEqual(retry_after('7'), 7)
        self.assertEqual(retry_after('600'), 60)
        self.assertEqual(retry_after(None), 20)
        self.assertEqual(retry_after('Wed, 21 Oct 2026 07:28:00 GMT'), 20)

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
        g = t.merge([{'name': 'Grand Theft Auto', 'url': 'u1', 'src': 'wikipedia', 'signal': 'a', 'strength': 20},
                     {'name': 'Grand Theft Auto VI', 'url': 'u0', 'src': 'wikipedia', 'signal': 'b', 'strength': 25},
                     {'name': 'Grand Theft Auto VI', 'url': 'u2', 'src': 'twitch', 'signal': 'c', 'strength': 29},
                     {'name': 'Fortnite', 'url': 'u3', 'src': 'twitch', 'signal': 'd', 'strength': 25}])
        self.assertEqual(g[0]['name'], 'Grand Theft Auto VI')
        self.assertEqual(g[0]['sources'], {'wikipedia', 'twitch'})     # two articles are still one source
        self.assertEqual(g[1]['name'], 'Fortnite')

    def test_a_single_day_on_google_or_x_only_confirms(self):
        g = t.merge([{'name': 'iphone 18', 'url': 'u', 'src': 'google', 'signal': 'g', 'strength': 12, 'weak': True},
                     {'name': 'the voice', 'url': 'u', 'src': 'x', 'signal': 'x', 'strength': 14, 'weak': True},
                     {'name': 'iPhone 18', 'url': 'w', 'src': 'wiki-gr', 'signal': 'w', 'strength': 22}])
        self.assertEqual([x['name'] for x in g], ['iPhone 18'])
        self.assertEqual(g[0]['sources'], {'wiki-gr', 'google'})

    def test_memory_counts_earlier_days_only(self):
        m, seen = t.remember({'2026-11-20': ['a', 'b'], '2026-11-21': ['a']}, '2026-11-22', ['a', 'c'])
        self.assertEqual(seen, {'a': 2, 'c': 0})
        m, seen = t.remember(m, '2026-11-22', ['a', 'c'])              # a second run on the same day
        self.assertEqual(seen, {'a': 2, 'c': 0})
        self.assertEqual(sorted(m), ['2026-11-20', '2026-11-21', '2026-11-22'])

    def test_pick_limits_wikipedia_only_items(self):
        w = [{'name': str(i), 'sources': {'wikipedia'}} for i in range(9)] + [{'name': 'x', 'sources': {'x'}}]
        self.assertEqual(len([g for g in t.pick(w) if g['sources'] == {'wikipedia'}]), t.MAX_WIKI_ONLY)
        self.assertIn('x', [g['name'] for g in t.pick(w)])


if __name__ == '__main__':
    unittest.main()
