"""Standard Bloom filter (educational, stdlib only).

Deterministic hashing via BLAKE2b (never Python's randomized hash()).
A positive query means "possibly present", never proof of existence.
No deletion inside one generation: bits persist until reset; the optional
two-generation rotating mode retains the old filter for queries and starts a
new filter for inserts so the structure can "forget" high-risk identifiers.
"""
import hashlib
import math


def identifier_positions(key, size, num_hashes):
    """Normalise one identifier and return its shared key plus k bit positions.

    Inserts, scoring queries and UI checks all call this function, so they use
    the same whitespace normalisation, BLAKE2b keys and filter parameters.
    """
    normalized = str(key).strip()
    data = normalized.encode('utf-8')
    out = []
    for i in range(num_hashes):
        h = hashlib.blake2b(data, digest_size=8, key=f'bloom-k{i}'.encode('utf-8'))
        out.append(int.from_bytes(h.digest(), 'big') % size)
    return normalized, out


class BloomFilter:
    def __init__(self, size=512, num_hashes=3):
        if not isinstance(size, int) or size < 16:
            raise ValueError('Bloom size must be an int >= 16.')
        if not isinstance(num_hashes, int) or not 1 <= num_hashes <= 10:
            raise ValueError('num_hashes must be an int in 1..10.')
        self.size = size
        self.num_hashes = num_hashes
        self.reset()

    @classmethod
    def for_capacity(cls, n, target_fpp):
        """Size a filter for `n` expected inserts at `target_fpp`.

        m = ceil(-n ln(p) / (ln 2)^2), k = round((m/n) ln 2), k >= 1.
        """
        if isinstance(n, bool) or not isinstance(n, (int, float)) or n <= 0:
            raise ValueError('n must be a positive number.')
        if isinstance(target_fpp, bool) or not isinstance(target_fpp, (int, float)) or not 0 < target_fpp < 1:
            raise ValueError('target_fpp must be strictly between 0 and 1.')
        m = math.ceil(-float(n) * math.log(target_fpp) / (math.log(2) ** 2))
        m = max(16, m)
        k = max(1, min(10, round((m / float(n)) * math.log(2))))
        return cls(size=m, num_hashes=k)

    def reset(self):
        self.bits = [0] * self.size
        self.insertions = 0
        self.queries = 0
        self.positives = 0
        # Demo-only ground truth: identifier -> first event metadata. This is
        # never used for scoring and never reads labels.
        self._inserted = {}
        # Demo-only ground truth: bit position -> identifiers that set it.
        self._bit_sources = {}
        self._absent_queries = 0
        self._false_positives = 0

    def positions(self, key):
        # Keyed hashing: BLAKE2b `key=` parameter with a per-function domain
        # separation string. Each of the k hash functions uses a distinct key,
        # so positions are deterministic yet independent across functions.
        return identifier_positions(key, self.size, self.num_hashes)[1]

    def add(self, key, event_id=None, action=None, score=None):
        key, pos = identifier_positions(key, self.size, self.num_hashes)
        for p in pos:
            self.bits[p] = 1
            sources = self._bit_sources.setdefault(p, [])
            if key not in sources:
                sources.append(key)
        self.insertions += 1
        record = self._inserted.get(key)
        if record is None:
            self._inserted[key] = {'event_id': event_id, 'action': action, 'score': score}
        elif record.get('event_id') is None and event_id is not None:
            record.update(event_id=event_id, action=action, score=score)

    def _probe(self, key):
        key, pos = identifier_positions(key, self.size, self.num_hashes)
        hit = all(self.bits[p] for p in pos)
        # A Bloom filter has no false negatives for retained insertions.
        assert hit or key not in self._inserted, \
            f'Bloom false negative for inserted identifier {key!r}'
        return key, pos, hit

    def _record_query(self, key, hit):
        self.queries += 1
        if hit:
            self.positives += 1
        if key not in self._inserted:
            self._absent_queries += 1
            if hit:
                self._false_positives += 1

    def _base_result(self, key, pos):
        return {'key': key, 'positions': pos, 'inserted_event': None, 'bit_sources': []}

    def classify(self, key, count_query=False):
        """Return absent, true positive, or false positive with demo evidence."""
        key, pos, hit = self._probe(key)
        if count_query:
            self._record_query(key, hit)
        if not hit:
            return {**self._base_result(key, pos),
                    'result': 'definitely_absent', 'outcome': 'definitely_absent'}
        record = self._inserted.get(key)
        if record is not None:
            return {**self._base_result(key, pos),
                    'result': 'possibly_present', 'outcome': 'true_positive',
                    'inserted_event': dict(record)}
        return {**self._base_result(key, pos),
                'result': 'possibly_present', 'outcome': 'false_positive',
                'bit_sources': [{'position': p,
                                 'identifiers': list(self._bit_sources.get(p, []))}
                                for p in pos]}

    def check(self, key, count_query=True):
        """Return 'possibly_present' or 'definitely_absent'. Query increments stats."""
        key, pos, hit = self._probe(key)
        if count_query:
            self._record_query(key, hit)
        return 'possibly_present' if hit else 'definitely_absent'

    def contains_bits(self, key):
        return all(self.bits[p] for p in self.positions(key))

    @property
    def bits_set(self):
        return sum(self.bits)

    @property
    def occupancy(self):
        return round(self.bits_set / self.size, 4) if self.size else 0.0

    @property
    def measured_fpp(self):
        """Observed positives among queries for keys never inserted."""
        if not self._absent_queries:
            return None
        return round(self._false_positives / self._absent_queries, 4)

    def theoretical_fpp(self, n=None):
        """Standard formula: (1 - e^(-k*n/m))^k using insertion count."""
        n = self.insertions if n is None else n
        if n <= 0:
            return 0.0
        try:
            p = (1.0 - math.exp(-self.num_hashes * n / self.size)) ** self.num_hashes
        except OverflowError:
            p = 1.0
        return round(min(1.0, max(0.0, p)), 4)

    def exact_members(self):
        return set(self._inserted)

    def to_state(self, max_bits=4096):
        bits = self.bits[:max_bits]
        return {
            'size': self.size,
            'm': self.size,
            'num_hashes': self.num_hashes,
            'k': self.num_hashes,
            'bits_set': self.bits_set,
            'occupancy': self.occupancy,
            'insertions': self.insertions,
            'queries': self.queries,
            'positives': self.positives,
            'false_positives': self._false_positives,
            'absent_queries': self._absent_queries,
            'measured_fpp': self.measured_fpp,
            'theoretical_fpp': self.theoretical_fpp(),
            'bits': bits,
            'bits_truncated': len(self.bits) > max_bits,
            'rotating': False,
        }


