import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { panelLayout } from '../app/panel-layout.js';

const source = readFileSync(new URL('../app/panel.js', import.meta.url), 'utf8')
  .replace("import { panelLayout } from './panel-layout.js';", '');
const nodes = new Map();
const timers = new Map();
const windowEvents = new Map();
const documentEvents = new Map();
let now = 100_000;
let requests = 0;
let failure = null;
let reply = {
  paused: false,
  data: {
    scheduler: { alive: true, pid: 10, uptime: '00:00:59' },
    work_eta: { remaining: 120 },
  },
};

const node = (selector) => {
  if (!nodes.has(selector)) nodes.set(selector, {
    textContent: '',
    dataset: {},
    style: {},
    scrollTop: 0,
    lastElementChild: { textContent: '' },
    classList: { toggle() {} },
    addEventListener() {},
    getBoundingClientRect: () => ({ height: 460 }),
  });
  return nodes.get(selector);
};
const panelWindow = {
  __TAURI_INTERNALS__: {
    async invoke(command) {
      assert.equal(command, 'panel_snapshot');
      requests += 1;
      if (failure) throw failure;
      return reply;
    },
  },
  addEventListener: (name, callback) => windowEvents.set(name, callback),
  setInterval: (callback, ms) => {
    timers.set(ms, callback);
    return ms;
  },
  clearInterval: (id) => timers.delete(id),
};
const panelDocument = {
  body: { dataset: {} },
  visibilityState: 'visible',
  querySelector: node,
  addEventListener: (name, callback) => documentEvents.set(name, callback),
};
vm.runInNewContext(source, {
  window: panelWindow,
  document: panelDocument,
  Date: class extends Date {
    static now() { return now; }
  },
  panelLayout,
}, { filename: 'panel.js' });
const settle = () => new Promise(setImmediate);
const text = (selector) => node(selector).textContent;
const tick = (elapsedMs) => {
  now += elapsedMs;
  timers.get(1000)();
};
const refresh = async () => {
  panelWindow.__qpetRefresh();
  await settle();
};

await settle();
assert.equal(requests, 1);
assert.equal(text('#remaining'), '02:00');
assert.equal(text('#uptime'), '00:00:59');
tick(1000);
assert.equal(text('#remaining'), '01:59');
assert.equal(text('#uptime'), '00:01:00');
tick(3000);
assert.equal(text('#remaining'), '01:56', 'delayed timers catch up to elapsed time');
assert.equal(text('#uptime'), '00:01:03');
assert.equal(requests, 1, 'clock ticks do not poll the backend');
assert.ok(timers.has(5000), 'status polling keeps its five-second interval');

reply = {
  paused: true,
  data: {
    scheduler: { alive: true, pid: 10, uptime: '00:01:04' },
    work_eta: { remaining: 115 },
  },
};
timers.get(5000)();
await settle();
assert.equal(text('#remaining'), '01:55', 'a fresh snapshot recalibrates the clock');
tick(1000);
assert.equal(text('#remaining'), '01:54', 'game activity still elapses while paused');
assert.equal(text('#uptime'), '00:01:05');

panelDocument.visibilityState = 'hidden';
tick(30_000);
assert.equal(text('#remaining'), '01:54');
const beforeShow = requests;
panelDocument.visibilityState = 'visible';
documentEvents.get('visibilitychange')();
assert.equal(text('#remaining'), '01:24', 'showing the panel catches up immediately');
assert.equal(text('#uptime'), '00:01:35');
await settle();
assert.equal(requests, beforeShow + 1, 'showing the panel also requests a fresh snapshot');

reply = {
  data: {
    scheduler: { alive: true, pid: 11, uptime: '00:00:01' },
    work_eta: { remaining: 1 },
  },
};
await refresh();
assert.equal(text('#uptime'), '00:00:01', 'a new process does not inherit the old clock');
tick(2000);
assert.equal(text('#uptime'), '00:00:03');
assert.equal(text('#remaining'), '', 'expired countdowns do not become negative');

for (const [raw, expected] of [
  ['59:59', '01:00:00'],
  ['23:59:59', '24:00:00'],
  ['1-02:03:04', '26:03:05'],
  ['unknown', 'unknown'],
  ['00:61:00', '00:61:00'],
]) {
  reply = { data: { scheduler: { alive: true, uptime: raw } } };
  await refresh();
  tick(1000);
  assert.equal(text('#uptime'), expected, `uptime format: ${raw}`);
}

reply = {
  data: {
    scheduler: { alive: false, uptime: '00:10:00' },
    work_eta: { remaining: 60 },
  },
};
await refresh();
tick(1000);
assert.equal(text('#uptime'), '—');
assert.equal(text('#remaining'), '', 'stopped schedulers do not display stale activity');

reply = { data: { scheduler: { alive: true, uptime: '00:10:00' } } };
await refresh();
failure = new Error('offline');
await refresh();
tick(10_000);
assert.equal(text('#uptime'), '—', 'offline clock ticks cannot revive stale values');
assert.equal(text('#remaining'), '');

windowEvents.get('beforeunload')();
assert.equal(timers.size, 0, 'both intervals are cleaned up');
console.log('panel clock tests: ok');
