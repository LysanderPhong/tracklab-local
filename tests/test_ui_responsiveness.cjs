const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../portal/app.js'), 'utf8');
const html = fs.readFileSync(require.resolve('../portal/index.html'), 'utf8');
const points = [[10, 20], [10.01, 20], [10.01, 20.01], [10, 20.01]];
const idle = () => ({needs_clear: false, operation_pending: false,
  replay: {ready: true, active: false, state: 'idle'}});
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };

function element() {
  return {textContent: '', value: '', checked: false, hidden: false, disabled: false,
    children: [], attributes: {}, classList: {toggle() {}},
    setAttribute(key, value) { this.attributes[key] = value; },
    replaceChildren() { this.children = []; },
    append(...children) { this.children.push(...children); }, focus() {}};
}

async function fixture() {
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, element()]));
  for (const [id, value] of Object.entries({'distance-km': '1', 'route-pace': '8', duration: '20'}))
    elements.get(id).value = value;
  const markers = [], layers = new Set(), timers = [], memory = new Map();
  const map = {setView() { return this; }, on() {}, getZoom() { return 16; }, fitBounds() {},
    addLayer(layer) { layers.add(layer); }, removeLayer(layer) { layers.delete(layer); },
    hasLayer(layer) { return layers.has(layer); }};
  const layer = () => ({addTo(target) { target.addLayer(this); return this; },
    setLatLngs() { return this; }, setStyle() { return this; }, getBounds() { return {}; }});
  const latLng = (latitude, longitude) => ({lat: Array.isArray(latitude) ? latitude[0] : latitude,
    lng: Array.isArray(latitude) ? latitude[1] : longitude,
    distanceTo() { return 1; }, wrap() { return this; }});
  const L = {map: () => map, tileLayer: () => ({...layer(), on() {}}), divIcon: () => ({}),
    polyline: layer, circleMarker: layer, latLng,
    latLngBounds: () => ({getCenter: () => ({lat: 10, lng: 20})}),
    layerGroup() { const children = new Set(); return {...layer(),
      addLayer(item) { children.add(item); }, removeLayer(item) { children.delete(item); }}; },
    marker(point) {
      const item = {...layer(), point: [...point], events: {}, dragging: {enabled: true,
        enable() { this.enabled = true; }, disable() { this.enabled = false; }},
        on(name, handler) { this.events[name] = handler; return this; },
        setLatLng(value) { this.point = [...value]; return this; },
        getLatLng() { return latLng(this.point); }};
      markers.push(item); return item;
    }};
  const calls = [], queued = new Map();
  function defer(path) {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    const pending = {called: false, promise,
      reply(data, status) { resolve(response(data, status)); }};
    if (!queued.has(path)) queued.set(path, []);
    queued.get(path).push(pending); return pending;
  }
  const document = {hidden: false, addEventListener() {}, createElement: element,
    getElementById(id) { assert.ok(elements.has(id), 'missing real UI ID: ' + id); return elements.get(id); }};
  const context = vm.createContext({document, L, console, AbortSignal, performance,
    TrackGeometry: require('../portal/geometry.js'), TrackConnection: require('../portal/connection-state.js'),
    localStorage: {getItem: key => memory.get(key) || null, setItem: (key, value) => memory.set(key, value)},
    setTimeout(handler, delay) { timers.push({handler, delay}); return timers.length; },
    clearTimeout() {}, setInterval() { return 1; }, clearInterval() {},
    async fetch(path) {
      const endpoint = new URL(path, 'http://fixture.test').pathname;
      calls.push(endpoint);
      const next = queued.get(endpoint)?.shift();
      if (next) { next.called = true; return next.promise; }
      if (endpoint === '/api/bootstrap') return response({...idle(), token: 'fixture', version: 'test'});
      if (endpoint === '/api/status') return response(idle());
      if (endpoint === '/api/scan') return response({ready: true, message: 'ready', needs_clear: false});
      throw Error('Unexpected fixture request: ' + endpoint);
    }});
  const run = code => vm.runInContext(code, context);
  run(source); await flush();
  assert.equal(run('busy'), false, 'initial automatic scan must settle');
  return {run, defer, elements, document, markers, layers, timers, calls,
    click: id => elements.get(id).onclick(),
    count: path => calls.filter(item => item === path).length,
    choose(name = 'fixture') { run(`choose(${JSON.stringify({name, address: 'fixture', latitude: 10, longitude: 20})},false)`); },
    route() { run(`setRoute(${JSON.stringify(points)},'fixture')`); }};
}

