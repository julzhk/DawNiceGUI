import asyncio
import io
import re
import math
import struct
import wave

import pytest
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User

import store



@pytest.fixture(autouse=True)
def seeded(tmp_path, monkeypatch):
    """A fresh store containing one 3 s clip placed on track 1."""
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data.json')  # keep the real data.json untouched
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(i * 0.125))) for i in range(22050 * 3)))
    (store.MEDIA / '_test.wav').write_bytes(buf.getvalue())
    store.db.data = {'next_id': 1, 'clips': [], 'regions': []}
    store.db.add_clip('tone.wav', '_test.wav', 3.0)
    store.db.add_region(1, 0, 0)
    yield store.db
    (store.MEDIA / '_test.wav').unlink()


async def settle():
    """Let NiceGUI run refreshes, which it schedules as background tasks."""
    await asyncio.sleep(0.05)


async def test_select_preset_and_delete(user: User, seeded):
    await user.open('/')
    await user.should_see('tone.wav')
    await user.should_see('Select a region')
    user.find(marker='region-2').trigger('click')
    await user.should_see('Split at playhead')
    await user.should_see('Telephone')
    user.find(ui.toggle).elements.pop().set_value('slower')
    assert seeded.region(2)['preset'] == 'slower'
    assert store.span(seeded.region(2)) == pytest.approx(4.0)
    user.find('Delete').click()
    assert seeded.regions == []
    await user.should_see('Select a region')


async def test_drag_clip_then_move_trim_and_split(user: User, seeded):
    user.javascript_rules[re.compile(r'daw\.playhead')] = lambda _: 2.0
    await user.open('/')
    # Drag the library clip onto track 3 at x=100px (2 s at 50 px/s).
    user.find(marker='clip-1').trigger('dragstart', 0)
    user.find(marker='lane-2').trigger('drop', 100)
    await settle()
    new = seeded.regions[-1]
    assert (new['track'], new['start']) == (2, 2.0)
    await user.should_see('Split at playhead')  # the new region is selected
    # Move it: grabbed 25px into the box, dropped at 75px on track 1 -> 1 s.
    user.find(marker=f"region-{new['id']}").trigger('dragstart', 25)
    user.find(marker='lane-0').trigger('drop', 75)
    await settle()
    assert (seeded.region(new['id'])['track'], seeded.region(new['id'])['start']) == (0, 1.0)
    # Trim: release the Length slider at 2 s (Quasar's 'change' event).
    user.find(marker='length').trigger('change', 2.0)
    await settle()
    assert seeded.region(new['id'])['length'] == 2.0
    # Split at the (simulated) playhead of 2 s: 1 s into a region starting at 1 s.
    user.find('Split at playhead').click()
    for _ in range(20):  # the handler awaits the playhead from the browser
        parts = [r for r in seeded.regions if r['track'] == 0 and r['id'] != 2]
        if len(parts) == 2:
            break
        await asyncio.sleep(0.05)
    assert [(r['start'], r['offset'], r['length']) for r in parts] == [(1.0, 0, 1.0), (2.0, 1.0, 1.0)]


async def test_upload(user: User, seeded):
    await user.open('/')
    upload = user.find(ui.upload).elements.pop()
    wav = (store.MEDIA / '_test.wav').read_bytes()
    with user.client:
        await upload.handle_uploads([SmallFileUpload(name='new tone.wav', content_type='audio/wav', _data=wav)])
        await upload.handle_uploads([SmallFileUpload(name='junk.wav', content_type='audio/wav', _data=b'nope')])
    await user.should_see('new tone.wav')
    await user.should_see('Could not read audio from junk.wav.')
    added = seeded.clips[-1]
    assert (added['name'], added['duration']) == ('new tone.wav', 3.0)
    assert len(seeded.clips) == 2  # the junk file was rejected
    (store.MEDIA / added['file']).unlink()
