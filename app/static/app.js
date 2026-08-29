// DOM Elements
const stepInput = document.getElementById('step-input');
const stepExecution = document.getElementById('step-execution');
const stepResults = document.getElementById('step-results');

const requirementsInput = document.getElementById('requirements');
const buggyCodeInput = document.getElementById('buggyCode');
const startBtn = document.getElementById('startBtn');

const logContainer = document.getElementById('logContainer');

const resultTitle = document.getElementById('resultTitle');
const healedCode = document.getElementById('healedCode');
const downloadPdfBtn = document.getElementById('downloadPdfBtn');
const copyBtn = document.getElementById('copyBtn');
const resetBtn = document.getElementById('resetBtn');

// Global State
let latestResponseData = null;
let savedRequirements = '';
let savedBuggyCode = '';

// Helper: Escape HTML special characters
function escapeHtml(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// Helper: Show requested step section and hide others
function showStep(stepId) {
    const steps = ['step-input', 'step-execution', 'step-results'];
    steps.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            if (id === stepId) {
                el.classList.remove('hidden');
            } else {
                el.classList.add('hidden');
            }
        }
    });
}

// Helper: Append log line to terminal logContainer
function appendLog(stepName, message, timestamp) {
    const timeStr = timestamp 
        ? new Date(timestamp).toLocaleTimeString() 
        : new Date().toLocaleTimeString();

    const logDiv = document.createElement('div');
    logDiv.className = 'py-1 border-b border-slate-900 last:border-0 font-mono text-xs';

    const header = document.createElement('div');
    header.className = 'flex items-center gap-2 mb-0.5 text-slate-400';
    header.innerHTML = `<span class="text-slate-500">[${timeStr}]</span> <span class="font-bold text-emerald-400">[${escapeHtml(stepName)}]</span>`;

    const msg = document.createElement('pre');
    msg.className = 'text-slate-200 whitespace-pre-wrap break-words pl-2 font-mono';
    msg.textContent = message;

    logDiv.appendChild(header);
    logDiv.appendChild(msg);
    logContainer.appendChild(logDiv);
    logContainer.scrollTop = logContainer.scrollHeight;
}

// Event Listener: Run AutoFix Button
startBtn.addEventListener('click', async (e) => {
    e.preventDefault();

    const requirements = requirementsInput.value.trim();
    const buggyCode = buggyCodeInput.value.trim();

    if (!requirements || !buggyCode) {
        alert('Please provide both functional requirements and buggy Python code.');
        return;
    }

    // Save inputs for report generation
    savedRequirements = requirements;
    savedBuggyCode = buggyCode;

    // Transition to execution view
    showStep('step-execution');
    logContainer.innerHTML = '';
    appendLog('INIT', 'Initializing Multi-Agent Sandbox...');

    try {
        const response = await fetch('/api/heal', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                requirements: requirements,
                buggy_code: buggyCode,
            }),
        });

        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.detail || `Server returned error status ${response.status}`);
        }

        const data = await response.json();
        latestResponseData = data;

        // Append all response logs
        if (data.logs && data.logs.length > 0) {
            data.logs.forEach(entry => {
                appendLog(entry.step_name, entry.message, entry.timestamp);
            });
        }

        // Set Healed Code
        healedCode.textContent = data.final_code || '# No healed code returned';
        if (window.hljs) {
            hljs.highlightElement(healedCode);
        }

        // Update Results Title
        if (data.success) {
            resultTitle.textContent = `Pipeline Complete — Passed in ${data.iterations_used} iteration(s)`;
            resultTitle.className = 'text-lg font-semibold tracking-tight text-emerald-600';
        } else {
            resultTitle.textContent = `Pipeline Complete — Unresolved after ${data.iterations_used} iteration(s)`;
            resultTitle.className = 'text-lg font-semibold tracking-tight text-amber-600';
        }

        // Wait 1.5 seconds for UX so the user can see final logs
        setTimeout(() => {
            showStep('step-results');
        }, 1500);

    } catch (err) {
        console.error('Pipeline execution error:', err);
        appendLog('ERROR', `Pipeline execution failed: ${err.message}`);
        
        resultTitle.textContent = 'Pipeline Failed';
        resultTitle.className = 'text-lg font-semibold tracking-tight text-rose-600';
        healedCode.textContent = `# Error occurred during healing:\n# ${err.message}`;
        if (window.hljs) {
            hljs.highlightElement(healedCode);
        }

        setTimeout(() => {
            showStep('step-results');
        }, 1500);
    }
});