async function queryAndScanKeepMapUsable() {
  const f = await fixture(); f.choose();
  const query = f.defer('/api/tracks'), queryDone = f.click('find-tracks'); await flush();
  assert.equal(f.elements.get('mode-fixed').disabled, false, 'map query must not lock modes');
  assert.equal(f.elements.get('clear').disabled, false, 'map query must not lock restore');
  f.choose('new query target');
  const info = f.elements.get('route-info').textContent;
  query.reply({tracks: [], message: 'obsolete query'}); await queryDone;
  assert.equal(f.elements.get('route-info').textContent, info, 'late query must not replace new route information');

  const scan = f.defer('/api/scan'), scanDone = f.run("send('scan')"); await flush();
  assert.equal(f.elements.get('mode-fixed').disabled, false, 'USB scan must not lock map editing');
  f.choose('chosen during scan');
  assert.equal(f.run('selected.name'), 'chosen during scan');
  assert.equal(f.markers[0].dragging.enabled, true);
  scan.reply({ready: true, message: 'ready', needs_clear: false}); await scanDone;
}

async function routeHandlesAreReused() {
  const f = await fixture(); f.choose(); const before = f.markers.length;
  f.run("setRoute(Array.from({length:256},(_,i)=>[10+i*.00001,20+(i%2)*.00001]),'fixture')");
  assert.equal(f.markers.length - before, 0, 'display must not create editing handles');
  f.click('edit-route'); assert.equal(f.markers.length - before, 256);
  f.run('routePoints[5]=[10.01,20.01]; routeChanged()');
  assert.equal(f.markers.length - before, 256, 'one changed point must reuse handles');
  f.run("setOperation('fixed')"); assert.equal(f.layers.has(f.run('vertexLayer')), false);
  f.run("setOperation('route')"); f.click('edit-route');
  assert.equal(f.markers.length - before, 256, 'mode switching must reuse handles');
  f.document.hidden = true; assert.ok(f.run('pollDelay()') >= 15000, 'background polling must back off');
}

async function latePresetIsIgnored(fails) {
  const f = await fixture(); f.choose();
  const old = f.defer('/api/route-preset'), done = f.click('preset-route'); await flush();
  f.choose('new target'); f.run("note('new selection notice')");
  old.reply(fails ? {error: 'obsolete preset failure'} : {name: 'old preset', points}, fails ? 503 : 200);
  await done;
  assert.equal(f.run('selected.name'), 'new target', 'late preset must not replace selection');
  assert.equal(f.run('routePoints.length'), 0, 'late preset must not replace route');
  assert.equal(f.elements.get('notice').textContent, 'new selection notice', 'late success/failure must not replace notice');
}

async function latePreviewIsIgnoredAfterModeSwitch() {
  const f = await fixture(); f.route();
  const old = f.defer('/api/preview'), done = f.click('preview-route'); await flush();
  f.run("setOperation('fixed'); note('fixed mode notice')");
  old.reply({lap_metres: 400, distance_km: 1, duration: 480,
    track: points.map(([latitude, longitude]) => ({latitude, longitude}))}); await done;
  assert.equal(f.run('routePreview'), null, 'late preview must not be applied in fixed mode');
  assert.equal(f.run('previewDot'), null, 'late preview must not animate in fixed mode');
  assert.equal(f.elements.get('notice').textContent, 'fixed mode notice');
}

