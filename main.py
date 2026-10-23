"""PoC DAW built with NiceGUI: the same features as the Django + Datastar version."""

import json
import uuid
from pathlib import Path

import mutagen
from nicegui import app, events, ui

from plugins import PLUGINS, PRESETS, with_defaults
from store import MEDIA, ROOT, TRACKS, db, span

PPS = 50  # timeline zoom: pixels per second (also in static/engine.js)
TIMELINE_SECONDS = 60

# JS run in the browser on drag events: put something in dataTransfer (Firefox
# needs it) and send the pointer's x offset within the element to the server.
DRAG_X = "(e) => { %s emit(e.clientX - e.currentTarget.getBoundingClientRect().left) }"
DRAGSTART_JS = DRAG_X % "e.dataTransfer.setData('text/plain', 'daw');"
DROP_JS = DRAG_X % "e.preventDefault();"

MEDIA.mkdir(exist_ok=True)
app.add_media_files('/media', MEDIA)
app.add_static_files('/static', ROOT / 'static')


class PageState:
    """Per-browser-tab UI state; the arrangement itself lives in the store."""

    def __init__(self):
        self.playing = False
        self.selected = None
        self.drag = None  # ('clip' | 'region', id)
        self.grab = 0.0  # pointer offset within the dragged region, px