// Event Listener: Copy Code Button
copyBtn.addEventListener('click', async () => {
    const code = healedCode.textContent;
    if (!code) return;

    try {
        await navigator.clipboard.writeText(code);
        const originalContent = copyBtn.innerHTML;
        copyBtn.innerHTML = '<i class="fa-solid fa-check text-emerald-600"></i><span>Copied!</span>';
        setTimeout(() => {
            copyBtn.innerHTML = originalContent;
        }, 2000);
    } catch (err) {
        console.error('Failed to copy code to clipboard:', err);
    }
});

// Event Listener: Reset / Start New Button
resetBtn.addEventListener('click', () => {
    requirementsInput.value = '';
    buggyCodeInput.value = '';
    latestResponseData = null;
    savedRequirements = '';
    savedBuggyCode = '';
    showStep('step-input');
});

// Event Listener: Download PDF Report using html2pdf.js
downloadPdfBtn.addEventListener('click', () => {
    if (!latestResponseData) {
        alert('No report data available to download.');
        return;
    }

    // Format logs for PDF
    const logsHtml = (latestResponseData.logs || [])
        .map(entry => {
            const time = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : '';
            return `<div style="margin-bottom: 6px; padding-bottom: 6px; border-bottom: 1px solid #1e293b;">
                <span style="color: #64748b;">[${time}]</span> 
                <strong style="color: #38bdf8;">[${escapeHtml(entry.step_name)}]</strong>
                <pre style="margin: 4px 0 0 0; white-space: pre-wrap; font-family: monospace; color: #e2e8f0; font-size: 10px;">${escapeHtml(entry.message)}</pre>
            </div>`;
        })
        .join('');

    // Create dynamic container for PDF generation
    const hiddenDiv = document.createElement('div');
    hiddenDiv.style.padding = '24px';
    hiddenDiv.style.fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    hiddenDiv.style.color = '#0f172a';
    hiddenDiv.style.backgroundColor = '#ffffff';

    hiddenDiv.innerHTML = `
        <div style="border-bottom: 2px solid #0f172a; padding-bottom: 12px; margin-bottom: 20px;">
            <h1 style="font-size: 22px; font-weight: bold; margin: 0 0 4px 0; color: #0f172a;">AutoFix Execution Report</h1>
            <p style="font-size: 11px; color: #64748b; margin: 0;">
                Generated: ${new Date().toLocaleString()} &bull; 
                Status: <strong>${latestResponseData.success ? 'PASSED ✅' : 'FAILED ❌'}</strong> &bull; 
                Iterations: ${latestResponseData.iterations_used}
            </p>
        </div>

        <h2 style="font-size: 14px; font-weight: bold; margin: 14px 0 6px 0; color: #1e293b; text-transform: uppercase; letter-spacing: 0.05em;">Requirements</h2>
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; font-size: 11px; white-space: pre-wrap; color: #334155;">${escapeHtml(savedRequirements)}</div>

        <h2 style="font-size: 14px; font-weight: bold; margin: 14px 0 6px 0; color: #1e293b; text-transform: uppercase; letter-spacing: 0.05em;">Original Code</h2>
        <pre style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; font-size: 11px; font-family: monospace; white-space: pre-wrap; color: #0f172a;">${escapeHtml(savedBuggyCode)}</pre>

        <h2 style="font-size: 14px; font-weight: bold; margin: 14px 0 6px 0; color: #1e293b; text-transform: uppercase; letter-spacing: 0.05em;">Healed Code</h2>
        <pre style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; font-size: 11px; font-family: monospace; white-space: pre-wrap; color: #0f172a;">${escapeHtml(latestResponseData.final_code || '')}</pre>

        <h2 style="font-size: 14px; font-weight: bold; margin: 14px 0 6px 0; color: #1e293b; text-transform: uppercase; letter-spacing: 0.05em;">Execution Logs</h2>
        <div style="background-color: #020617; border-radius: 6px; padding: 12px; font-size: 10px; font-family: monospace;">
            ${logsHtml || '<div style="color: #64748b;">No logs recorded.</div>'}
        </div>
    `;

    // Configure and invoke html2pdf
    const options = {
        margin: 10,
        filename: 'AutoFix_Report.pdf',
        html2canvas: { scale: 2 },
        jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
    };

    if (window.html2pdf) {
        html2pdf().set(options).from(hiddenDiv).save();
    } else {
        console.error('html2pdf library is not loaded');
        alert('PDF generator library failed to load. Please try again.');
    }
});