async function fixedStartRejectsOldStatus(fails = false) {
  const f = await fixture(); f.choose(); f.run("setOperation('fixed')");
  const old = f.defer('/api/status'), fresh = f.defer('/api/status'), fixed = f.defer('/api/fixed');
  const count = f.count('/api/status'), oldDone = f.run('pollOnce()'); await flush();
  const startDone = f.run("send('fixed',{latitude:10,longitude:20,seconds:60})"); await flush();
  assert.equal(f.markers[0].dragging.enabled, false, 'starting must disable target marker dragging');
  f.choose('wrong target'); assert.equal(f.run('selected.name'), 'fixture');
  fixed.reply({needs_clear: true, replay: {active: true, mode: 'fixed', state: 'starting'}}); await flush();
  assert.equal(f.run('device.state'), 'starting', 'mutation acknowledgement must be applied immediately');
  old.reply(fails ? {error: 'obsolete status failure'} : idle(), fails ? 503 : 200);
  assert.equal(await oldDone, false, 'status from before mutation must be rejected'); await flush();
  assert.equal(fresh.called, true, 'completed mutation must issue a fresh status request');
  assert.equal(f.count('/api/status'), count + 2);
  assert.equal(f.run('device.state'), 'starting', 'old idle status must not roll back acknowledged start');
  fresh.reply({needs_clear: true, operation_pending: false,
    replay: {active: true, ready: true, mode: 'fixed', state: 'running'}}); await startDone;
  assert.equal(f.run('targetLocked()'), true);
  assert.ok(f.elements.get('notice').textContent.includes('定位已启动'), 'obsolete status failure must not replace acknowledged start notice');
  // A drag which began immediately before locking must snap back on dragend.
  f.markers[0].setLatLng([30, 40]); f.markers[0].events.dragend();
  assert.deepEqual(f.markers[0].point, [10, 20]);
}

async function earlyClearUsesFreshStatusThenScans() {
  const f = await fixture(); f.choose(); f.run("setOperation('fixed')");
  const active = {needs_clear: true, operation_pending: false,
    replay: {active: true, ready: true, mode: 'fixed', state: 'running'}};
  f.run(`applyStatus(${JSON.stringify(active)}); controls()`);
  const old = f.defer('/api/status'), fresh = f.defer('/api/status'), clear = f.defer('/api/clear');
  const scanCount = f.count('/api/scan'), oldDone = f.run('pollOnce()'); await flush();
  const clearDone = f.run("send('clear')"); await flush();
  clear.reply({needs_clear: false, message: 'restored'}); await flush();
  old.reply(active); assert.equal(await oldDone, false); await flush();
  assert.equal(fresh.called, true, 'clear must not reuse old active status');
  fresh.reply({...idle(), replay: {active: false, cleared: true, state: 'stopped'}}); await clearDone;
  assert.equal(f.count('/api/scan'), scanCount + 1, 'successful restore must recheck the device');
  assert.equal(f.elements.get('start').disabled, false, 'second fixed start must be available after early restore');
  assert.equal(f.markers[0].dragging.enabled, true);
}

async function unknownDeviceResultKeepsTargetLocked() {
  const f = await fixture(); f.choose(); f.run("setOperation('fixed')");
  const old = f.defer('/api/status'), fresh = f.defer('/api/status'), fixed = f.defer('/api/fixed');
  const oldDone = f.run('pollOnce()'); await flush();
  const startDone = f.run("send('fixed',{latitude:10,longitude:20,seconds:60})"); await flush();
  fixed.reply({error: 'device result unknown'}, 503); await flush();
  old.reply(idle()); await oldDone; await flush();
  assert.equal(fresh.called, true, 'uncertain device result must request new status');
  fresh.reply({error: 'status unavailable'}, 503); await startDone;
  assert.equal(f.run('operationPending'), true, 'old idle status must not remove uncertainty about device result');
  assert.equal(f.run('targetLocked()'), true, 'unknown device result must keep the target stable');
}

(async () => {
  await queryAndScanKeepMapUsable();
  await routeHandlesAreReused();
  await latePresetIsIgnored(false);
  await latePresetIsIgnored(true);
  await latePreviewIsIgnoredAfterModeSwitch();
  await fixedStartRejectsOldStatus();
  await fixedStartRejectsOldStatus(true);
  await earlyClearUsesFreshStatusThenScans();
  await unknownDeviceResultKeepsTargetLocked();
  console.log('UI responsiveness and asynchronous state regressions passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
