const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { test } = require('node:test');
const { JSDOM } = require('jsdom');

const root = resolve(__dirname, '../..');
const template = readFileSync(resolve(root, 'frontend/templates/pack_inbox.html'), 'utf8');
const script = readFileSync(resolve(root, 'frontend/static/js/pack_inbox.js'), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

async function page(t, overrides = {}) {
    // Load real page markup and templates without executing its Jinja wrappers.
    // No resource loader is enabled: this suite must never contact providers.
    const dom = new JSDOM(template.replace(/\{%[\s\S]*?%\}/g, ''), {
        runScripts: 'outside-only', url: 'http://kapowarr.test/'
    });
    const w = dom.window;
    const state = {
        folder: '/inbox', items: [], jobs: [], subscriptions: [], releases: [],
        confirm: false, calls: [], unexpected: [], timers: [], ...overrides
    };
    t.after(() => { dom.window.close(); assert.deepEqual(state.unexpected, []); });
    w.url_base = '/kapowarr';
    w.usingApiKey = async () => 'fixture-key';
    w.setInterval = (fn, delay) => state.timers.push({ fn, delay });
    w.HTMLElement.prototype.scrollIntoView = () => {};
    w.confirm = () => state.confirm;
    w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    w.HTMLDialogElement.prototype.close = function () { this.open = false; this.onclose?.(); };
    w.fetchAPI = async path => {
        if (path === '/pack-inbox') return { result: { folder: state.folder, items: state.items } };
        if (path === '/pack-downloads') return { result: state.jobs };
        if (path === '/pack-subscriptions') return { result: state };
        state.unexpected.push(path);
        throw Error('Unexpected GET ' + path);
    };
    w.sendAPI = async (method, path, key, params, data) => {
        assert.equal(method, 'POST');
        assert.equal(key, 'fixture-key');
        state.calls.push({ path, data: JSON.parse(JSON.stringify(data)) });
        let result;
        if (state.respond) result = await state.respond(path, data);
        if (result === undefined) {
            state.unexpected.push(path);
            throw Error('Unexpected POST ' + path);
        }
        return { json: async () => ({ result }) };
    };
    w.eval(script);
    await tick();
    return { w, state, el: selector => w.document.querySelector(selector) };
}

const job = (id, status) => ({
    id, status, root: '/inbox', folder: '/inbox/Pack-' + id,
    title: 'Weekly ' + id, message: 'Fixture', received: 100, total: 100
});

test('review filters clear hidden selections and import only visible matches', async t => {
    const { el, state } = await page(t, {
        items: [
            { status: 'matched', relative_path: 'Week/A.cbz', token: 'a' },
            { status: 'matched', relative_path: 'Week/B.cbz', token: 'b' },
            { status: 'imported', relative_path: 'Week/C.cbz', destination: '/library/C.cbz' }
        ],
        respond: path => path === '/pack-inbox/import' ? { folder: '/inbox', items: [] } : undefined
    });
    assert.equal(el('#inbox-results').querySelectorAll('tr:not([hidden])').length, 2);
    el('#inbox-select').click();
    assert.equal(el('#inbox-import').textContent, 'Import Selected Copies (2)');
    el('#inbox-search').value = 'A.cbz';
    el('#inbox-search').oninput();
    assert.equal(el('#inbox-import').textContent, 'Import Selected Copies (1)');
    await el('#inbox-import').onclick();
    assert.deepEqual(state.calls.at(-1), { path: '/pack-inbox/import', data: { items: ['a'] } });
});

test('finished jobs collapse, releases deduplicate and job collapse survives refresh', async t => {
    const { el } = await page(t, {
        jobs: [job('active', 'ready'), job('done', 'finished')],
        releases: [
            { title: '2026.09.09 Weekly', article: 'old', status: 'review', message: '' },
            { title: '2026.09.16 Weekly', article: 'new', status: 'review', message: '' },
            { title: '2026.09.09 Weekly', article: 'old', status: 'review', message: '' }
        ]
    });
    assert.equal(el('#pack-download-jobs').children.length, 1);
    assert.equal(el('#pack-finished-jobs').children.length, 1);
    assert.equal(el('#pack-finished').open, false);
    assert.equal(el('#pack-subscription-releases').children.length, 2);
    assert.match(el('#pack-subscription-releases').firstElementChild.textContent, /^2026.09.16/);
    el('#pack-download-jobs details').open = false;
    await tick();
    await el('#pack-jobs-refresh').onclick();
    assert.equal(el('#pack-download-jobs details').open, false);
});

test('finish requires confirmation and uses the preview token', async t => {
    const { el, state } = await page(t, {
        jobs: [job('id', 'ready')], folder: '/inbox/Pack-id/ready',
        respond: path => {
            if (path === '/pack-downloads/finish-preview') return {
                token: 'confirmation', files: [{ path: 'payload.archive' }], bytes: 100
            };
            if (path === '/pack-downloads/finish') return {};
        }
    });
    assert.equal(el('#pack-download-folder').value, '/inbox');
    await el('.pack-finish').onclick();
    assert.equal(state.calls.length, 1);
    assert.equal(el('.pack-finish').disabled, false);
    state.confirm = true;
    await el('.pack-finish').onclick();
    assert.deepEqual(state.calls.at(-1), {
        path: '/pack-downloads/finish', data: { token: 'confirmation', confirm: true }
    });
});

test('search pagination, subscription creation, pause, weekday and manual check', async t => {
    const { el, state } = await page(t, {
        subscriptions: [{ id: 1, enabled: 1, weekday: 6, query: 'weekly pack',
            link_filter: 'Marvel', service: 'GetComics', automatic: 1, message: '' }],
        respond: path => {
            if (path === '/pack-subscriptions/search') return { articles: [], has_more: true };
            if (['create', 'toggle', 'schedule', 'check'].some(action => path === '/pack-subscriptions/' + action)) return {};
        }
    });
    el('#pack-discovery-form').onsubmit({ preventDefault() {} });
    await tick();
    assert.equal(state.calls.at(-1).data.page, 1);
    el('#pack-query').value = 'changed text';
    await el('#pack-older').onclick();
    assert.deepEqual(state.calls.at(-1).data, { query: 'weekly pack', page: 2 });
    el('#pack-link-filter').value = 'Marvel';
    el('#pack-weekday').value = '4';
    await el('#pack-subscribe').onclick();
    assert.equal(state.calls.at(-1).data.weekday, 4);
    assert.equal(state.calls.at(-1).data.automatic, true);
    await el('.pack-sub-toggle').onclick();
    assert.deepEqual(state.calls.at(-1).data, { id: 1, enabled: false });
    el('.pack-sub-day select').value = '2';
    await el('.pack-sub-save').onclick();
    assert.deepEqual(state.calls.at(-1).data, { id: 1, weekday: 2 });
    await el('#pack-check').onclick();
    assert.equal(state.calls.at(-1).path, '/pack-subscriptions/check');
});

test('provider choices disable unsupported mirrors and submit the selected token', async t => {
    const { el, state } = await page(t, {
        respond: path => {
            if (path === '/pack-downloads/preview') return { title: '<img src=x>', choices: [
                { token: 'http', label: 'Main', service: 'GetComics', supported: true },
                { token: 'mega', label: 'Mega', service: 'Mega', supported: false }
            ] };
            if (path === '/pack-downloads/download') return {};
        }
    });
    el('#pack-article').value = 'https://getcomics.org/weekly/';
    await el('#pack-download-form').onsubmit({ preventDefault() {} });
    const buttons = el('#pack-download-choices').querySelectorAll('button');
    assert.equal(buttons[1].disabled, true);
    assert.equal(el('#pack-download-status img'), null);
    await buttons[0].onclick();
    assert.deepEqual(state.calls.at(-1).data, { token: 'http', folder: '/inbox' });
});


test('series picker binds only the originating file and trusted frame selection', async t => {
    const item = {status: 'review', relative_path: 'Week/Alternate #001.cbz', token: 'file-a', series_query: 'Alternate'};
    const { w, el, state } = await page(t, {
        items: [item],
        respond: path => {
            if (path === '/pack-inbox/match-options') return {title: 'Correct series', issues: [{id: 17, number: 1, owned: false}], selected: [17]};
            if (path === '/pack-inbox/match') return {folder: '/inbox', items: [{...item, status: 'matched'}]};
        }
    });
    el('.inbox-add-series').click();
    assert.equal(el('#inbox-series-dialog').open, true);
    assert.ok(el('#inbox-series-frame').src.includes('picker=file-a'));
    const message = {type: 'pack-series-selected', token: 'file-a', volume_id: 4};
    w.dispatchEvent(new w.MessageEvent('message', {origin: 'https://untrusted.test', source: el('#inbox-series-frame').contentWindow, data: message}));
    await tick();
    assert.equal(state.calls.length, 0);
    w.dispatchEvent(new w.MessageEvent('message', {origin: w.location.origin, source: el('#inbox-series-frame').contentWindow, data: message}));
    await tick();
    assert.deepEqual(state.calls.at(-1), {path: '/pack-inbox/match', data: {token: 'file-a', volume_id: 4, issue_ids: [17]}});
    assert.equal(el('#inbox-series-dialog').open, false);
});

test('ambiguous issue selection waits for explicit choice without importing', async t => {
    const { w, el, state } = await page(t, {
        items: [{status: 'review', relative_path: 'Unknown.cbz', token: 'b', series_query: 'Unknown'}],
        respond: path => path === '/pack-inbox/match-options' ? {title: 'Series', selected: [], issues: [{id: 8, number: 1, owned: false}, {id: 9, number: 2, owned: true}]} : undefined
    });
    el('.inbox-add-series').click();
    w.dispatchEvent(new w.MessageEvent('message', {origin: w.location.origin, source: el('#inbox-series-frame').contentWindow, data: {type: 'pack-series-selected', token: 'b', volume_id: 2}}));
    await tick();
    assert.equal(state.calls.length, 1);
    assert.equal(el('#inbox-series-issues').classList.contains('hidden'), false);
    assert.equal(el('#inbox-issue-selection').options[1].disabled, false);
    assert.match(el('#inbox-issue-selection').options[1].textContent, /already owned/);
});

test('100 selected imports run sequentially with one file per request', async t => {
    const items = Array.from({length: 100}, (_, i) => ({
        status: 'matched', relative_path: `${i}.cbz`, token: String(i)
    }));
    let active = 0;
    const {el, state} = await page(t, {
        items,
        respond: async (path, data) => {
            assert.equal(path, '/pack-inbox/import');
            assert.equal(data.items.length, 1);
            assert.equal(++active, 1);
            await tick();
            active--;
            return {folder: '/inbox', items: []};
        }
    });
    el('#inbox-select').click();
    const pending = el('#inbox-import').onclick();
    assert.equal(el('#inbox-import').disabled, true);
    await pending;
    assert.deepEqual(state.calls.map(call => call.data.items[0]), items.map(item => item.token));
    assert.match(el('#inbox-status').textContent, /Processed 100/);
});

test('interrupted import stops without retrying or starting remaining files', async t => {
    let requests = 0;
    const {el, state} = await page(t, {
        items: ['a', 'b', 'c'].map(token => ({status: 'matched', relative_path: token, token})),
        respond: async () => {
            if (++requests === 2) throw {status: 504, json: async () => { throw Error('Not JSON'); }};
            return {folder: '/inbox', items: []};
        }
    });
    el('#inbox-select').click();
    await el('#inbox-import').onclick();
    assert.equal(state.calls.length, 2);
    assert.match(el('#inbox-status').textContent, /Stopped after 1 of 3/);
    assert.match(el('#inbox-status').textContent, /may still be running/);
    assert.match(el('#inbox-status').textContent, /HTTP 504/);
    assert.equal(el('#inbox-refresh').disabled, false);
});


test('attention and review filters exclude owned entries while all retains them', async t => {
    const {el} = await page(t, {
        items: ['review', 'held', 'importing', 'owned', 'matched', 'imported', 'discarded']
            .map(status => ({status, relative_path: status + '.cbz', token: status}))
    });
    const visible = () => [...el('#inbox-results').children]
        .filter(row => !row.hidden).map(row => row.dataset.status);
    el('#inbox-filter').value = 'review';
    el('#inbox-filter').onchange();
    assert.deepEqual(visible(), ['review', 'held', 'importing']);
    el('#inbox-filter').value = 'pending';
    el('#inbox-filter').onchange();
    assert.deepEqual(visible(), ['review', 'held', 'importing', 'matched']);
    el('#inbox-filter').value = 'all';
    el('#inbox-filter').onchange();
    assert.equal(visible().length, 7);
});


test('picker records an owned issue and hides it from needs attention', async t => {
    const item = {status: 'review', relative_path: 'Other Title #001.cbz', token: 'owned-file', series_query: 'Other Title'};
    const {w, el, state} = await page(t, {
        items: [item],
        respond: path => {
            if (path === '/pack-inbox/match-options') return {title: 'Series', selected: [9], issues: [{id: 9, number: 1, owned: true}]};
            if (path === '/pack-inbox/match') return {folder: '/inbox', items: [{...item, status: 'owned'}]};
        }
    });
    el('.inbox-add-series').click();
    w.dispatchEvent(new w.MessageEvent('message', {origin: w.location.origin, source: el('#inbox-series-frame').contentWindow, data: {type: 'pack-series-selected', token: item.token, volume_id: 2}}));
    await tick();
    assert.deepEqual(state.calls.at(-1).data, {token: item.token, volume_id: 2, issue_ids: [9]});
    assert.equal(el('#inbox-series-dialog').open, false);
    assert.equal(el('#inbox-results tr').hidden, true);
    assert.equal(el('#inbox-results input'), null);
    assert.match(el('#inbox-status').textContent, /excluded from import/);
    assert.equal(state.calls.some(call => call.path.endsWith('/import')), false);
});

const subscription = id => ({id, enabled: true, weekday: 0, query: 'weekly',
    link_filter: 'Marvel', service: 'GetComics', automatic: true, message: ''});

test('a pending subscription poll preserves weekday edits made after it started', async t => {
    const {w, el, state} = await page(t, {subscriptions: [subscription(1)]});
    Object.defineProperty(w.document, 'hidden', {value: false});
    let finish;
    w.fetchAPI = async path => {
        assert.equal(path, '/pack-subscriptions');
        return new Promise(resolve => {finish = resolve;});
    };
    state.timers.find(timer => timer.delay === 10000).fn();
    const day = el('.pack-sub-day select');
    day.value = '3'; day.onchange();
    finish({result: state});
    await tick();
    assert.equal(el('.pack-sub-day select').value, '3');
    assert.equal(el('.pack-sub-day select').dataset.dirty, 'true');
});

test('saving one subscription preserves another draft and rejects an older poll', async t => {
    const {w, el, state} = await page(t, {
        subscriptions: [subscription(1), subscription(2)],
        respond: (path, data) => {
            assert.equal(path, '/pack-subscriptions/schedule');
            state.subscriptions[0].weekday = data.weekday;
            return {};
        }
    });
    Object.defineProperty(w.document, 'hidden', {value: false});
    let finishOld;
    const originalFetch = w.fetchAPI;
    w.fetchAPI = () => new Promise(resolve => {finishOld = resolve;});
    state.timers.find(timer => timer.delay === 10000).fn();
    w.fetchAPI = originalFetch;
    const days = w.document.querySelectorAll('.pack-sub-day select');
    days[0].value = '3'; days[0].onchange();
    days[1].value = '5'; days[1].onchange();
    await el('.pack-sub-save').onclick();
    finishOld({result: {...state, subscriptions: [subscription(1), subscription(2)]}});
    await tick();
    const updated = w.document.querySelectorAll('.pack-sub-day select');
    assert.equal(updated[0].value, '3');
    assert.equal(updated[0].dataset.dirty, undefined);
    assert.equal(updated[1].value, '5');
    assert.equal(updated[1].dataset.dirty, 'true');
});

test('failed weekday save keeps its draft and restores controls for retry', async t => {
    const {el} = await page(t, {
        subscriptions: [subscription(1)],
        respond: () => {throw Error('Server unavailable');}
    });
    const day = el('.pack-sub-day select');
    day.value = '4'; day.onchange();
    await el('.pack-sub-save').onclick();
    assert.equal(el('.pack-sub-day select').value, '4');
    assert.equal(el('.pack-sub-day select').dataset.dirty, 'true');
    assert.equal(el('.pack-sub-save').disabled, false);
    assert.equal(el('.pack-sub-day select').disabled, false);
    assert.match(el('#pack-discovery-status').textContent, /selection is retained/);
});

test('older pack responses cannot restore ready actions after a finished response', async t => {
    const {w, el} = await page(t, {jobs: [job('one', 'ready')]});
    const responses = [];
    w.fetchAPI = async path => {
        assert.equal(path, '/pack-downloads');
        return new Promise(resolve => responses.push(resolve));
    };
    const older = el('#pack-jobs-refresh').onclick();
    const newer = el('#pack-jobs-refresh').onclick();
    responses[1]({result: [job('one', 'finished')]});
    await newer;
    responses[0]({result: [job('one', 'ready')]});
    await older;
    assert.equal(el('#pack-download-jobs').children.length, 0);
    assert.equal(el('#pack-finished-jobs').children.length, 1);
    assert.equal(el('#pack-finished-jobs .pack-finish'), null);
    assert.equal(el('#pack-finished-jobs .pack-review'), null);
});

test('cleanup invalidates pending polls and keeps actions disabled until completion', async t => {
    let finishCleanup;
    const {w, el, state} = await page(t, {
        confirm: true, jobs: [job('one', 'ready')],
        respond: path => {
            if (path === '/pack-downloads/finish-preview') return {token: 'preview', files: [], bytes: 0};
            if (path === '/pack-downloads/finish') return new Promise(resolve => {finishCleanup = resolve;});
        }
    });
    const originalFetch = w.fetchAPI;
    let finishPoll;
    w.fetchAPI = () => new Promise(resolve => {finishPoll = resolve;});
    const pendingPoll = el('#pack-jobs-refresh').onclick();
    w.fetchAPI = originalFetch;
    const finishButton = el('.pack-finish');
    const cleanup = finishButton.onclick();
    await tick();
    finishPoll({result: [job('one', 'ready')]});
    await pendingPoll;
    await el('#pack-jobs-refresh').onclick();
    assert.equal(el('.pack-finish'), finishButton);
    assert.equal(finishButton.disabled, true);
    assert.equal(el('.pack-review').disabled, true);
    state.jobs = [job('one', 'finished')];
    finishCleanup({});
    await cleanup;
    assert.equal(el('#pack-download-jobs').children.length, 0);
    assert.equal(el('#pack-finished-jobs').children.length, 1);
});

test('an older polling error cannot overwrite newer pack status', async t => {
    const {w, el} = await page(t);
    let rejectOld;
    w.fetchAPI = () => new Promise((resolve, reject) => {rejectOld = reject;});
    const older = el('#pack-jobs-refresh').onclick();
    w.fetchAPI = async () => ({result: []});
    await el('#pack-jobs-refresh').onclick();
    el('#pack-download-status').textContent = 'Newer result';
    rejectOld({json: async () => ({error: 'InvalidKeyValue', result: {value: 'Old failure'}})});
    await older;
    assert.equal(el('#pack-download-status').textContent, 'Newer result');
});

test('periodic pack refresh waits for a slow outstanding request', async t => {
    const {w, el, state} = await page(t);
    Object.defineProperty(w.document, 'hidden', {value: false});
    let complete, requests = 0;
    w.fetchAPI = () => {
        requests++;
        return new Promise(resolve => {complete = resolve;});
    };
    const pending = el('#pack-jobs-refresh').onclick();
    const poll = state.timers.find(timer => timer.delay === 5000);
    poll.fn(); poll.fn();
    assert.equal(requests, 1);
    complete({result: [job('slow', 'ready')]});
    await pending;
    assert.match(el('#pack-download-jobs summary').textContent, /slow/);
    poll.fn();
    assert.equal(requests, 2);
    complete({result: []});
    await tick();
});
