"""A tiny JSON-file store for clips and regions (the Django version uses models)."""

import json
from pathlib import Path

from plugins import PRESETS, with_defaults

ROOT = Path(__file__).parent
DATA = ROOT / 'data.json'
MEDIA = ROOT / 'media'
TRACKS = 3
MIN_LENGTH = 0.05


def rate(region):
    return PRESETS.get(region['preset'], PRESETS['none']).get('rate', 1)


def span(region):
    """Time the region occupies on the timeline, after any speed change."""
    return region['length'] / rate(region)


def chain(region):
    """Effects to apply, in order: the preset's, then the enabled plugins."""
    preset = PRESETS.get(region['preset'], PRESETS['none']).get('chain', [])
    return [*preset, *((name, p) for name, p in with_defaults(region['fx']).items() if p['on'])]


class Store:
    def __init__(self):
        self.data = json.loads(DATA.read_text()) if DATA.exists() else {'next_id': 1, 'clips': [], 'regions': []}

    def save(self):
        DATA.write_text(json.dumps(self.data, indent=1))

    def _new_id(self):
        self.data['next_id'] += 1
        return self.data['next_id'] - 1

    @property
    def clips(self):
        return self.data['clips']

    @property
    def regions(self):
        return sorted(self.data['regions'], key=lambda r: (r['track'], r['start']))

    def clip(self, clip_id):
        return next(c for c in self.clips if c['id'] == clip_id)

    def region(self, region_id):
        return next((r for r in self.data['regions'] if r['id'] == region_id), None)

    def add_clip(self, name, file, duration):
        self.clips.append({'id': self._new_id(), 'name': name, 'file': file, 'duration': duration})
        self.save()

    def add_region(self, clip_id, track, start, **values):
        region = {
            'id': self._new_id(), 'clip': clip_id, 'track': track, 'start': start, 'offset': 0,
            'length': self.clip(clip_id)['duration'], 'fx': {}, 'preset': 'none', **values,
        }
        self.data['regions'].append(region)
        self.save()
        return region

    def update(self, region_id, values):
        """Apply edited values, clamped the same way as the Django views."""
        region = self.region(region_id)
        duration = self.clip(region['clip'])['duration']
        region['track'] = min(max(int(values['track']), 0), TRACKS - 1)
        region['start'] = max(0.0, float(values['start']))
        region['offset'] = min(max(0.0, float(values['offset'])), duration - MIN_LENGTH)
        region['length'] = min(max(MIN_LENGTH, float(values['length'])), duration - region['offset'])
        region['fx'] = with_defaults(values.get('fx', {}))
        region['preset'] = values['preset'] if values.get('preset') in PRESETS else 'none'
        self.save()

    def split(self, region_id, at):
        """Cut a region in two at timeline position `at`; returns False if `at` is outside it."""
        region = self.region(region_id)
        if not region['start'] + MIN_LENGTH < at < region['start'] + span(region) - MIN_LENGTH:
            return False
        cut = (at - region['start']) * rate(region)  # in clip time
        self.add_region(
            region['clip'], region['track'], at, offset=region['offset'] + cut,
            length=region['length'] - cut, fx=region['fx'], preset=region['preset'],
        )
        region['length'] = cut
        self.save()
        return True

    def delete(self, region_id):
        self.data['regions'] = [r for r in self.data['regions'] if r['id'] != region_id]
        self.save()

    def arrangement(self):
        """What the browser's audio engine needs to play every region."""
        return [{
            'src': f"/media/{self.clip(r['clip'])['file']}",
            'start': r['start'], 'offset': r['offset'], 'length': r['length'],
            'rate': rate(r), 'chain': chain(r),
        } for r in self.regions]


db = Store()  # the single shared store used by the app
