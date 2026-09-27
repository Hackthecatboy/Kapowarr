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
    document.querySelector('#pack-jobs-refresh').onclick = refreshJobs;
    refreshJobs();
    setInterval(() => { if (!document.hidden) refreshJobs(); }, 5000);
    request('refresh');
});
