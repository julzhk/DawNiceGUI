// Minimal Web Audio engine. The NiceGUI server pushes the arrangement with
// daw.setArrangement() and calls daw.play()/stop()/seek(). Everything that
// changes every frame (playhead line, time readout, scrubbing) is handled
// here in the browser; only "playback ended" is sent back, via emitEvent().
(() => {
  const PPS = 50; // pixels per second, must match main.py
  let ctx = null;
  let session = null;
  let lastScrub = 0;
  let regions = [];
  let playhead = 0;
  const buffers = new Map();

  const audio = () => (ctx ??= new AudioContext());
  const arrangement = () => regions;

  function setPlayhead(t) {
    playhead = t;
    document.querySelectorAll('.daw-playhead').forEach((el) => (el.style.left = `${t * PPS}px`));
    document.querySelectorAll('.daw-time').forEach((el) => (el.textContent = `${t.toFixed(2)} s`));
  }

  // Effect plugins: same names and params as studio/plugins.py.
  // Each returns {input, output} nodes so they can be chained.
  const PLUGINS = {
    gain(c, p) {
      const g = c.createGain();
      g.gain.value = p.level;
      return { input: g, output: g };
    },
    lowpass(c, p) {
      const f = c.createBiquadFilter();
      f.type = 'lowpass';
      f.frequency.value = p.freq;
      f.Q.value = p.q;
      return { input: f, output: f };
    },
    highpass(c, p) {
      const f = c.createBiquadFilter();
      f.type = 'highpass';
      f.frequency.value = p.freq;
      f.Q.value = p.q;
      return { input: f, output: f };
    },
    delay(c, p) {
      const input = c.createGain(), output = c.createGain();
      const delay = c.createDelay(2), feedback = c.createGain(), wet = c.createGain();
      delay.delayTime.value = p.time;
      feedback.gain.value = p.feedback;
      wet.gain.value = p.mix;
      input.connect(output);
      input.connect(delay).connect(feedback).connect(delay);
      delay.connect(wet).connect(output);
      return { input, output };
    },
  };

  function load(url) {
    if (!buffers.has(url)) {
      buffers.set(url, fetch(url).then((r) => r.arrayBuffer()).then((b) => audio().decodeAudioData(b)));
    }
    return buffers.get(url);
  }

  // Connect a list of [plugin, params] in order and return the chain's input node.
  function effectChain(c, chain, destination) {
    let input = destination;
    for (const [name, params] of [...chain].reverse()) {
      if (!PLUGINS[name]) continue;
      const stage = PLUGINS[name](c, params);
      stage.output.connect(input);
      input = stage.input;
    }
    return input;
  }

  function position() {
    return session?.t0 ? session.from + Math.max(0, ctx.currentTime - session.t0) : 0;
  }

  function stop() {
    if (!session) return;
    session.sources?.forEach((s) => { try { s.stop(); } catch {} });
    session.out?.disconnect();
    session = null;
  }

  async function play(from = playhead) {
    stop();
    const token = (session = { from });
    const c = audio();
    await c.resume();
    const regions = arrangement();
    const loaded = await Promise.all(regions.map((r) => load(r.src)));
    if (session !== token) return; // stopped while loading

    const out = c.createGain();
    out.connect(c.destination);
    const t0 = c.currentTime + 0.05;
    const end = Math.max(0, ...regions.map((r) => r.start + r.length / r.rate));
    const sources = [];
    regions.forEach((r, i) => {
      // Timeline times are divided by the playback rate; clip times are not.
      const span = r.length / r.rate;
      const skip = Math.max(0, from - r.start);
      if (skip >= span) return;
      const src = c.createBufferSource();
      src.buffer = loaded[i];
      src.playbackRate.value = r.rate;
      src.connect(effectChain(c, r.chain, out));
      src.start(t0 + Math.max(0, r.start - from), r.offset + skip * r.rate, (span - skip) * r.rate);
      sources.push(src);
    });
    Object.assign(token, { out, sources, t0 });

    const tick = () => {
      if (session !== token) return;
      const t = position();
      setPlayhead(t);
      if (t >= end) {
        stop();
        window.emitEvent?.('daw-ended');
      } else {
        requestAnimationFrame(tick);
      }
    };
    tick();
  }

  // Restart from the current position so edits are heard while playing.
  function refresh() {
    if (session?.t0) play(position());
  }

  // Audible scrubbing: play a short, enveloped grain of every region under t.
  function scrub(t) {
    const now = performance.now();
    if (now - lastScrub < 50) return;
    lastScrub = now;
    const c = audio();
    c.resume();
    for (const r of arrangement()) {
      const pos = t - r.start;
      if (pos < 0 || pos >= r.length / r.rate) continue;
      load(r.src).then((buffer) => {
        const src = c.createBufferSource(), env = c.createGain();
        const at = c.currentTime;
        src.buffer = buffer;
        src.playbackRate.value = r.rate;
        env.gain.setValueAtTime(0, at);
        env.gain.linearRampToValueAtTime(1, at + 0.01);
        env.gain.linearRampToValueAtTime(0, at + 0.08);
        src.connect(env).connect(c.destination);
        src.start(at, r.offset + pos * r.rate, 0.08);
      });
    }
  }

  function seek(t) {
    setPlayhead(t);
    if (session) play(t);
  }

  function setArrangement(list) {
    regions = list;
    refresh();
  }

  // Ruler scrubbing, handled entirely in the browser. Elements are created by
  // NiceGUI after this script runs, so listen on the document.
  let scrubbing = null;
  const rulerTime = (e) => Math.max(0, (e.clientX - scrubbing.getBoundingClientRect().left) / PPS);
  document.addEventListener('pointerdown', (e) => {
    scrubbing = e.target.closest('.daw-ruler');
    if (!scrubbing) return;
    scrubbing.setPointerCapture(e.pointerId);
    scrubbing.resume = !!session;
    stop();
    setPlayhead(rulerTime(e));
    scrub(playhead);
  });
  document.addEventListener('pointermove', (e) => {
    if (!scrubbing) return;
    setPlayhead(rulerTime(e));
    scrub(playhead);
  });
  document.addEventListener('pointerup', () => {
    if (scrubbing?.resume) play(playhead);
    scrubbing = null;
  });

  window.daw = {
    play, stop, seek, scrub, refresh, load, setArrangement,
    get playhead() { return playhead; },
  };

  // <daw-wave src offset length>: draws the visible part of a clip's waveform.
  // Uses shadow DOM so the canvas stays private to the component.
  customElements.define('daw-wave', class extends HTMLElement {
    static observedAttributes = ['src', 'offset', 'length'];

    constructor() {
      super();
      const root = this.attachShadow({ mode: 'open' });
      root.innerHTML = '<style>:host{position:absolute;inset:0;display:block}canvas{width:100%;height:100%}</style><canvas></canvas>';
      this.canvas = root.querySelector('canvas');
      new ResizeObserver(() => this.draw()).observe(this);
    }

    attributeChangedCallback() { this.draw(); }

    async draw() {
      const src = this.getAttribute('src');
      if (!src || !this.isConnected) return;
      const buffer = await load(src);
      const w = (this.canvas.width = this.clientWidth);
      const h = (this.canvas.height = this.clientHeight);
      const g = this.canvas.getContext('2d');
      const data = buffer.getChannelData(0);
      const first = +this.getAttribute('offset') * buffer.sampleRate;
      const perPixel = (+this.getAttribute('length') * buffer.sampleRate) / w;
      const stride = Math.max(1, Math.floor(perPixel / 64));
      g.fillStyle = getComputedStyle(this).color;
      for (let x = 0; x < w; x++) {
        const a = Math.floor(first + x * perPixel);
        const b = Math.min(data.length, Math.floor(a + perPixel));
        let peak = 0;
        for (let i = a; i < b; i += stride) peak = Math.max(peak, Math.abs(data[i]));
        const bar = Math.max(1, peak * h);
        g.fillRect(x, (h - bar) / 2, 1, bar);
      }
    }
  });
})();
