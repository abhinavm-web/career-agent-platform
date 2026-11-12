const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../browser-companion/popup.js'), 'utf8');

test('manifest limits page access to the active tab', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../browser-companion/manifest.json'), 'utf8'));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions.sort(), ['activeTab', 'clipboardWrite', 'scripting']);
  assert.equal(manifest.host_permissions, undefined);
  assert.equal(manifest.content_scripts, undefined);
});

function harness(data, scripts = null) {
  const button = {disabled: false, addEventListener: (_name, fn) => { button.click = fn; }};
  const status = {textContent: ''}; let copied = null, reads = 0;
  const chrome = {tabs: {query: async () => [{id: 42}]}, scripting: {executeScript: async options => { reads++; return [{result: scripts === null ? data : options.func()}]; }}};
  const navigator = {clipboard: {writeText: async value => { copied = value; }}};
  vm.runInNewContext(source, {document: {getElementById: id => id === 'capture' ? button : status,
    title: data.title, querySelectorAll: () => scripts.map(textContent => ({textContent}))},
    location: {href: data.url}, window: {getSelection: () => ({toString: () => data.selection})}, chrome, navigator, URL});
  return {button, status, value: () => copied, reads: () => reads};
}

test('captures only after click and never sends or saves the page', async () => {
  const h = harness({title: 'Fictional role', url: 'https://example.invalid/job', selection: 'Fictional requirement'});
  assert.equal(h.reads(), 0); assert.equal(h.value(), null);
  await h.button.click();
  assert.deepEqual(JSON.parse(h.value()), {format: 'career-agent-job-capture-v1', title: 'Fictional role', url: 'https://example.invalid/job', selection: 'Fictional requirement'});
  assert.match(h.status.textContent, /Copied/); assert.equal(h.button.disabled, false);
});

test('captures only unambiguous structured title and employer hints', async () => {
  const data = {title: 'Page title', url: 'https://example.invalid/job', selection: 'Fictional requirement'};
  const posting = {'@type': 'JobPosting', title: 'Fictional Engineer',
    hiringOrganization: {name: 'Fictional Co', secret: 'DO NOT COPY'}, description: 'DO NOT COPY'};
  const h = harness(data, [JSON.stringify({'@graph': [posting]})]);
  await h.button.click();
  const capture = JSON.parse(h.value());
  assert.deepEqual(capture.metadata, {job_title: 'Fictional Engineer', company: 'Fictional Co'});
  assert.equal(JSON.stringify(capture).includes('DO NOT COPY'), false);
  const ambiguous = harness(data, [JSON.stringify([posting, {...posting, title: 'Other role'}])]);
  await ambiguous.button.click();
  assert.equal(JSON.parse(ambiguous.value()).metadata, undefined);
  const oversized = harness(data, ['x'.repeat(100001), JSON.stringify(posting)]);
  await oversized.button.click();
  assert.equal(JSON.parse(oversized.value()).metadata, undefined);
});

test('rejects empty, oversized and credential-bearing captures', async () => {
  for (const data of [
    {title: 'Role', url: 'https://example.invalid/job', selection: ''},
    {title: 'Role', url: 'https://example.invalid/job', selection: 'x'.repeat(50001)},
    {title: 'Role', url: 'https://user:pass@example.invalid/job', selection: 'Text'},
    {title: 'Role', url: 'file:///private', selection: 'Text'}
  ]) {
    const h = harness(data); await h.button.click(); assert.equal(h.value(), null); assert.ok(h.status.textContent); assert.equal(h.button.disabled, false);
  }
});
