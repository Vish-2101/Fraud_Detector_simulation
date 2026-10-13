"""Classical DGIM algorithm for approximate counting of 1s in last N stream elements.

Educational implementation (stdlib only):
- buckets: list of {'pos': int stream position, 'size': power of two}, oldest first.
- invariant: at most `max_buckets` buckets of each size.
- expiry: buckets with pos <= (current_pos - window_size) are outside the window.
- estimate: sum(sizes) - oldest_size // 2  (boundary-bucket approximation).
- query() is pure: it works on an expired *copy* and never mutates buckets.
- exact history (deque of last N bits) is kept ONLY as a classroom reference.
"""
from collections import deque


class DGIM:
    def __init__(self, window_size=60, max_buckets=2):
        if not isinstance(window_size, int) or window_size < 1:
            raise ValueError('window_size must be a positive int.')
        if not isinstance(max_buckets, int) or max_buckets < 2:
            raise ValueError('max_buckets must be an int >= 2.')
        self.window_size = window_size
        self.max_buckets = max_buckets
        self.reset()

    def reset(self):
        self.pos = 0  # stream position; incremented per update
        self.buckets = []  # oldest first
        self.history = deque(maxlen=self.window_size)

    def _merge(self):
        # Enforce at most max_buckets per size, merging oldest pair first.
        # Merge: two buckets of size s with newer timestamp -> one bucket size 2s.
        changed = True
        while changed:
            changed = False
            sizes = {}
            for b in self.buckets:
                sizes.setdefault(b['size'], []).append(b)
            for size in sorted(sizes):
                group = [b for b in self.buckets if b['size'] == size]
                while len(group) > self.max_buckets:
                    # merge the two OLDEST of this size (front of list)
                    idx = [i for i, b in enumerate(self.buckets) if b['size'] == size][:2]
                    i1, i2 = idx[0], idx[1]
                    newer_pos = max(self.buckets[i1]['pos'], self.buckets[i2]['pos'])
                    # remove both (higher index first)
                    for i in sorted([i1, i2], reverse=True):
                        del self.buckets[i]
                    self.buckets.append({'pos': newer_pos, 'size': size * 2})
                    self.buckets.sort(key=lambda b: b['pos'])
                    changed = True
                    break
                if changed:
                    break

    def update(self, bit):
        bit = 1 if bit else 0
        self.pos += 1
        self.history.append(bit)
        if bit == 1:
            self.buckets.append({'pos': self.pos, 'size': 1})
            self._merge()
        self._expire_mutating()
        return self.estimate()

    def _expire_mutating(self):
        cutoff = self.pos - self.window_size
        self.buckets = [b for b in self.buckets if b['pos'] > cutoff]

    def _expired_copy(self, at_pos=None):
        at_pos = self.pos if at_pos is None else at_pos
        cutoff = at_pos - self.window_size
        return [b for b in self.buckets if b['pos'] > cutoff]

    def estimate(self, at_pos=None):
        """Pure query: does not mutate buckets."""
        alive = self._expired_copy(at_pos)
        if not alive:
            return 0
        total = sum(b['size'] for b in alive)
        oldest = min(alive, key=lambda b: b['pos'])
        return total - oldest['size'] // 2

    def exact_count(self):
        return sum(self.history)

    def bucket_summary(self):
        return [{'pos': b['pos'], 'size': b['size']} for b in sorted(self.buckets, key=lambda b: b['pos'])]

    def check_invariants(self):
        from collections import Counter
        c = Counter(b['size'] for b in self.buckets)
        powers = all(s > 0 and (s & (s - 1)) == 0 for s in c)
        merged = all(v <= self.max_buckets for v in c.values())
        ordered = all(self.buckets[i]['pos'] <= self.buckets[i + 1]['pos'] for i in range(len(self.buckets) - 1))
        cutoff = self.pos - self.window_size
        fresh = all(b['pos'] > cutoff for b in self.buckets)
        return {'powers_of_two': powers, 'max_per_size_ok': merged,
                'ordered': ordered, 'no_expired': fresh}

    def snapshot(self):
        return dict(self.buckets)

    def to_state(self):
        inv = self.check_invariants()
        return {
            'window_size': self.window_size,
            'max_buckets': self.max_buckets,
            'stream_pos': self.pos,
            'estimate': self.estimate(),
            'exact': self.exact_count(),
            'buckets': self.bucket_summary()[-12:],
            'bucket_count': len(self.buckets),
            'invariants': inv,
        }


def long_stream_demo(n=1000, window=100, density=0.3, seed=42):
    """Deterministic long-stream demo (§7.2): run n bits through DGIM and
    sample estimate vs exact count plus bucket count over time.

    Shows the memory/accuracy trade-off: bucket count stays O(log n) while a
    naive exact window stores all `window` bits, and relative error stays
    within the classical ~50% bound. Returns {'samples': [...], 'summary'}.
    Samples every max(1, n//50) steps to keep the payload small.
    """
    import random as _random
    rng = _random.Random(seed)
    d = DGIM(window_size=window)
    exact_window = deque(maxlen=window)
    samples = []
    step = max(1, n // 50)
    max_rel_err, err_sum, err_n = 0.0, 0.0, 0
    max_buckets = 0
    for i in range(1, n + 1):
        bit = 1 if rng.random() < density else 0
        d.update(bit)
        exact_window.append(bit)
        max_buckets = max(max_buckets, len(d.buckets))
        if i % step == 0 or i == n:
            exact = sum(exact_window)
            est = d.estimate()
            rel = abs(est - exact) / exact if exact else 0.0
            max_rel_err = max(max_rel_err, rel)
            err_sum += rel
            err_n += 1
            samples.append({'pos': i, 'exact': exact, 'estimate': est,
                            'rel_error': round(rel, 4), 'buckets': len(d.buckets)})
    return {'samples': samples,
            'summary': {'n': n, 'window': window, 'density': density,
                        'max_rel_error': round(max_rel_err, 4),
                        'avg_rel_error': round(err_sum / max(1, err_n), 4),
                        'max_buckets': max_buckets,
                        'exact_storage': window,
                        'note': 'Buckets stay O(log n); exact window stores `window` bits.'}}
