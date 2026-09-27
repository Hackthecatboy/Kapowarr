const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { test } = require('node:test');
const { JSDOM } = require('jsdom');

const root = resolve(__dirname, '../..');
const template = readFileSync(resolve(root, 'frontend/templates/queue.html'), 'utf8');
const script = readFileSync(resolve(root, 'frontend/static/js/queue.js'), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const hash = 'a'.repeat(40);
const discovered = {
    id: `discovered-1-${hash}`, discovered: true, client_id: 1, torrent_hash: hash,
    title: 'Collection', source_name: 'qbit', web_title: '', web_sub_title: null,
    web_link: '', status: 'seeding', size: 100, progress: 100, speed: 0, can_review: true
};

async function page(t) {
    const rows = template.match(/\{% block pre_build_rows %\}([\s\S]*?)\{% endblock %\}/)[1];
    const main = template.match(/<main>[\s\S]*?<\/main>/)[0];
    const html = `<div class="pre-build-els"><table>${rows}</table></div>${main}<button id="removeall-button"></button>`;
    const dom = new JSDOM(html.replace(/\{%[\s\S]*?%\}|\{\{[\s\S]*?\}\}/g, ''), {
        url: 'http://kapowarr.test/', runScripts: 'outside-only'
    });
    t.after(() => dom.window.close());
    const w = dom.window;
    const state = { jobs: [{ ...discovered }], tracked: [], calls: [], errors: [] };
    w.url_base = '/kapowarr';
    w.usingApiKey = async () => 'key';
    w.socket = { on() {} };
    w.setInterval = () => {};
    w.convertSize = value => String(value);
    w.minDecimalPoints = value => String(value);
    w.fetchAPI = async path => {
        if (path === '/activity/queue') return { result: state.tracked };
        assert.equal(path, '/activity/queue/discovered');
        return { result: { downloads: state.jobs, errors: state.errors } };
    };
    w.sendAPI = async (...args) => {
        state.calls.push(JSON.parse(JSON.stringify(args)));
        return { ok: false, json: async () => ({ error: 'InvalidKeyValue', result: { value: 'Torrent no longer complete' } }) };
    };
    w.eval(script);
    await tick();
    return { w, state, el: selector => w.document.querySelector(selector) };
}

test('discovery rows remain unique and offer review without destructive controls', async t => {
    const { w, el, state } = await page(t);
    await w.fillQueue('key');
    assert.equal(el('#queue').children.length, 1);
    assert.equal(el('#queue .review-torrent-dl').disabled, false);
    for (const selector of ['.remove-dl', '.blocklist-dl', '.move-up-dl', '.move-down-dl']) {
        assert.equal(el('#queue ' + selector).hidden, true);
    }
    assert.equal(state.calls.length, 0);
    state.jobs = [];
    await w.fillQueue('key');
    assert.equal(el('#queue').children.length, 0);
});

test('incomplete torrents disable review; explicit review handles revalidation errors', async t => {
    const { w, el, state } = await page(t);
    state.jobs[0].can_review = false;
    await w.fillQueue('key');
    assert.equal(el('#queue .review-torrent-dl').disabled, true);
    state.jobs[0].can_review = true;
    await w.fillQueue('key');
    await el('#queue .review-torrent-dl').onclick();
    assert.equal(state.calls[0][1], '/activity/queue/discovered/review');
    assert.deepEqual(state.calls[0][4], { client_id: 1, info_hash: hash });
    assert.equal(el('#queue .recovery-error').textContent, 'Torrent no longer complete');
});

test('client errors are visible even with an empty queue', async t => {
    const { w, el, state } = await page(t);
    state.jobs = [];
    state.errors = ['qbit: Connection failed'];
    await w.fillQueue('key');
    assert.equal(el('#discovery-status').textContent, 'qbit: Connection failed');
});

test('review waits for the scan response body and recovers from interrupted delivery', async t => {
    const {w, el} = await page(t);
    let failBody;
    let reading = false;
    w.sendAPI = async () => ({ok: true, json: () => {
        reading = true;
        return new Promise((resolve, reject) => {failBody = reject;});
    }});
    const pending = el('#queue .review-torrent-dl').onclick();
    await tick();
    assert.equal(reading, true);
    assert.equal(el('#queue .review-torrent-dl').disabled, true);
    failBody(new Error('Connection interrupted'));
    await pending;
    assert.equal(el('#queue .review-torrent-dl').disabled, false);
    assert.match(el('#queue .recovery-error').textContent, /Could not scan/);
});


test('tracked completed torrents offer review without duplicating discovery rows', async t => {
    const { w, el, state } = await page(t);
    state.jobs = [];
    state.tracked = [{ ...discovered, id: 42, discovered: false }];
    await w.fillQueue('key');
    assert.equal(el('#queue').children.length, 1);
    assert.equal(el('#queue .review-torrent-dl').classList.contains('hidden'), false);
    assert.equal(el('#queue .remove-dl').hidden, false);
    await el('#queue .review-torrent-dl').onclick();
    assert.deepEqual(state.calls[0][4], { client_id: 1, info_hash: hash });
    state.tracked[0].can_review = false;
    await w.fillQueue('key');
    assert.equal(el('#queue .review-torrent-dl').classList.contains('hidden'), true);
});