class RotatingBloomFilter:
    """Two-generation Bloom filter: previous (queries only) + active (inserts).

    Every `rotate_every` inserts the active filter becomes the previous filter
    and a fresh active filter is created. Queries check both generations. This
    bounds how long an identifier can keep producing positives (the structure
    can "forget"), at the cost of a little extra memory and a longer positive
    tail right after rotation.
    """

    def __init__(self, size=512, num_hashes=3, rotate_every=1000):
        if not isinstance(rotate_every, int) or rotate_every < 1:
            raise ValueError('rotate_every must be a positive int.')
        self.size = size
        self.num_hashes = num_hashes
        self.rotate_every = rotate_every
        self.reset()

    def reset(self):
        self.active = BloomFilter(self.size, self.num_hashes)
        self.previous = None
        self.generation = 0
        self._since_rotate = 0

    @property
    def insertions(self):
        return (self.active.insertions + (self.previous.insertions if self.previous else 0))

    @property
    def bits_set(self):
        return self.active.bits_set + (self.previous.bits_set if self.previous else 0)

    @property
    def queries(self):
        return self.active.queries + (self.previous.queries if self.previous else 0)

    @property
    def positives(self):
        return self.active.positives + (self.previous.positives if self.previous else 0)

    def positions(self, key):
        return self.active.positions(key)

    def add(self, key, event_id=None, action=None, score=None):
        self.active.add(key, event_id=event_id, action=action, score=score)
        self._since_rotate += 1
        if self._since_rotate >= self.rotate_every:
            self.rotate()

    def rotate(self):
        self.previous = self.active
        self.active = BloomFilter(self.size, self.num_hashes)
        self.generation += 1
        self._since_rotate = 0

    def check(self, key, count_query=True):
        active = self.active.check(key, count_query=count_query)
        if active == 'possibly_present':
            return active
        if self.previous is not None:
            return self.previous.check(key, count_query=count_query)
        return active

    def classify(self, key, count_query=False):
        result = self.active.classify(key, count_query=count_query)
        if result['outcome'] != 'definitely_absent' or self.previous is None:
            return result
        return self.previous.classify(key, count_query=count_query)

    def theoretical_fpp(self, n=None):
        total = self.active.theoretical_fpp(n)
        if self.previous:
            total = 1 - (1 - total) * (1 - self.previous.theoretical_fpp())
        return round(total, 4)

    @property
    def measured_fpp(self):
        absent = self.active._absent_queries + (self.previous._absent_queries if self.previous else 0)
        fp = self.active._false_positives + (self.previous._false_positives if self.previous else 0)
        return round(fp / absent, 4) if absent else None

    @property
    def occupancy(self):
        total_bits = self.size * (2 if self.previous else 1)
        return round(self.bits_set / total_bits, 4) if total_bits else 0.0

    def exact_members(self):
        members = set(self.active.exact_members())
        if self.previous:
            members |= self.previous.exact_members()
        return members

    def to_state(self, max_bits=4096):
        state = self.active.to_state(max_bits=max_bits)
        state.update({
            'rotating': True,
            'rotate_every': self.rotate_every,
            'generation': self.generation,
            'active_insertions': self.active.insertions,
            'previous_insertions': self.previous.insertions if self.previous else 0,
            'previous_size': self.size if self.previous else 0,
            'insertions': self.insertions,
            'bits_set': self.bits_set,
            'occupancy': self.occupancy,
            'theoretical_fpp': self.theoretical_fpp(),
            'measured_fpp': self.measured_fpp,
        })
        return state


