const duplicateStatus = document.querySelector('#duplicates-status');
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
    }
    for (const group of report.exact)
        section(group.same_physical_file ? 'Identical content — paths refer to the same physical file (hard links)' : 'Identical file contents (SHA-256 verified)', group.files);
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
