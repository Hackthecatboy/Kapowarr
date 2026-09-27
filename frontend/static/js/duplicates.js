const duplicateStatus = document.querySelector('#duplicates-status');
let duplicateApiKey;
const duplicateResults = document.querySelector('#duplicates-results');
function showDuplicateReport(report) {
    duplicateResults.replaceChildren();
    duplicateStatus.textContent = `Checked ${report.scanned_files} indexed files. ${report.exact.length} identical groups; ${report.same_issue.length} other same-issue groups. ${report.limited ? 'Incomplete scan: limit reached. Select one volume to narrow the scan.' : ''}`;
    const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});
    function parts(file) {
        const split = file.filepath.lastIndexOf('/');
        const name = file.filepath.slice(split + 1);
        const suffix = name.match(/^(.*) \((\d+)\)(\.[^.]+)$/);
        return {folder: file.filepath.slice(0, split), name,
            base: suffix ? suffix[1] + suffix[3] : name,
            copy: suffix ? Number(suffix[2]) : 0};
    }
    function compareFiles(a, b) {
        const left = parts(a), right = parts(b);
        return collator.compare(left.folder, right.folder)
            || collator.compare(left.base, right.base)
            || left.copy - right.copy
            || collator.compare(left.name, right.name)
            || a.id - b.id;
    }
    function section(title, files, description) {
        const box = document.createElement('details');
        box.className = 'duplicate-group';
        const heading = document.createElement('summary');
        heading.textContent = title;
        box.append(heading);
        const note = document.createElement('p');
        note.textContent = description;
        box.append(note);
        const folder = parts(files[0]).folder;
        const sharedFolder = files.every(file => parts(file).folder === folder);
        if (sharedFolder) {
            const path = document.createElement('p');
            path.className = 'duplicate-folder';
            path.textContent = folder + '/';
            box.append(path);
        }
        const list = document.createElement('div');
        list.className = 'duplicate-files';
        box.append(list);
        duplicateResults.append(box);
        return {box, list, sharedFolder};
    }
    const exact = report.exact.map(group => ({...group, files: [...group.files].sort(compareFiles)}))
        .sort((a, b) => compareFiles(a.files[0], b.files[0]));
    const sameIssue = [...report.same_issue].sort((a, b) =>
        collator.compare(a.title, b.title) || a.volume_id - b.volume_id
        || collator.compare(String(a.issue_number), String(b.issue_number)))
        .map(group => ({...group, manualReview: true, files: [...group.files].sort(compareFiles)}));
    for (const [index, group] of [...exact, ...sameIssue].entries()) {
        const {box, list, sharedFolder} = section(
            group.manualReview
                ? `${group.title} #${group.issue_number} — ${group.files.length} files to review`
                : `${parts(group.files[0]).base} — ${group.files.length} identical copies`, group.files,
            group.manualReview
                ? 'Different or unverified editions. Review the files and choose which to keep; format and size do not prove quality or identical pages.'
                : group.same_physical_file
                ? 'SHA-256 verified. These paths refer to the same physical file (hard links).'
                : 'Identical file contents (SHA-256 verified).');
        const size = document.createElement('p');
        size.textContent = `${group.files[0].size.toLocaleString()} bytes per copy`;
        if (!group.manualReview) box.append(size);
        let keepId = group.manualReview ? null : group.files[0].id;
        const checks = [];
        for (const file of group.files) {
            const row = document.createElement('div');
            row.className = 'duplicate-file';
            if (group.token) {
                const keepLabel = document.createElement('label');
                const keep = document.createElement('input');
                keep.type = 'radio'; keep.name = `duplicate-keep-${index}`;
                keep.value = file.id; keep.checked = file.id === keepId;
                keep.setAttribute('aria-label', `Keep ${file.filepath}`);
                keep.onchange = () => { keepId = file.id; updateSelection(); };
                keepLabel.append(keep, ' Keep');
                const deleteLabel = document.createElement('label');
                const check = document.createElement('input');
                check.type = 'checkbox'; check.value = file.id;
                check.setAttribute('aria-label', `Delete ${file.filepath}`);
                deleteLabel.append(check, ' Delete');
                row.append(keepLabel, deleteLabel);
                checks.push(check);
            }
            const name = document.createElement('span');
            name.className = 'duplicate-filename';
            name.textContent = sharedFolder ? parts(file).name : file.filepath;
            if (group.manualReview) name.textContent += ` (${(file.size / 1024 / 1024).toFixed(2)} MiB · ${file.size.toLocaleString()} bytes)`;
            name.title = file.filepath;
            row.append(name);
            list.append(row);
        }
        if (!group.token) continue;
        const remove = document.createElement('button');
        remove.type = 'button'; remove.textContent = group.manualReview ? 'Delete Selected Editions' : 'Delete Selected Duplicates';
        const status = document.createElement('p'); status.setAttribute('role','status');
        function updateSelection() {
            for (const check of checks) {
                check.disabled = Number(check.value) === keepId;
                if (check.disabled) check.checked = false;
            }
            remove.disabled = keepId === null || !checks.some(check => check.checked);
        }
        checks.forEach(check => check.onchange = updateSelection);
        remove.onclick = async () => {
            const ids = checks.filter(check => check.checked).map(check => Number(check.value));
            const retained = group.files.find(file => file.id === keepId);
            const paths = group.files.filter(file => ids.includes(file.id)).map(file => file.filepath);
            const warning = group.manualReview ? 'These files are NOT verified identical. Different pages, extras or quality may be lost. You are choosing which edition to retain.\n\n' : '';
            if (!retained || !ids.length || !confirm(`${warning}Permanently delete these library copies?\n\n${paths.join('\n')}\n\nKEEP: ${retained.filepath}\n\nFiles will be checked again before deletion.`)) return;
            [...box.querySelectorAll('input,select,button')].forEach(control => control.disabled = true);
            status.textContent = 'Verifying files before deletion…';
            try {
                const response = await sendAPI('POST','/duplicates/delete',duplicateApiKey,{}, {
                    token:group.token,keep_id:retained.id,delete_ids:ids,confirm:true,reviewed_different:!!group.manualReview
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
