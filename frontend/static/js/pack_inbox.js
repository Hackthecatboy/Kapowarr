const PackEls = {
    inbox: {
        form: document.querySelector('#pack-inbox-form'),
        folder: document.querySelector('#inbox-folder'),
        results: document.querySelector('#inbox-results'),
        status: document.querySelector('#inbox-status'),
        import: document.querySelector('#inbox-import'),
        filter: document.querySelector('#inbox-filter'),
        search: document.querySelector('#inbox-search'),
        refresh: document.querySelector('#inbox-refresh'),
        select: document.querySelector('#inbox-select'),
        heading: document.querySelector('#pack-review-heading')
    },
    download: {
        form: document.querySelector('#pack-download-form'),
        status: document.querySelector('#pack-download-status'),
        folder: document.querySelector('#pack-download-folder'),
        choices: document.querySelector('#pack-download-choices'),
        jobs: document.querySelector('#pack-download-jobs'),
        finished: document.querySelector('#pack-finished-jobs'),
        finished_count: document.querySelector('#pack-finished-count'),
        article: document.querySelector('#pack-article'),
        controls: document.querySelector('#pack-download-controls'),
        refresh: document.querySelector('#pack-jobs-refresh')
    },
    discovery: {
        status: document.querySelector('#pack-discovery-status'),
        query: document.querySelector('#pack-query'),
        history_results: document.querySelector('#pack-history-results'),
        search_results: document.querySelector('#pack-search-results'),
        older: document.querySelector('#pack-older'),
        subscriptions: document.querySelector('#pack-subscriptions'),
        weekday: document.querySelector('#pack-weekday'),
        subscription_releases: document.querySelector('#pack-subscription-releases'),
        release_count: document.querySelector('#pack-release-count'),
        form: document.querySelector('#pack-discovery-form'),
        subscribe: document.querySelector('#pack-subscribe'),
        link_filter: document.querySelector('#pack-link-filter'),
        service: document.querySelector('#pack-service'),
        sub_mode: document.querySelector('#pack-sub-mode'),
        check: document.querySelector('#pack-check')
    },
    templates: {
        file: document.querySelector('#pack-file-template'),
        job: document.querySelector('#pack-job-template'),
        article: document.querySelector('#pack-article-template'),
        subscription: document.querySelector('#pack-subscription-template')
    }
};

// Clone static markup; API values are assigned as text, never parsed as HTML.
function clonePackRow(template) {
    return template.content.firstElementChild.cloneNode(true);
}

