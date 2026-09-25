const QEls = {
	queue: document.querySelector('#queue'),
	queue_entry: document.querySelector('.pre-build-els .queue-entry'),
    tool_bar: {
        remove_all: document.querySelector('#removeall-button')
    }
};

//
// Filling data
//
function addQueueEntry(api_key, obj) {
	const entry = QEls.queue_entry.cloneNode(true);
	entry.dataset.id = obj.id;
	QEls.queue.appendChild(entry);

	const title = entry.querySelector('a:first-of-type');
	title.innerText = obj.title;
	title.href = `${url_base}/volumes/${obj.volume_id}`;

	const source = entry.querySelector('td:nth-child(3) a')
    source.innerText =
		obj.source_name.charAt(0).toUpperCase() + obj.source_name.slice(1);
    source.href = obj.web_link;
    source.title = `Page Title:\n${obj.web_title}`;
    if (obj.web_sub_title !== null)
        source.title += `\n\nSub Section:\n${obj.web_sub_title}`;

	const index = [...QEls.queue.children].indexOf(entry);
	entry.querySelector('.move-up-dl').onclick = e => moveEntry(
		obj.id, index - 1, api_key
	);
	entry.querySelector('.move-down-dl').onclick = e => moveEntry(
		obj.id, index + 1, api_key
	);
	entry.querySelector('.remove-dl').onclick = e => deleteEntry(
        obj.id, api_key
    );
	entry.querySelector('.blocklist-dl').onclick = e => deleteEntry(
        obj.id,
        api_key,
        blocklist=true
    );

    entry.querySelector('.retry-import-dl').onclick = () => recoverEntry(obj.id, api_key, 'retry', entry);
    entry.querySelector('.forget-dl').onclick = () => {
        if (confirm('Remove this entry from Kapowarr only? The client job and all files will be kept.'))
            recoverEntry(obj.id, api_key, 'forget', entry);
    };
	updateQueueEntry(obj);
};

function updateQueueEntry(obj) {
	const tr = document.querySelector(`#queue > tr[data-id="${obj.id}"]`);
    if (!tr) return;
    tr.querySelector('.retry-import-dl').classList.toggle('hidden', !obj.can_retry);
    tr.querySelector('.forget-dl').classList.toggle('hidden', !obj.can_forget);
	tr.dataset.status = obj.status;
	tr.querySelector('td:nth-child(1)').innerText =
		obj.status.charAt(0).toUpperCase() + obj.status.slice(1) + (obj.error ? `: ${obj.error}` : '');
	tr.querySelector('td:nth-child(4)').innerText =
		convertSize(obj.size, 1);
	tr.querySelector('td:nth-child(5)').innerText =
		minDecimalPoints(Math.round(obj.speed / 100000) / 10, 2) + 'MB/s';
	tr.querySelector('td:nth-child(6)').innerText =
		obj.size === -1
			? convertSize(obj.progress, 1)
			: minDecimalPoints(Math.round(obj.progress * 10) / 10, 2) + '%';
};

function removeQueueEntry(id) {
	document.querySelector(`#queue > tr[data-id="${id}"]`).remove();
};

function fillQueue(api_key) {
	fetchAPI('/activity/queue', api_key)
	.then(json => {
		QEls.queue.innerHTML = '';
		json.result.forEach(obj => addQueueEntry(api_key, obj));
	})
};

//
// Actions
//
async function recoverEntry(id, api_key, action, entry) {
    const buttons = entry.querySelectorAll('.recovery-action');
    const error = entry.querySelector('.recovery-error');
    error.textContent = '';
    buttons.forEach(button => button.disabled = true);
    try {
        const response = await sendAPI('POST', `/activity/queue/${id}/recovery`, api_key, {}, {action});
        if (!response.ok) throw new Error('Recovery rejected');
        fillQueue(api_key);
    } catch (_) {
        error.textContent = 'Recovery could not be started. Refresh the queue and check its status.';
    } finally {
        buttons.forEach(button => button.disabled = false);
    }
}

function deleteAll(api_key) {
   sendAPI('DELETE', '/activity/queue', api_key);
};

function moveEntry(id, index, api_key) {
	sendAPI('PUT', `/activity/queue/${id}`, api_key, {
		index: index
	}, {})
	.then(response => {
		if (!response.ok)
			return;

		fillQueue(api_key);
	});
}

function deleteEntry(id, api_key, blocklist=false) {
	sendAPI('DELETE', `/activity/queue/${id}`, api_key, {}, {
        blocklist: blocklist
    });
};

// code run on load

usingApiKey()
.then(api_key => {
	fillQueue(api_key);
	socket.on('queue_added', data => addQueueEntry(api_key, data));
	socket.on('queue_status', updateQueueEntry);
	socket.on('queue_ended', data => removeQueueEntry(data.id));
    QEls.tool_bar.remove_all.onclick = e => deleteAll(api_key);
});
