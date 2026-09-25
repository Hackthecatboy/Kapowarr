const logOutput = document.querySelector('#logs-output');
const logStatus = document.querySelector('#logs-status');
const logFilter = document.querySelector('#filter-logs');
const logRefresh = document.querySelector('#refresh-logs');
const logDownload = document.querySelector('#download-logs');
const logAutoRefresh = document.querySelector('#auto-refresh-logs');
const logLevelFilter = document.querySelector('#filter-log-level');
const recordLogLevel = document.querySelector('#record-log-level');
const recordLogStatus = document.querySelector('#record-log-status');
const logPreferenceKey = `kapowarr.logs:${typeof url_base === 'string' ? url_base : ''}`;
let logText = '';
let logsLoading = false;

try {
    const preferences = JSON.parse(localStorage.getItem(logPreferenceKey) || '{}');
    logAutoRefresh.checked = preferences.autoRefresh === true;
    if (Array.from(logLevelFilter.options).some(option => option.value === preferences.level)) {
        logLevelFilter.value = preferences.level;
    }
    if (typeof preferences.filter === 'string') logFilter.value = preferences.filter;
} catch (_) { /* Storage may be unavailable or contain an older value. */ }

function saveLogPreferences() {
    try {
        localStorage.setItem(logPreferenceKey, JSON.stringify({
            autoRefresh: logAutoRefresh.checked, level: logLevelFilter.value, filter: logFilter.value
        }));
    } catch (_) { /* The viewer also works without browser storage. */ }
}

function renderLogs() {
    const term = logFilter.value.toLowerCase();
    // Keep multiline tracebacks with their parent record when filtering.
    const records = [];
    for (const line of logText.split('\n')) {
        const match = line.match(/^\d{4}-\d\d-\d\dT.*? \| .*? \| .*? \| .*? \| (DEBUG|INFO|WARNING|ERROR|CRITICAL) \| /);
        if (match || !records.length) records.push({level: match?.[1], lines: [line]});
        else records[records.length - 1].lines.push(line);
    }
    const text = records.filter(record =>
        (logLevelFilter.value === 'ALL' || record.level === logLevelFilter.value) &&
        record.lines.join('\n').toLowerCase().includes(term)
    ).map(record => record.lines.join('\n')).join('\n');
    const atBottom = logOutput.scrollHeight - logOutput.scrollTop - logOutput.clientHeight < 30;
    logOutput.textContent = text || (term || logLevelFilter.value !== 'ALL' ? 'No matching entries in the loaded logs.' : 'No log entries yet.');
    if (atBottom) logOutput.scrollTop = logOutput.scrollHeight;
}

async function refreshLogs(apiKey) {
    if (logsLoading) return;
    logsLoading = true;
    logRefresh.disabled = true;
    try {
        const json = await fetchAPI('/system/logs/recent', apiKey);
        logText = json.result.text;
        renderLogs();
        logStatus.textContent = `Updated ${new Date().toLocaleTimeString()}. ` +
            (json.result.truncated ? 'Showing the newest 1,000 lines or 256 KiB; older entries omitted.' : 'All retained entries loaded.');
    } catch (_) {
        logStatus.textContent = 'Could not load logs. Previously loaded entries are still displayed. Try Refresh.';
    } finally {
        logsLoading = false;
        logRefresh.disabled = false;
    }
}

logFilter.addEventListener('input', () => { saveLogPreferences(); renderLogs(); });
logLevelFilter.addEventListener('change', () => { saveLogPreferences(); renderLogs(); });
logAutoRefresh.addEventListener('change', saveLogPreferences);
usingApiKey().then(apiKey => {
    let savedRecordLevel;
    fetchAPI('/settings', apiKey).then(json => {
        savedRecordLevel = String(json.result.log_level);
        recordLogLevel.value = savedRecordLevel;
        recordLogLevel.disabled = false;
        recordLogStatus.textContent = 'Applies to new entries; saved for this server.';
    }).catch(() => {
        recordLogStatus.textContent = 'Could not load log level. Reload this page to retry.';
    });
    recordLogLevel.onchange = async () => {
        recordLogLevel.disabled = true;
        try {
            const response = await sendAPI('PUT', '/settings', apiKey, {}, {log_level: Number(recordLogLevel.value)});
            if (!response.ok) throw new Error('Save failed');
            savedRecordLevel = recordLogLevel.value;
            recordLogStatus.textContent = 'Saved. Repeat the action to capture new entries.';
            await refreshLogs(apiKey);
        } catch (_) {
            recordLogLevel.value = savedRecordLevel;
            recordLogStatus.textContent = 'Could not save log level. Previous setting restored.';
        } finally {
            recordLogLevel.disabled = false;
        }
    };
    logRefresh.onclick = () => refreshLogs(apiKey);
    logDownload.onclick = async () => {
        logDownload.disabled = true;
        try {
            const response = await fetchAPI('/system/logs', apiKey, {}, false);
            if (!response.ok) throw new Error('Download failed');
            const url = URL.createObjectURL(await response.blob());
            const link = document.createElement('a');
            link.href = url;
            link.download = 'Kapowarr-logs.txt';
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (_) {
            logStatus.textContent = 'Could not download logs. Try again.';
        } finally {
            logDownload.disabled = false;
        }
    };
    refreshLogs(apiKey);
    const timer = setInterval(() => {
        if (!document.hidden && logAutoRefresh.checked) refreshLogs(apiKey);
    }, 5000);
    window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
});
