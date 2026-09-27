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
    if (document.querySelector(`#queue > tr[data-id="${obj.id}"]`)) { updateQueueEntry(obj); return; }
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
    if (obj.discovered) {
        title.removeAttribute('href');
        source.removeAttribute('href');
        entry.classList.add('discovered-torrent');
        entry.querySelectorAll('.move-up-dl,.move-down-dl,.remove-dl,.blocklist-dl').forEach(button => button.hidden = true);
    }
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
    const review = tr.querySelector('.review-torrent-dl');
    review.classList.toggle('hidden', !obj.discovered && !obj.can_review);
    review.onclick = () => usingApiKey().then(api_key => reviewTorrent(obj, api_key, tr));
    review.disabled = !obj.can_review || tr.dataset.reviewBusy === 'true';
    tr.querySelector('.retry-import-dl').classList.toggle('hidden', !obj.can_retry);
    tr.querySelector('.forget-dl').classList.toggle('hidden', !obj.can_forget);
	tr.dataset.status = obj.status;
	tr.querySelector('td:nth-child(1)').innerText =
		obj.status.charAt(0).toUpperCase() + obj.status.slice(1) + (obj.error ? `: ${obj.error}` : obj.status_detail ? `: ${obj.status_detail}` : '');
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
	document.querySelector(`#queue > tr[data-id="${id}"]`)?.remove();
};

let queueRefreshPending = false;
async function fillQueue(api_key) {
    if (queueRefreshPending) return;
    queueRefreshPending = true;
    try {
        const json = await fetchAPI('/activity/queue', api_key);
        let discovered = [];
        const notice = document.querySelector('#discovery-status');
        try {
            const snapshot = await fetchAPI('/activity/queue/discovered', api_key);
            discovered = snapshot.result.downloads;
            notice.textContent = snapshot.result.errors.join(' ');
        } catch (_) {
            notice.textContent = 'Could not refresh category downloads. The next refresh will retry.';
        }
        QEls.tool_bar.remove_all.disabled = !json.result.length;
        const jobs = [...json.result, ...discovered];
        const ids = new Set(jobs.map(obj => String(obj.id)));
        for (const entry of [...QEls.queue.children])
            if (!ids.has(entry.dataset.id)) entry.remove();
        jobs.forEach((obj, index) => {
            addQueueEntry(api_key, obj);
            const entry = document.querySelector(`#queue > tr[data-id="${obj.id}"]`);
            QEls.queue.appendChild(entry);
            entry.querySelector('.move-up-dl').onclick = () => moveEntry(obj.id, index - 1, api_key);
            entry.querySelector('.move-down-dl').onclick = () => moveEntry(obj.id, index + 1, api_key);
            if (!obj.discovered) {
                entry.querySelector('.move-up-dl').hidden = index === 0;
                entry.querySelector('.move-down-dl').hidden = index === json.result.length - 1;
            }
        });
    } catch (error) {
        console.warn('Queue refresh failed; will retry');
    } finally {
        queueRefreshPending = false;
    }
};

//
// Actions
//
async function reviewTorrent(obj, api_key, entry) {
    const button = entry.querySelector('.review-torrent-dl');
    const error = entry.querySelector('.recovery-error');
    entry.dataset.reviewBusy = 'true';
    button.disabled = true;
    error.textContent = 'Scanning completed files for review…';
    try {
        const response = await sendAPI('POST', '/activity/queue/discovered/review', api_key, {}, {
            client_id: obj.client_id, info_hash: obj.torrent_hash
        });
        if (!response.ok) throw response;
        const body = await response.json();
        if (!body.result || typeof body.result.folder !== 'string' || !Array.isArray(body.result.items))
            throw new Error('Incomplete inbox scan response');
        window.location.assign(`${url_base}/pack-inbox`);
    } catch (failure) {
        let message = 'Could not scan this torrent. Check its completion state, mounts and remote mappings.';
        try { const body = await failure.json(); if (body.error === 'InvalidKeyValue') message = String(body.result.value); } catch (_) {}
        error.textContent = message;
        entry.dataset.reviewBusy = 'false';
        button.disabled = false;
    }
}

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
    socket.on('connect', () => fillQueue(api_key));
    setInterval(() => { if (!document.hidden) fillQueue(api_key); }, 5000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) fillQueue(api_key); });
	socket.on('queue_ended', data => removeQueueEntry(data.id));
    QEls.tool_bar.remove_all.onclick = e => deleteAll(api_key);
});
