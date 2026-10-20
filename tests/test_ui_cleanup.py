import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RemovedUiTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        self.js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')

    def test_sound_toggle_and_beeps_are_gone(self):
        self.assertNotIn('id="sound"', self.html)
        self.assertNotIn('soundOn', self.js)
        self.assertNotIn('function beep', self.js)

    def test_shadow_view_selector_is_gone(self):
        self.assertNotIn('id="shadow-view"', self.html)
        self.assertNotIn('shadow-view', self.js)

    def test_advantages_card_and_hero_subtitle_are_gone(self):
        self.assertNotIn('Advantages / Limitations', self.html)
        self.assertNotIn('Event-time windows', self.html)

    def test_surviving_controls_still_present(self):
        for ident in ('theme', 'guided', 'run-scenario', 'events', 'total'):
            self.assertIn(f'id="{ident}"', self.html)


class SurvivingWiringTests(unittest.TestCase):
    def test_event_view_defaults_to_live_lens(self):
        js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("function eventView(e) {", js)
        self.assertIn("mode: 'full'", js)
        self.assertNotIn("mode: 'baseline'", js)


if __name__ == '__main__':
    unittest.main()
