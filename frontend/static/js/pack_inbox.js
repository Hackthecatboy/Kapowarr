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
        rows.querySelectorAll('input').forEach(el => el.disabled = busy);
        importButton.textContent = `Import Selected Copies (${selected().length})`;
        importButton.disabled = busy || !selected().length || selected().length > 100;
    }
    function render(data) {
        folder.value = data.folder;
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
            if (item.destination) {
                const destination = document.createElement('p');
                destination.textContent = 'Library copy: ' + item.destination;
                info.appendChild(destination);
            }
            row.append(selection, file, info); rows.appendChild(row);
        }
        status.textContent = `${data.items.length} files. Select up to 100 matched files to import. Held or interrupted copies require review before any further action.`;
    }
    async function request(action) {
        if (busy) return;
        const data = action === 'scan' ? {folder: folder.value} : {items: selected()};
        busy = true; controls(); status.classList.remove('error');
        status.textContent = action === 'import' ? 'Copying and verifying selected files…' : 'Loading inbox…';
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
    request('refresh');
});