def find_false_positive(size=64, num_hashes=3, max_items=20000):
    """Insert deterministic items until an unseen key reports possibly_present.

    Returns the colliding bit positions for the inserted items and the unseen
    key. This is the classroom "false positive" demonstration, not fraud data.
    """
    bf = BloomFilter(size=size, num_hashes=num_hashes)
    inserted = []
    for i in range(max_items):
        key = f'device-{i}'
        bf.add(key)
        inserted.append(key)
        probe = f'unseen-device-{i}'
        if bf.check(probe, count_query=False) == 'possibly_present':
            # Show the inserted keys whose bits cover the probe's positions.
            needed = set(bf.positions(probe))
            collisions = []
            for candidate in inserted:
                pos = set(bf.positions(candidate))
                if pos & needed:
                    collisions.append({'key': candidate, 'positions': sorted(pos)})
            return {
                'found': True,
                'size': size,
                'num_hashes': num_hashes,
                'insertions': len(inserted),
                'probe': probe,
                'probe_positions': sorted(needed),
                'collisions': collisions[:12],
                'bits_set': bf.bits_set,
                'theoretical_fpp': bf.theoretical_fpp(),
            }
    return {
        'found': False,
        'size': size,
        'num_hashes': num_hashes,
        'insertions': len(inserted),
        'probe': f'unseen-device-{max_items}',
        'probe_positions': [],
        'collisions': [],
        'bits_set': bf.bits_set,
        'theoretical_fpp': bf.theoretical_fpp(),
    }
