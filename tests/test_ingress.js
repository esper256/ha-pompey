/* Exercise the router/bootstrap contract without downloading browser tooling. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('pompey/rootfs/usr/share/pompey/debug-shim.js', 'utf8');
for (const app of ['Radarr', 'Sonarr']) {
  const base = '/api/hassio_ingress/example-token/debug/' + app.toLowerCase();
  for (const suffix of ['/', '/movie/123', '/series/example']) {
    const calls = [];
    const window = {
      location: {pathname: base + suffix, origin: 'https://ha.example', href: 'https://ha.example' + base + suffix},
      fetch: (url) => { calls.push(url); },
      history: {pushState: (_state, _title, url) => calls.push(url), replaceState: (_state, _title, url) => calls.push(url)},
    };
    const context = {window, document: {}, URL, Element: function () {}};
    context.Element.prototype.setAttribute = function () {};
    vm.runInNewContext(source, context);
    window[app] = {urlBase: ''}; // Inline HTML bootstrap.
    assert.equal(window[app].urlBase, base);
    window.fetch(window[app].urlBase + '/initialize.json');
    window[app] = {urlBase: '', apiKey: 'synthetic'}; // Real app replaces it with initialize.json.
    assert.equal(window[app].urlBase, base);
    assert.equal(window[app].apiKey, 'synthetic');
    window.fetch(window[app].urlBase + '/api/v3/system/status');
    window.history.replaceState({}, '', base + '/');
    assert.deepEqual(calls, [base + '/initialize.json', base + '/api/v3/system/status', base + '/']);
  }
}
