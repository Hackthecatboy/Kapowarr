usingApiKey().then(apiKey => {
    const form = document.querySelector('#pack-inbox-form');
    const folder = document.querySelector('#inbox-folder');
    const rows = document.querySelector('#inbox-results');
    const status = document.querySelector('#inbox-status');
    const importButton = document.querySelector('#inbox-import');
    let busy = false;
    const selected = () => [...rows.querySelectorAll('input:checked')].map(input => input.value);
    function controls() {
        form.querySelectorAll('button,input').forEach(el => el.disabled = busy);
        rows.querySelectorAll('input,button').forEach(el => el.disabled = busy);
        importButton.textContent = `Import Selected Copies (${selected().length})`;
        importButton.disabled = busy || !selected().length || selected().length > 100;
    }
    function render(data) {
        folder.value = data.folder;
        if (!packFolder.value) packFolder.value = data.folder;
        rows.replaceChildren();
        for (const item of data.items) {
            const row = document.createElement('tr');
            const selection = document.createElement('td');
            if (item.status === 'matched') {
                const input = document.createElement('input');
                input.type = 'checkbox'; input.value = item.token;
                input.setAttribute('aria-label', 'Import ' + item.relative_path);
                input.onchange = controls;
                selection.appendChild(input);
            }
            const file = document.createElement('td');
            file.textContent = item.relative_path;
            const info = document.createElement('td');
            info.textContent = `${item.status}: ${item.message}`;
            if (item.can_cleanup) {
                const cleanup = document.createElement('button');
                cleanup.type = 'button'; cleanup.className = 'inbox-cleanup';
                cleanup.textContent = 'Delete Imported Source';
                cleanup.onclick = () => request('cleanup', [item.token]);
                info.append(document.createElement('br'), cleanup);
            }
            if (item.series_query) {
                const action = document.createElement('p');
                const add = document.createElement('a');
                add.href = `${url_base}/add?q=${encodeURIComponent(item.series_query)}`;
                add.target = '_blank'; add.rel = 'noopener';
                add.textContent = 'Find / Add Series';
                add.className = 'inbox-add-series';
                action.append(add, ' — opens series search in a new tab. Choose the correct series, then return and Save Folder & Scan.');
                info.append(action);
            }
            if (item.destination) {
                const destination = document.createElement('p');
                destination.textContent = 'Library copy: ' + item.destination;
                info.appendChild(destination);
            }
            row.append(selection, file, info); rows.appendChild(row);
        }
        status.textContent = `${data.items.length} files. Select up to 100 matched files to import. Held or interrupted copies require review before any further action.`;
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
    document.querySelector('#inbox-refresh').onclick = () => request('refresh');
    document.querySelector('#inbox-select').onclick = () => {
        [...rows.querySelectorAll('input')].forEach((input, index) => input.checked = index < 100);
        controls();
    };
    importButton.onclick = () => request('import');
    const packForm = document.querySelector('#pack-download-form');
    const packStatus = document.querySelector('#pack-download-status');
    const packFolder = document.querySelector('#pack-download-folder');
    const choices = document.querySelector('#pack-download-choices');
    const jobs = document.querySelector('#pack-download-jobs');
    let downloadingRequest = false;
    async function packError(error) {
        let message = 'Pack operation failed. Check System → Logs.';
        try { const body = await error.json(); if (body.error === 'InvalidKeyValue') message = String(body.result.value); } catch (_) {}
        packStatus.textContent = message;
    }
    async function refreshJobs() {
        try {
            const data = await fetchAPI('/pack-downloads', apiKey);
            jobs.replaceChildren();
            for (const job of data.result) {
                if (!packFolder.value || packFolder.value === job.folder || packFolder.value.startsWith(job.folder + '/')) packFolder.value = job.root;
                const row = document.createElement('article');
                const title = document.createElement('strong');
                title.textContent = job.title;
                const info = document.createElement('p');
                info.textContent = `${job.status}: ${(job.received / 1024 / 1024).toFixed(1)} MiB${job.total ? ' / ' + (job.total / 1024 / 1024).toFixed(1) + ' MiB' : ''}. ${job.message}`;
                const path = document.createElement('p');
                path.textContent = job.folder;
                row.append(title, info, path);
                if (job.status === 'ready') {
                    const review = document.createElement('button');
                    review.type = 'button'; review.textContent = 'Scan Pack for Review';
                    review.onclick = () => { if (!busy) { folder.value = job.folder + '/ready'; request('scan'); } };
                    row.append(review);
                }
                if (job.status === 'ready' || job.status === 'held') {
                    const finish = document.createElement('button');
                    finish.type = 'button'; finish.textContent = 'Finish Pack / Delete Remaining Files';
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
                    row.append(finish);
                }
                jobs.append(row);
            }
        } catch (error) { await packError(error); }
    }
    packForm.onsubmit = async event => {
        event.preventDefault();
        if (downloadingRequest) return;
        downloadingRequest = true;
        choices.replaceChildren(); packStatus.textContent = 'Reading article download links…';
        try {
            const data = await (await sendAPI('POST', '/pack-downloads/preview', apiKey, {}, {url: document.querySelector('#pack-article').value})).json();
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
    const discoveryStatus = document.querySelector('#pack-discovery-status');
    const queryInput = document.querySelector('#pack-query');
    let historyPage = 1;
    let historyQuery = '';
    let discoveryBusy = false;
    async function discoveryPost(action, data = {}) {
        return (await (await sendAPI('POST', `/pack-subscriptions/${action}`, apiKey, {}, data)).json()).result;
    }
    function previewArticle(article) {
        document.querySelector('#pack-article').value = article;
        packForm.requestSubmit();
        packForm.scrollIntoView({block: 'start'});
    }
    function articleRow(title, url, message = '') {
        const row = document.createElement('p');
        const preview = document.createElement('button');
        preview.type = 'button'; preview.textContent = 'Preview Links';
        preview.onclick = () => previewArticle(url);
        row.append(title + (message ? ' — ' + message + ' ' : ' '), preview);
        return row;
    }
    async function searchPacks(reset) {
        if (discoveryBusy) return;
        discoveryBusy = true;
        if (reset) { historyPage = 1; historyQuery = queryInput.value; }
        discoveryStatus.textContent = 'Searching GetComics…';
        try {
            const result = await discoveryPost('search', {query: historyQuery, page: historyPage});
            const rows = document.querySelector('#pack-history-results');
            if (reset) rows.replaceChildren();
            result.articles.forEach(article => rows.append(articleRow(article.title, article.url)));
            document.querySelector('#pack-older').disabled = !result.has_more || historyPage >= 100;
            historyPage += 1;
            discoveryStatus.textContent = `${result.articles.length} articles on this page. Preview and select older packs individually.`;
        } catch (error) { discoveryStatus.textContent = 'Search failed; check System Logs.'; }
        finally { discoveryBusy = false; }
    }
    async function refreshSubscriptions() {
        try {
            const data = (await fetchAPI('/pack-subscriptions', apiKey)).result;
            const rows = document.querySelector('#pack-subscriptions'); rows.replaceChildren();
            for (const sub of data.subscriptions) {
                const row = document.createElement('p');
                const toggle = document.createElement('button'); toggle.type = 'button';
                toggle.textContent = sub.enabled ? 'Pause' : 'Resume';
                toggle.onclick = async () => {
                    try { await discoveryPost('toggle', {id: sub.id, enabled: !sub.enabled}); await refreshSubscriptions(); }
                    catch (error) { discoveryStatus.textContent = 'Could not change subscription.'; }
                };
                row.append(`${sub.query} / ${sub.link_filter} / ${sub.service} — ${sub.automatic ? 'Automatic download' : 'Review only'} — ${sub.message}. Last check: ${sub.last_checked || 'Not checked yet'} `, toggle);
                const day = document.querySelector('#pack-weekday').cloneNode(true);
                day.removeAttribute('id'); day.value = String(sub.weekday);
                day.onchange = () => { day.dataset.dirty = 'true'; };
                day.setAttribute('aria-label', 'Check weekday for ' + sub.query);
                const save = document.createElement('button'); save.type = 'button'; save.textContent = 'Save Day';
                save.onclick = async () => {
                    save.disabled = true;
                    try { await discoveryPost('schedule', {id: sub.id, weekday: Number(day.value)}); await refreshSubscriptions(); }
                    catch (_) { discoveryStatus.textContent = 'Could not save weekday.'; }
                    finally { save.disabled = false; }
                };
                row.append(' Check every ', day, save);
                rows.append(row);
            }
            const releases = document.querySelector('#pack-subscription-releases'); releases.replaceChildren();
            data.releases.forEach(release => releases.append(articleRow(release.title, release.article, `${release.status}: ${release.message}`)));
        } catch (_) { discoveryStatus.textContent = 'Could not load subscriptions.'; }
    }
    document.querySelector('#pack-discovery-form').onsubmit = event => { event.preventDefault(); searchPacks(true); };
    document.querySelector('#pack-older').onclick = () => searchPacks(false);
    document.querySelector('#pack-subscribe').onclick = async () => {
        try {
            await discoveryPost('create', {query: queryInput.value, link_filter: document.querySelector('#pack-link-filter').value,
                service: document.querySelector('#pack-service').value, folder: packFolder.value,
                weekday: Number(document.querySelector('#pack-weekday').value),
                automatic: document.querySelector('#pack-sub-mode').value === 'download'});
            discoveryStatus.textContent = 'Subscription saved. Checks run weekly on the selected day; use Check Subscriptions Now to check sooner.';
            await refreshSubscriptions();
        } catch (error) {
            try { discoveryStatus.textContent = (await error.json()).result.value; }
            catch (_) { discoveryStatus.textContent = 'Could not save subscription.'; }
        }
    };
    document.querySelector('#pack-check').onclick = async () => {
        try { await discoveryPost('check'); discoveryStatus.textContent = 'Subscription check queued. Results update below.'; }
        catch (_) { discoveryStatus.textContent = 'Could not queue subscription check.'; }
    };
    refreshSubscriptions();
    setInterval(() => {
        const subscriptions = document.querySelector('#pack-subscriptions');
        if (!document.hidden && !subscriptions.contains(document.activeElement) && !subscriptions.querySelector('[data-dirty]')) refreshSubscriptions();
    }, 10000);
    document.querySelector('#pack-jobs-refresh').onclick = refreshJobs;
    refreshJobs();
    setInterval(() => { if (!document.hidden) refreshJobs(); }, 5000);
    request('refresh');
});
