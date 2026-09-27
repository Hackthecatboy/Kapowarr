const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {JSDOM} = require('jsdom');
const source = readFileSync(resolve(__dirname, '../../frontend/static/js/add_volume.js'), 'utf8');
const addFunction = source.slice(source.indexOf('function addVolume()'), source.indexOf('// code run on load'));

test('add volume exits loading state on server and network failures', async t => {
    const dom = new JSDOM('', {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const w = dom.window;
    const fields = Object.fromEntries(['volume_folder_input', 'cv_input', 'root_folder_input',
        'monitor_volume_input', 'monitor_issues_input', 'monitoring_scheme',
        'special_state_input', 'auto_search_input', 'submit'].map(name => [name, {value: '', style: {}}]));
    w.SearchEls = {window: fields};
    w.setLocalStorage = () => {};
    w.usingApiKey = async () => 'key';
    w.console.error = () => {};
    let loading;
    w.showLoadWindow = () => { loading = true; };
    w.showWindow = () => { loading = false; };
    w.eval(addFunction);
    for (const error of [{status: 500}, {status: 504}, new TypeError('Network error')]) {
        w.sendAPI = async () => {throw error;};
        await w.addVolume();
        assert.equal(loading, false);
        assert.match(fields.submit.innerText, /Check the library before retrying/);
    }
});
