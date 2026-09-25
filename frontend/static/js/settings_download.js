const preferenceFields = {
    download_min_size_mb: 'number', download_max_size_mb: 'number',
    download_preferred_formats: 'list', download_preferred_terms: 'list',
    download_excluded_terms: 'list'
};
const preferenceInput = key => document.querySelector('#' + key.replaceAll('_', '-') + '-input');

function fillSettings(api_key) {
	fetchAPI('/settings', api_key)
	.then(json => {
        for (const [key, type] of Object.entries(preferenceFields))
            preferenceInput(key).value = type === 'list' ? (json.result[key] || []).join(', ') : (json.result[key] || 0);
		document.querySelector('#download-folder-input').value = json.result.download_folder;
		document.querySelector('#concurrent-direct-downloads-input').value = json.result.concurrent_direct_downloads;
		document.querySelector('#download-timeout-input').value = ((json.result.failing_download_timeout || 0) / 60) || '';
		document.querySelector('#seeding-handling-input').value = json.result.seeding_handling;
		document.querySelector('#delete-downloads-input').checked = json.result.delete_completed_downloads;
	});
};

function saveSettings(api_key) {
    if (!document.querySelector('#settings-form').reportValidity()) return;
    document.querySelector('#preference-settings-error').textContent = '';
    Object.keys(preferenceFields).forEach(key => preferenceInput(key).classList.remove('error-input'));
	document.querySelector("#save-button p").innerText = 'Saving';
	document.querySelector('#download-folder-input').classList.remove('error-input');
	const data = {
		'download_folder': document.querySelector('#download-folder-input').value,
		'concurrent_direct_downloads': parseInt(document.querySelector('#concurrent-direct-downloads-input').value),
		'failing_download_timeout': parseInt(document.querySelector('#download-timeout-input').value || 0) * 60,
		'seeding_handling': document.querySelector('#seeding-handling-input').value,
		'delete_completed_downloads': document.querySelector('#delete-downloads-input').checked
	};
    for (const [key, type] of Object.entries(preferenceFields)) {
        const value = preferenceInput(key).value;
        data[key] = type === 'list' ? value.split(',').map(term => term.trim()).filter(Boolean) : Number(value || 0);
    }
	sendAPI('PUT', '/settings', api_key, {}, data)
	.then(response => 
		document.querySelector("#save-button p").innerText = 'Saved'
	)
	.catch(e => {
		document.querySelector("#save-button p").innerText = 'Failed';
        e.json().then(e => {
            if (e.error === 'InvalidKeyValue' && e.result.key in preferenceFields) {
                preferenceInput(e.result.key).classList.add('error-input');
                document.querySelector('#preference-settings-error').textContent = `Invalid ${e.result.key.replaceAll('_', ' ')}: ${e.result.value}`;
                return;
            }
            if (
                e.error === "InvalidKeyValue"
                && e.result.key === "download_folder"
                ||
                e.error === "FolderNotFound"
            )
                document.querySelector('#download-folder-input').classList.add('error-input');

			else
                console.log(e);
        });
	});
};

//
// Empty download folder
//
function emptyFolder(api_key) {
	sendAPI('DELETE', '/activity/folder', api_key)
	.then(response => {
		document.querySelector('#empty-download-folder').innerText = 'Done';
	});
};

// code run on load
usingApiKey()
.then(api_key => {
	fillSettings(api_key);

	document.querySelector('#save-button').onclick = e => saveSettings(api_key);
	document.querySelector('#empty-download-folder').onclick = e => emptyFolder(api_key);
});
