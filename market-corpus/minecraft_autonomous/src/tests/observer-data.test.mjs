import test from 'node:test';
import assert from 'node:assert/strict';
import { safeViewerUrl, observationView, sparkline } from '../static/observer-data.mjs';

test('viewer accepts explicit HTTP(S) pages without credentials or injected parameters', () => {
  assert.equal(safeViewerUrl('http://127.0.0.1:3000/dungeon/'), 'http://127.0.0.1:3000/dungeon/');
  assert.equal(safeViewerUrl('https://viewer.example.org/third/'), 'https://viewer.example.org/third/');
  for (const url of ['', '//example.org/', 'javascript:alert(1)', 'data:text/html,x', 'https://u:p@host/',
    'https://host/?token=x', 'https://host/#token', ' https://host/', 'https://ho\nst/', 'https://host:99999/',
    'http:example.org', 'http:\\example.org']) {
    assert.equal(safeViewerUrl(url), '', url);
  }
});

test('missing or failed reads cannot manufacture a live player', () => {
  assert.equal(observationView({}, 100000).fresh, false);
  assert.equal(observationView({}, 100000).health, null);
  assert.equal(observationView({ runtime: { updated_at: new Date(100000).toISOString(), game_connected: true } }, 200000).fresh, false);
  assert.equal(observationView({ runtime: { updated_at: 'not-a-date' } }).fresh, false);
  assert.equal(observationView({ runtime: { updated_at: new Date(200000).toISOString() } }, 100000).fresh, false);
  assert.equal(observationView({ error: 'unavailable', runtime: { updated_at: new Date(100000).toISOString() } }, 100000).fresh, false);
});

test('zero health/food remain zero, with timestamp and bounded real samples', () => {
  const input = { runtime: { updated_at: new Date(100000).toISOString(), facts: [
    { key: 'body', text: '生命 0/20，饥饿 0/20' }, { key: 'place', text: '森林' },
  ] }, audit: { decisions: Array.from({ length: 30 }, (_, i) => ({ latency_ms: i })) } };
  const actual = observationView(input, 105000);
  assert.equal(actual.health, 0);
  assert.equal(actual.food, 0);
  assert.equal(actual.fresh, true);
  assert.equal(actual.place, '森林');
  assert.equal(actual.decisions.length, 20);
  assert.equal(actual.decisions[0].latency_ms, 10);
});

test('charts contain only finite observed values, not fabricated missing samples', () => {
  assert.equal(sparkline([]), '');
  assert.equal(sparkline([null, undefined, NaN, Infinity, -1, '50']), '');
  assert.match(sparkline([0, 100, 50]), /^0,56 130,0 260,28$/);
});
