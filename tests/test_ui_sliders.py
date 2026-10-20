import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FULL_RANGE = {
    'speed': (0, 1000),
    'review-range': (0, 100),
    'block-range': (0, 100),
    'dgim-window-range': (1, 10000),
    'dgim-threshold-range': (1, 10000),
    'apriori-support': (0, 100),
    'apriori-confidence': (0, 100),
}


class SliderRangeTests(unittest.TestCase):
    def test_every_range_input_spans_its_full_range(self):
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        inputs = re.findall(r'<input[^>]*type="range"[^>]*>', html)
        found = {}
        for tag in inputs:
            ident = re.search(r'id="([^"]+)"', tag)
            low = re.search(r'min="(-?\d+)"', tag)
            high = re.search(r'max="(-?\d+)"', tag)
            self.assertIsNotNone(ident, tag)
            self.assertIsNotNone(low, tag)
            self.assertIsNotNone(high, tag)
            found[ident.group(1)] = (int(low.group(1)), int(high.group(1)))
        self.assertEqual(set(found), set(FULL_RANGE))
        for ident, expected in FULL_RANGE.items():
            self.assertEqual(found[ident], expected, f'{ident} does not span {expected}')

    def test_threshold_slider_is_no_longer_coupled(self):
        source = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertNotIn('if (review >= block)', source)
        self.assertIn('Math.max(0, Math.round(support * 100))', source)

    def test_range_inputs_do_not_inherit_textbox_inset(self):
        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        rules = re.findall(r'input\[type=range\]\{([^}]*)\}', css)
        self.assertTrue(rules, 'missing input[type=range] rule')
        self.assertTrue(any('padding:0' in body and 'border:0' in body for body in rules),
                        'range inputs still inherit the textbox padding/border inset')


class ThresholdEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        import server
        server.detector.review_threshold, server.detector.block_threshold = 35, 65
        server.detector.risk_cutoffs = {'elevated': 35, 'highest': 65}
        server.detector.apriori_support, server.detector.apriori_confidence = 0.12, 0.0
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, path, body):
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}{path}',
            data=json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())

    def reject(self, path, body):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(path, body)
        return caught.exception.code

    def test_thresholds_accept_both_ends_and_opposite_order(self):
        status, state = self.post('/api/thresholds', {'review': 0, 'block': 100})
        self.assertEqual(status, 200)
        self.assertEqual(state['thresholds'], {'review': 0, 'block': 100})
        status, state = self.post('/api/thresholds', {'review': 100, 'block': 0})
        self.assertEqual(status, 200)
        self.assertEqual(state['thresholds'], {'review': 100, 'block': 0})
        self.assertEqual(self.reject('/api/thresholds', {'review': 101, 'block': 0}), 400)

    def test_apriori_support_accepts_zero(self):
        status, state = self.post('/api/apriori', {'min_support': 0.0})
        self.assertEqual(status, 200)
        self.assertEqual(state['apriori_config']['min_support'], 0.0)
        self.assertEqual(self.reject('/api/apriori', {'min_support': 1.5}), 400)


if __name__ == '__main__':
    unittest.main()
