const duplicateStatus = document.querySelector('#duplicates-status');
let duplicateApiKey;
const duplicateResults = document.querySelector('#duplicates-results');
function showDuplicateReport(report) {
    duplicateResults.replaceChildren();
    duplicateStatus.textContent = `Checked ${report.scanned_files} indexed files. ${report.exact.length} identical groups; ${report.same_issue.length} other same-issue groups. ${report.limited ? 'Incomplete scan: limit reached. Select one volume to narrow the scan.' : ''}`;
    function section(title, files) {
        const box = document.createElement('section');
        const heading = document.createElement('h3');
        heading.textContent = title;
        box.append(heading);
        const list = document.createElement('ul');
        for (const file of files) {
            const entry = document.createElement('li');
            entry.textContent = `${file.filepath} (${file.size} bytes)`;
            list.append(entry);
        }
        box.append(list);
        duplicateResults.append(box);
        return box;
    }
    for (const group of report.exact) {
        const box = section(group.same_physical_file ? 'Identical content — paths refer to the same physical file (hard links)' : 'Identical file contents (SHA-256 verified)', group.files);
        if (!group.token) continue;
        const label = document.createElement('label');
        label.textContent = 'Copy to keep: ';
        const keep = document.createElement('select');
        for (const file of group.files) {
            const option = document.createElement('option');
            option.value = file.id;
            option.textContent = file.filepath;
            keep.append(option);
        }
        label.append(keep); box.append(label);
        const selection = document.createElement('div');
        const checks = [];
        for (const file of group.files) {
            const row = document.createElement('label');
            const check = document.createElement('input');
            check.type = 'checkbox'; check.value = file.id;
            row.append(check, document.createTextNode(' Delete ' + file.filepath));
            selection.append(row);
            checks.push(check);
        }
        selection.className = 'duplicate-selection'; box.append(selection);
        const remove = document.createElement('button');
        remove.type = 'button'; remove.textContent = 'Delete Selected Duplicates';
        const status = document.createElement('p'); status.setAttribute('role','status');
        function updateSelection() {
            for (const check of checks) {
                check.disabled = check.value === keep.value;
                if (check.disabled) check.checked = false;
            }
            remove.disabled = !checks.some(check => check.checked);
        }
        keep.onchange = updateSelection;
        checks.forEach(check => check.onchange = updateSelection);
        remove.onclick = async () => {
            const ids = checks.filter(check => check.checked).map(check => Number(check.value));
            const retained = group.files.find(file => file.id === Number(keep.value));
            const paths = group.files.filter(file => ids.includes(file.id)).map(file => file.filepath);
            if (!ids.length || !confirm(`Permanently delete these library copies?\n\n${paths.join('\n')}\n\nKEEP: ${retained.filepath}\n\nFiles will be checked again before deletion.`)) return;
            [...box.querySelectorAll('input,select,button')].forEach(control => control.disabled = true);
            status.textContent = 'Verifying files before deletion…';
            try {
                const response = await sendAPI('POST','/duplicates/delete',duplicateApiKey,{}, {
                    token:group.token,keep_id:retained.id,delete_ids:ids,confirm:true
                });
                const result = (await response.json()).result;
                status.textContent = `Deleted ${result.deleted.length} file(s). Kept: ${result.kept}. ${result.errors.map(error => error.reason).join('; ')} Scan again for current results.`;
            } catch (error) {
                let message = 'Deletion failed. Scan again before retrying.';
                try { const body = await error.json(); if (body.error === 'InvalidKeyValue') message = body.result.value; } catch (_) {}
                status.textContent = message;
            }
        };
        box.append(remove,status); updateSelection();
    }
    for (const group of report.same_issue)
        section(`${group.title} #${group.issue_number}: different or unverified files — review editions before removing anything`, group.files);
    if (report.errors.length) {
        const warning = document.createElement('section');
        const title = document.createElement('h3');
        title.textContent = 'Files requiring review';
        warning.append(title);
        for (const error of report.errors) {
            const line = document.createElement('p');
            line.textContent = `${error.filepath}: ${error.reason}`;
            warning.append(line);
        }
        duplicateResults.append(warning);
    }
}
usingApiKey().then(apiKey => {
    duplicateApiKey = apiKey;
    document.querySelector('#duplicates-form').onsubmit = async event => {
        event.preventDefault();
        const button = event.target.querySelector('button');
        button.disabled = true;
        duplicateResults.replaceChildren();
        duplicateStatus.textContent = 'Checking library files…';
        const value = document.querySelector('#duplicates-volume').value;
        try {
            const response = await sendAPI('POST', '/duplicates/scan', apiKey, {}, {volume_id: value ? Number(value) : null});
            const json = await response.json();
            showDuplicateReport(json.result);
        } catch (error) {
            duplicateStatus.textContent = 'Scan failed. Check System → Logs and try a single volume.';
        } finally {
            button.disabled = false;
        }
    };
});