usingApiKey().then(apiKey => {
    const form = PackEls.inbox.form;
    const folder = PackEls.inbox.folder;
    const rows = PackEls.inbox.results;
    const status = PackEls.inbox.status;
    const importButton = PackEls.inbox.import;
    let busy = false;
    const selected = () => [...rows.querySelectorAll('tr:not([hidden]) input:checked')].map(input => input.value);
    function controls() {
        form.querySelectorAll('button,input').forEach(el => el.disabled = busy);
        rows.querySelectorAll('input,button').forEach(el => el.disabled = busy);
        importButton.textContent = `Import Selected Copies (${selected().length})`;
        importButton.disabled = busy || !selected().length || selected().length > 100;
    }
    function filterRows() {
        const mode = PackEls.inbox.filter.value;
        const query = PackEls.inbox.search.value.trim().toLocaleLowerCase();
        for (const row of rows.children) {
            const state = row.dataset.status;
            const visible = (mode === 'all' || (mode === 'pending' && !['imported', 'discarded'].includes(state))
                || (mode === 'review' && !['matched', 'imported', 'discarded'].includes(state)) || mode === state)
                && row.dataset.path.toLocaleLowerCase().includes(query);
            row.hidden = !visible;
            if (!visible) row.querySelectorAll('input').forEach(input => input.checked = false);
        }
        const visible = [...rows.children].filter(row => !row.hidden).length;
        status.textContent = `${visible} of ${rows.children.length} files shown. ${rows.querySelectorAll('[data-status="matched"]').length} matched. Select up to 100 to import.`;
        controls();
    }
    PackEls.inbox.filter.onchange = filterRows;
    PackEls.inbox.search.oninput = filterRows;
    function render(data) {
        folder.value = data.folder;
        if (!packFolder.value) packFolder.value = data.folder;
        rows.replaceChildren();
        for (const item of data.items) {
            const row = clonePackRow(PackEls.templates.file);
            row.dataset.status = item.status;
            row.dataset.path = item.relative_path;
            const input = row.querySelector('input');
            if (item.status === 'matched') {
                input.value = item.token;
                input.setAttribute('aria-label', 'Import ' + item.relative_path);
                input.onchange = controls;
            } else {
                input.remove();
            }
            const file = row.querySelector('.inbox-file');
            file.textContent = item.relative_path.split('/').pop();
            file.title = item.relative_path;
            row.querySelector('.inbox-message').textContent = `${item.status}: ${item.message}`;
            const cleanup = row.querySelector('.inbox-cleanup');
            if (item.can_cleanup) {
                cleanup.onclick = () => request('cleanup', [item.token]);
            } else {
                cleanup.parentElement.remove();
            }
            const add = row.querySelector('.inbox-add-series');
            if (item.series_query) {
                add.href = `${url_base}/add?q=${encodeURIComponent(item.series_query)}`;
            } else {
                add.parentElement.remove();
            }
            const destination = row.querySelector('.inbox-destination');
            if (item.destination) {
                destination.querySelector('span').textContent = item.destination;
            } else {
                destination.remove();
            }
            rows.appendChild(row);
        }
        filterRows();
    }
    async function request(action, tokens = null) {
        if (busy) return;
        const data = action === 'scan' ? {folder: folder.value} : {items: tokens || selected()};
        busy = true; controls(); status.classList.remove('error');
        status.textContent = action === 'import' ? 'Copying and verifying selected files…' : action === 'cleanup' ? 'Verifying the library copy before deleting its extracted source…' : 'Loading inbox…';
        try {
            const result = action === 'refresh'
                ? await fetchAPI('/pack-inbox', apiKey)
                : await (await sendAPI('POST', `/pack-inbox/${action}`, apiKey, {}, data)).json();
            render(result.result);
        } catch (error) {
            let message = 'Inbox operation failed. Refresh results to inspect any completed or held copies.';
            try { const response = await error.json(); if (response.error === 'InvalidKeyValue') message = String(response.result.value); } catch (_) {}
            status.textContent = message; status.classList.add('error');
        } finally { busy = false; controls(); }
    }
    form.onsubmit = event => { event.preventDefault(); if (form.reportValidity()) request('scan'); };
    PackEls.inbox.refresh.onclick = () => request('refresh');
    PackEls.inbox.select.onclick = () => {
        [...rows.querySelectorAll('tr:not([hidden]) input')].forEach((input, index) => input.checked = index < 100);
        controls();
    };
    importButton.onclick = () => request('import');
    const packForm = PackEls.download.form;
    const packStatus = PackEls.download.status;
    const packFolder = PackEls.download.folder;
    const choices = PackEls.download.choices;
    const jobs = PackEls.download.jobs;
    let downloadingRequest = false;
    async function packError(error) {
        let message = 'Pack operation failed. Check System → Logs.';
        try { const body = await error.json(); if (body.error === 'InvalidKeyValue') message = String(body.result.value); } catch (_) {}
        packStatus.textContent = message;
    }
    const jobOpen = new Map();
    async function refreshJobs() {
        try {
            const data = await fetchAPI('/pack-downloads', apiKey);
            jobs.replaceChildren();
            const finishedJobs = PackEls.download.finished; finishedJobs.replaceChildren();
            PackEls.download.finished_count.textContent = `(${data.result.filter(job => job.status === 'finished').length})`;
            for (const job of data.result) {
                if (!packFolder.value || packFolder.value === job.folder || packFolder.value.startsWith(job.folder + '/')) packFolder.value = job.root;
                const row = clonePackRow(PackEls.templates.job);
                row.open = jobOpen.get(job.id) ?? (job.status !== 'finished');
                row.ontoggle = () => { if (row.isConnected) jobOpen.set(job.id, row.open); };
                const title = row.querySelector('summary');
                title.textContent = `${job.title} — ${job.status}`;
                const info = row.querySelector('.pack-job-info');
                info.textContent = `${job.status}: ${(job.received / 1024 / 1024).toFixed(1)} MiB${job.total ? ' / ' + (job.total / 1024 / 1024).toFixed(1) + ' MiB' : ''}. ${job.message}`;
                const path = row.querySelector('.pack-job-path');
                path.textContent = job.folder;
                const review = row.querySelector('.pack-review');
                const finish = row.querySelector('.pack-finish');
                if (job.status === 'ready') {
                    review.onclick = () => { if (!busy) { folder.value = job.folder + '/ready'; request('scan'); PackEls.inbox.heading.scrollIntoView({block: 'start'}); } };
                } else {
                    review.remove();
                }
                if (job.status === 'ready' || job.status === 'held') {
                    finish.onclick = async () => {
                        finish.disabled = true;
                        try {
                            const preview = (await (await sendAPI('POST', '/pack-downloads/finish-preview', apiKey, {}, {id: job.id})).json()).result;
                            const paths = preview.files.map(file => file.path).join('\n');
                            if (!confirm(`Permanently delete the retained archive and ALL remaining files in this pack? This includes unselected and unmatched comics. Library copies will stay.\n\n${preview.files.length} files, ${(preview.bytes / 1024 / 1024).toFixed(1)} MiB\n${paths}`)) return;
                            await sendAPI('POST', '/pack-downloads/finish', apiKey, {}, {token: preview.token, confirm: true});
                            packStatus.textContent = 'Pack finished. Archive and remaining sources deleted; library copies preserved.';
                            await refreshJobs();
                            request('refresh');
                        } catch (error) { await packError(error); }
                        finally { finish.disabled = false; }
                    };
                } else {
                    finish.remove();
                }
                (job.status === 'finished' ? finishedJobs : jobs).append(row);
            }
        } catch (error) { await packError(error); }
    }
    packForm.onsubmit = async event => {
        event.preventDefault();
        if (downloadingRequest) return;
        downloadingRequest = true;
        choices.replaceChildren(); packStatus.textContent = 'Reading article download links…';
        try {
            const data = await (await sendAPI('POST', '/pack-downloads/preview', apiKey, {}, {url: PackEls.download.article.value})).json();
            packStatus.textContent = `${data.result.title}: choose one pack link or mirror. Do not download every mirror.`;
            if (!data.result.choices.length) packStatus.textContent += ' No supported download buttons found.';
            for (const choice of data.result.choices) {
                const button = document.createElement('button');
                button.type = 'button'; button.textContent = `${choice.label} (${choice.service})${choice.supported ? '' : ' — unsupported'}`;
                button.disabled = !choice.supported;
                button.onclick = async () => {
                    if (downloadingRequest) return;
                    downloadingRequest = true;
                    try {
                        await sendAPI('POST', '/pack-downloads/download', apiKey, {}, {token: choice.token, folder: packFolder.value});
                        packStatus.textContent = 'Pack download started. Progress appears below; the archive and source files will be retained.';
                        choices.replaceChildren();
                        await refreshJobs();
                    } catch (error) { await packError(error); }
                    finally { downloadingRequest = false; }
                };
                choices.append(button);
            }
        } catch (error) { await packError(error); }
        finally { downloadingRequest = false; }
    };
    const discoveryStatus = PackEls.discovery.status;
    const queryInput = PackEls.discovery.query;
    let historyPage = 1;
    let historyQuery = '';
    let discoveryBusy = false;
    async function discoveryPost(action, data = {}) {
        return (await (await sendAPI('POST', `/pack-subscriptions/${action}`, apiKey, {}, data)).json()).result;
    }
    function previewArticle(article) {
        PackEls.download.controls.open = true;
        PackEls.download.article.value = article;
        packForm.requestSubmit();
        packForm.scrollIntoView({block: 'start'});
    }
    function articleRow(title, url, message = '') {
        const row = clonePackRow(PackEls.templates.article);
        const preview = row.querySelector('button');
        preview.onclick = () => previewArticle(url);
        row.querySelector('span').textContent = title + (message ? ' — ' + message + ' ' : ' ');
        return row;
    }
    async function searchPacks(reset) {
        if (discoveryBusy) return;
        discoveryBusy = true;
        if (reset) { historyPage = 1; historyQuery = queryInput.value; }
        discoveryStatus.textContent = 'Searching GetComics…';
        try {
            const result = await discoveryPost('search', {query: historyQuery, page: historyPage});
            const rows = PackEls.discovery.history_results;
            if (reset) rows.replaceChildren();
            PackEls.discovery.search_results.open = true;
            result.articles.forEach(article => rows.append(articleRow(article.title, article.url)));
            PackEls.discovery.older.disabled = !result.has_more || historyPage >= 100;
            historyPage += 1;
            discoveryStatus.textContent = `${result.articles.length} articles on this page. Preview and select older packs individually.`;
        } catch (error) { discoveryStatus.textContent = 'Search failed; check System Logs.'; }
        finally { discoveryBusy = false; }
    }
    async function refreshSubscriptions() {
        try {
            const data = (await fetchAPI('/pack-subscriptions', apiKey)).result;
            const rows = PackEls.discovery.subscriptions; rows.replaceChildren();
            for (const sub of data.subscriptions) {
                const row = clonePackRow(PackEls.templates.subscription);
                const toggle = row.querySelector('.pack-sub-toggle');
                toggle.textContent = sub.enabled ? 'Pause' : 'Resume';
                toggle.onclick = async () => {
                    try { await discoveryPost('toggle', {id: sub.id, enabled: !sub.enabled}); await refreshSubscriptions(); }
                    catch (error) { discoveryStatus.textContent = 'Could not change subscription.'; }
                };
                row.querySelector('.pack-sub-description').textContent = `${sub.query} / ${sub.link_filter} / ${sub.service} — ${sub.automatic ? 'Automatic download' : 'Review only'} — ${sub.message}. Last check: ${sub.last_checked || 'Not checked yet'} `;
                const day = PackEls.discovery.weekday.cloneNode(true);
                day.removeAttribute('id'); day.value = String(sub.weekday);
                day.onchange = () => { day.dataset.dirty = 'true'; };
                day.setAttribute('aria-label', 'Check weekday for ' + sub.query);
                const save = row.querySelector('.pack-sub-save');
                save.onclick = async () => {
                    save.disabled = true;
                    try { await discoveryPost('schedule', {id: sub.id, weekday: Number(day.value)}); await refreshSubscriptions(); }
                    catch (_) { discoveryStatus.textContent = 'Could not save weekday.'; }
                    finally { save.disabled = false; }
                };
                row.querySelector('.pack-sub-day').append(day);
                rows.append(row);
            }
            const releases = PackEls.discovery.subscription_releases; releases.replaceChildren();
            // The same weekly article can be found by several subscriptions.
            const groups = new Map();
            for (const release of data.releases) {
                if (!groups.has(release.article)) groups.set(release.article, {title: release.title, states: new Set()});
                groups.get(release.article).states.add(`${release.status}: ${release.message}`);
            }
            PackEls.discovery.release_count.textContent = `(${groups.size})`;
            [...groups.entries()].sort((a, b) => b[1].title.localeCompare(a[1].title, undefined, {numeric: true}))
                .forEach(([article, group]) => releases.append(articleRow(group.title, article, [...group.states].join('; '))));
        } catch (_) { discoveryStatus.textContent = 'Could not load subscriptions.'; }
    }
    PackEls.discovery.form.onsubmit = event => { event.preventDefault(); searchPacks(true); };
    PackEls.discovery.older.onclick = () => searchPacks(false);
    PackEls.discovery.subscribe.onclick = async () => {
        try {
            await discoveryPost('create', {query: queryInput.value, link_filter: PackEls.discovery.link_filter.value,
                service: PackEls.discovery.service.value, folder: packFolder.value,
                weekday: Number(PackEls.discovery.weekday.value),
                automatic: PackEls.discovery.sub_mode.value === 'download'});
            discoveryStatus.textContent = 'Subscription saved. Checks run weekly on the selected day; use Check Subscriptions Now to check sooner.';
            await refreshSubscriptions();
        } catch (error) {
            try { discoveryStatus.textContent = (await error.json()).result.value; }
            catch (_) { discoveryStatus.textContent = 'Could not save subscription.'; }
        }
    };
    PackEls.discovery.check.onclick = async () => {
        try { await discoveryPost('check'); discoveryStatus.textContent = 'Subscription check queued. Results update below.'; }
        catch (_) { discoveryStatus.textContent = 'Could not queue subscription check.'; }
    };
    refreshSubscriptions();
    setInterval(() => {
        const subscriptions = PackEls.discovery.subscriptions;
        if (!document.hidden && !subscriptions.contains(document.activeElement) && !subscriptions.querySelector('[data-dirty]')) refreshSubscriptions();
    }, 10000);
    PackEls.download.refresh.onclick = refreshJobs;
    refreshJobs();
    setInterval(() => { if (!document.hidden) refreshJobs(); }, 5000);
    request('refresh');
});