@ui.page('/')
async def index():
    ui.add_head_html('<script src="/static/engine.js"></script>')
    ui.dark_mode().enable()
    state = PageState()
    # Send JS via the client, not ui.run_javascript: handlers often refresh (and so
    # delete) the element that triggered them, which breaks NiceGUI's context lookup.
    client = ui.context.client

    def push_arrangement():
        client.run_javascript(f'daw.setArrangement({json.dumps(db.arrangement())})')

    def arrangement_changed():
        lanes.refresh()
        push_arrangement()

    def select(region_id):
        state.selected = region_id
        lanes.refresh()
        inspector.refresh()

    def toggle_play():
        state.playing = not state.playing
        client.run_javascript('daw.play()' if state.playing else 'daw.stop()')

    def start_drag(kind, item_id, grab):
        state.drag, state.grab = (kind, item_id), grab

    def drop(track, x):
        if not state.drag:
            return
        kind, item_id = state.drag
        state.drag = None
        start = max(0.0, round((x - state.grab) / PPS, 2))
        if kind == 'clip':
            region = db.add_region(item_id, track, start)
        else:
            region = db.region(item_id)
            db.update(item_id, {**region, 'track': track, 'start': start})
        state.selected = region['id']
        arrangement_changed()
        inspector.refresh()

    async def upload(e: events.UploadEventArguments):
        name = Path(e.file.name).name
        path = MEDIA / f'{uuid.uuid4().hex}{Path(name).suffix.lower()}'
        await e.file.save(path)
        try:
            info = mutagen.File(path)
        except mutagen.MutagenError:
            info = None
        if info is None or not getattr(info.info, 'length', 0):
            path.unlink()
            ui.notify(f'Could not read audio from {name}.', type='negative')
            return
        db.add_clip(name, path.name, info.info.length)
        library.refresh()

    ui.on('daw-ended', lambda: setattr(state, 'playing', False))

    with ui.header().classes('items-center gap-2 bg-slate-800'):
        ui.label('PoC DAW · NiceGUI').classes('text-lg font-bold mr-4')
        ui.button(on_click=toggle_play).classes('w-20') \
            .bind_text_from(state, 'playing', backward=lambda playing: 'Stop' if playing else 'Play')
        ui.button('⏮', on_click=lambda: client.run_javascript('daw.seek(0)')).props('flat color=white')
        ui.label('0.00 s').classes('daw-time font-mono')
        ui.label('Drag clips onto tracks · drag the ruler to scrub · click a region to edit') \
            .classes('text-xs opacity-60 ml-auto')

    @ui.refreshable
    def library():
        if not db.clips:
            ui.label('Upload a clip, then drag it onto a track.').classes('text-sm opacity-60')
        for clip in db.clips:
            with ui.element('div').props('draggable=true') \
                    .classes('flex justify-between gap-2 px-2 py-1 rounded cursor-grab hover:bg-white/10') \
                    .mark(f"clip-{clip['id']}") as item:
                item.on('dragstart', lambda c=clip: start_drag('clip', c['id'], 0), js_handler=DRAGSTART_JS)
                ui.label(clip['name']).classes('truncate text-sm')
                ui.badge(f"{clip['duration']:.1f}s").props('color=grey-8')

    def region_box(region):
        selected = 'ring-2 ring-yellow-400' if region['id'] == state.selected else ''
        with ui.element('div').props('draggable=true') \
                .classes(f'absolute top-1 bottom-1 rounded border border-blue-400 bg-blue-500/20 '
                         f'overflow-hidden cursor-grab {selected}') \
                .style(f"left: {region['start'] * PPS}px; width: {span(region) * PPS}px") \
                .mark(f"region-{region['id']}") as box:
            box.on('dragstart', lambda e, r=region: start_drag('region', r['id'], e.args), js_handler=DRAGSTART_JS)
            box.on('click', lambda r=region: select(r['id']))
            clip = db.clip(region['clip'])
            ui.element('daw-wave').classes('text-blue-400') \
                .props(f"src=/media/{clip['file']} offset={region['offset']} length={region['length']}")
            ui.label(clip['name']).classes('relative text-xs px-1 pointer-events-none')

    @ui.refreshable
    def lanes():
        for track in range(TRACKS):
            with ui.element('div').classes('relative h-20 border-t border-gray-700').mark(f'lane-{track}') as lane:
                lane.on('dragover', js_handler='(e) => e.preventDefault()')
                lane.on('drop', lambda e, t=track: drop(t, e.args), js_handler=DROP_JS)
                ui.label(f'Track {track + 1}').classes('absolute left-1 top-1 text-xs opacity-40 pointer-events-none')
                for region in db.regions:
                    if region['track'] == track:
                        region_box(region)

    @ui.refreshable
    def inspector():
        region = db.region(state.selected) if state.selected else None
        if not region:
            ui.label('Select a region to trim, split or add effects.').classes('text-sm opacity-60')
            return
        clip = db.clip(region['clip'])
        values = {**region, 'fx': {name: dict(p) for name, p in with_defaults(region['fx']).items()}}

        def save():
            db.update(region['id'], values)
            arrangement_changed()

        def set_value(key, value):
            values[key] = value
            save()

        def set_fx(name, param, value):
            values['fx'][name][param] = value
            save()

        async def split():
            at = await client.run_javascript('daw.playhead')
            if db.split(region['id'], float(at)):
                arrangement_changed()

        def delete():
            db.delete(region['id'])
            state.selected = None
            arrangement_changed()
            inspector.refresh()

        with ui.row().classes('w-full items-center'):
            ui.label(clip['name']).classes('text-sm font-bold grow')
            ui.button('Split at playhead', on_click=split).props('outline size=sm')
            ui.button('Delete', on_click=delete).props('outline size=sm color=negative')
        ui.toggle({key: p['label'] for key, p in PRESETS.items()}, value=region['preset'],
                  on_change=lambda e: set_value('preset', e.value)).props('size=sm no-caps')
        with ui.grid(columns=5).classes('w-full gap-6'):
            with ui.column().classes('gap-0'):
                ui.label('Region').classes('text-xs font-bold uppercase opacity-60')
                ui.select({t: f'Track {t + 1}' for t in range(TRACKS)}, value=region['track'],
                          on_change=lambda e: set_value('track', e.value)).props('dense').classes('w-full')
                for key, label, lo, hi in [
                    ('start', 'Start', 0, TIMELINE_SECONDS),
                    ('offset', 'Trim in', 0, clip['duration']),
                    ('length', 'Length', 0.05, clip['duration']),
                ]:
                    ui.label(label).classes('text-xs mt-2')
                    # 'change' fires when the slider is released, not on every move.
                    ui.slider(min=lo, max=hi, step=0.01, value=region[key]).props('label').mark(key) \
                        .on('change', lambda e, k=key: set_value(k, e.args))
            for name, plugin in PLUGINS.items():
                with ui.column().classes('gap-0'):
                    ui.switch(plugin['label'], value=values['fx'][name]['on'],
                              on_change=lambda e, n=name: set_fx(n, 'on', e.value)).props('dense')
                    for param, spec in plugin['params'].items():
                        ui.label(param).classes('text-xs mt-2')
                        ui.slider(min=spec['min'], max=spec['max'], step=spec['step'],
                                  value=values['fx'][name][param]).props('label') \
                            .on('change', lambda e, n=name, p=param: set_fx(n, p, e.args))

    with ui.row().classes('w-full no-wrap items-start gap-4'):
        with ui.card().classes('w-64 shrink-0'):
            ui.upload(label='Upload audio', auto_upload=True, on_upload=upload) \
                .props('accept=audio/* flat bordered').classes('w-full')
            ui.label('Clips').classes('text-sm font-bold')
            library()
        with ui.column().classes('grow min-w-0 gap-4'):
            with ui.card().classes('w-full p-0 overflow-x-auto'):
                with ui.element('div').classes('relative').style(f'width: {TIMELINE_SECONDS * PPS}px'):
                    with ui.element('div').classes('daw-ruler relative h-6 cursor-ew-resize select-none bg-gray-800'):
                        for s in range(0, TIMELINE_SECONDS, 5):
                            ui.label(f'{s}s').classes('absolute top-0 text-[10px] pl-1 opacity-70 pointer-events-none') \
                                .style(f'left: {s * PPS}px')
                    lanes()
                    ui.element('div').classes('daw-playhead absolute top-0 bottom-0 w-0.5 bg-red-500 pointer-events-none') \
                        .style('left: 0px')
            with ui.card().classes('w-full'):
                inspector()

    await ui.context.client.connected()
    push_arrangement()


ui.run(title='PoC DAW (NiceGUI)', port=8090, reload=False, show=False)
