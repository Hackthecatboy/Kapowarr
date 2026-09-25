const logOutput = document.querySelector('#logs-output');
const logStatus = document.querySelector('#logs-status');
const logFilter = document.querySelector('#filter-logs');
const logRefresh = document.querySelector('#refresh-logs');
const logDownload = document.querySelector('#download-logs');
let logText = '';
let logsLoading = false;

function renderLogs() {
    const term = logFilter.value.toLowerCase();
    const text = logText.split('\n').filter(line => line.toLowerCase().includes(term)).join('\n');
    const atBottom = logOutput.scrollHeight - logOutput.scrollTop - logOutput.clientHeight < 30;
    logOutput.textContent = text || (term ? 'No matching lines in the loaded logs.' : 'No log entries yet.');
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

logFilter.addEventListener('input', renderLogs);
usingApiKey().then(apiKey => {
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
        if (!document.hidden && document.querySelector('#auto-refresh-logs').checked) refreshLogs(apiKey);
    }, 5000);
    window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
});
