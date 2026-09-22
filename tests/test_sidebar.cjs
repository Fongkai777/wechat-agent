const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('compact header keeps an accessible icon sync button beside the title', () => {
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  const topbar = html.slice(html.indexOf('class="topbar page-header"'), html.indexOf('class="sync-toolbar"'));
  assert.match(topbar, /id="refreshBtn"/);
  assert.match(topbar, /title="同步最新消息"/);
  assert.match(topbar, /<span>同步最新消息<\/span>/);
  assert.equal((html.match(/id="refreshBtn"/g) || []).length, 1);
  assert.match(html, /aria-valuenow="320"/);
});

function setup({ saved = null, viewport = 1280, blockedStorage = false } = {}) {
  const listeners = new Map();
  const attrs = new Map();
  const classes = new Set();
  const captures = new Set();
  const storage = new Map(saved === null ? [] : [['wechat-agent.chat-sidebar-width', saved]]);
  const windowListeners = new Map();
  let width = 320;
  const shell = {
    getBoundingClientRect: () => ({ width: viewport }),
    style: { setProperty: (_, value) => { width = Number.parseFloat(value); } },
    classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
  };
  const handle = {
    addEventListener: (name, fn) => listeners.set(name, fn),
    setAttribute: (name, value) => attrs.set(name, value),
    setPointerCapture: id => captures.add(id), hasPointerCapture: id => captures.has(id),
    releasePointerCapture: id => captures.delete(id), focus() {},
  };
  const elements = {
    '#rawShell': shell, '#rawSidebar': { getBoundingClientRect: () => ({ width }) },
    '#sidebarResizeHandle': handle,
  };
  const window = {
    innerWidth: viewport,
    addEventListener: (name, fn) => windowListeners.set(name, fn),
    localStorage: {
      getItem(key) { if (blockedStorage) throw new Error('storage blocked'); return storage.get(key) ?? null; },
      setItem(key, value) { if (blockedStorage) throw new Error('storage blocked'); storage.set(key, value); },
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/sidebar.js'), 'utf8'), {
    document: { querySelector: selector => elements[selector] }, window,
  });
  return {
    get width() { return width; }, attrs, classes, captures, storage,
    emit(name, props = {}) {
      const event = { button: 0, pointerId: 1, isPrimary: true, clientX: 320, preventDefault() {}, ...props };
      listeners.get(name)(event);
    },
    resize(value) { viewport = value; window.innerWidth = value; windowListeners.get('resize')(); },
    blur() { windowListeners.get('blur')(); },
  };
}

test('default width and restored preference are constrained', () => {
  assert.equal(setup().width, 320);
  assert.equal(setup({ saved: '510' }).width, 510);
  assert.equal(setup({ saved: 'bad' }).width, 320);
  assert.equal(setup({ saved: '9999' }).width, 640);
  assert.equal(setup({ saved: '510', viewport: 800 }).width, 420);
});

test('drag captures the pointer, resizes continuously, then persists', () => {
  const app = setup();
  app.emit('pointerdown');
  assert.ok(app.captures.has(1));
  assert.ok(app.classes.has('is-resizing'));
  app.emit('pointermove', { clientX: 510 });
  assert.equal(app.width, 510);
  app.emit('pointerup');
  assert.equal(app.storage.get('wechat-agent.chat-sidebar-width'), '510');
  assert.equal(app.attrs.get('aria-valuenow'), '510');
  assert.equal(app.captures.size, 0);
  assert.equal(app.classes.size, 0);
  assert.equal(setup({ saved: app.storage.get('wechat-agent.chat-sidebar-width') }).width, 510);
});

test('drag respects bounds and ignores unrelated pointers', () => {
  const app = setup();
  app.emit('pointerdown');
  app.emit('pointermove', { pointerId: 2, clientX: 700 });
  assert.equal(app.width, 320);
  app.emit('pointermove', { clientX: 2000 });
  assert.equal(app.width, 640);
  app.emit('pointermove', { clientX: -100 });
  assert.equal(app.width, 280);
  app.emit('pointerup', { pointerId: 2 });
  assert.ok(app.classes.has('is-resizing'));
  app.emit('pointercancel');
  assert.equal(app.classes.size, 0);
});

test('lost capture and window blur always finish resizing', () => {
  const app = setup();
  app.emit('pointerdown');
  app.emit('lostpointercapture');
  assert.equal(app.classes.size, 0);
  app.emit('pointerdown');
  app.blur();
  assert.equal(app.classes.size, 0);
  assert.equal(app.captures.size, 0);
});

test('keyboard adjustments and double-click reset work without dragging', () => {
  const app = setup();
  app.emit('keydown', { key: 'ArrowRight' });
  assert.equal(app.width, 330);
  app.emit('keydown', { key: 'ArrowLeft', shiftKey: true });
  assert.equal(app.width, 280);
  app.emit('keydown', { key: 'End' });
  assert.equal(app.width, 640);
  app.emit('keydown', { key: 'Home' });
  assert.equal(app.width, 280);
  app.emit('dblclick');
  assert.equal(app.width, 320);
  assert.equal(app.storage.get('wechat-agent.chat-sidebar-width'), '320');
});

test('viewport resize keeps desktop preference and disables mobile dragging', () => {
  const app = setup({ saved: '600' });
  app.resize(800);
  assert.equal(app.width, 420);
  assert.equal(app.attrs.get('aria-valuemax'), '420');
  app.resize(390);
  app.emit('pointerdown');
  assert.equal(app.captures.size, 0);
  app.emit('keydown', { key: 'End' });
  assert.equal(app.storage.get('wechat-agent.chat-sidebar-width'), '600');
  app.resize(1280);
  assert.equal(app.width, 600);
});

test('unavailable storage does not break drag or keyboard controls', () => {
  const app = setup({ blockedStorage: true });
  app.emit('pointerdown');
  app.emit('pointermove', { clientX: 480 });
  app.emit('pointerup');
  assert.equal(app.width, 480);
  app.emit('keydown', { key: 'Enter' });
  assert.equal(app.width, 320);
});
