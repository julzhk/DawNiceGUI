"""Effect plugin registry.

Each plugin declares its parameters here (used to render controls and
validate input); the matching Web Audio implementation lives under the same
name in static/engine.js.
"""

PLUGINS = {
    'gain': {
        'label': 'Gain',
        'params': {
            'level': {'min': 0, 'max': 2, 'step': 0.01, 'default': 1},
        },
    },
    'lowpass': {
        'label': 'Low-pass filter',
        'params': {
            'freq': {'min': 100, 'max': 20000, 'step': 10, 'default': 2000},
            'q': {'min': 0.1, 'max': 20, 'step': 0.1, 'default': 1},
        },
    },
    'highpass': {
        'label': 'High-pass filter',
        'params': {
            'freq': {'min': 20, 'max': 5000, 'step': 10, 'default': 200},
            'q': {'min': 0.1, 'max': 20, 'step': 0.1, 'default': 1},
        },
    },
    'delay': {
        'label': 'Echo',
        'params': {
            'time': {'min': 0.05, 'max': 1, 'step': 0.01, 'default': 0.3},
            'feedback': {'min': 0, 'max': 0.9, 'step': 0.01, 'default': 0.4},
            'mix': {'min': 0, 'max': 1, 'step': 0.01, 'default': 0.4},
        },
    },
}

# Filter presets: a playback rate plus a fixed chain of the plugins above.
PRESETS = {
    'none': {'label': 'None'},
    'echo': {'label': 'Echo', 'chain': [('delay', {'time': 0.25, 'feedback': 0.5, 'mix': 0.5})]},
    'slower': {'label': 'Slower', 'rate': 0.75},
    'faster': {'label': 'Faster', 'rate': 1.5},
    'telephone': {'label': 'Telephone', 'chain': [
        ('highpass', {'freq': 400, 'q': 1}),
        ('lowpass', {'freq': 3000, 'q': 1}),
        ('gain', {'level': 1.5}),
    ]},
    'underwater': {'label': 'Underwater', 'chain': [('lowpass', {'freq': 350, 'q': 8})]},
}


def with_defaults(fx):
    """Return a complete, clamped settings dict for every plugin."""
    result = {}
    for name, plugin in PLUGINS.items():
        stored = fx.get(name, {})
        settings = {'on': bool(stored.get('on', False))}
        for param, spec in plugin['params'].items():
            try:
                value = float(stored.get(param, spec['default']))
            except (TypeError, ValueError):
                value = spec['default']
            settings[param] = min(max(value, spec['min']), spec['max'])
        result[name] = settings
    return result
