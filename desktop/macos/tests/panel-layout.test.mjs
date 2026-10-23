import assert from 'node:assert/strict';
import { panelLayout } from '../app/panel-layout.js';

const layout = (overrides = {}) => panelLayout({
  anchor: { x: 500, y: 0, scale: 1 },
  viewportWidth: 1024,
  viewportHeight: 768,
  contentHeight: 460,
  ...overrides,
});

assert.deepEqual(layout(), {
  width: 392,
  maxHeight: 748,
  left: 304,
  top: 10,
});
assert.deepEqual(
  layout({ anchor: { x: 1000, y: 0, scale: 2 } }),
  layout(),
);
assert.equal(layout({ anchor: { x: 0, y: 0, scale: 2 } }).left, 10);
assert.equal(layout({ anchor: { x: 2048, y: 0, scale: 2 } }).left, 622);
assert.equal(layout({ anchor: { x: 1000, y: 1500, scale: 2 } }).top, 298);
assert.deepEqual(layout({
  viewportWidth: 320,
  viewportHeight: 260,
  anchor: { x: 640, y: 0, scale: 2 },
}), {
  width: 300,
  maxHeight: 240,
  left: 10,
  top: 10,
});
assert.equal(layout({ contentHeight: 900 }).top, 10);
assert.equal(layout({ anchor: { x: 500, y: 0, scale: 0 } }).left, 304);

console.log('panel layout tests: ok');
