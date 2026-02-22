
async function loadTasksContent() {
    const contentArea = document.getElementById('contentArea');
    contentArea.innerHTML = `
                <div class="flex items-center justify-center h-full text-gray-500">
                    <i data-lucide="loader-2" class="w-5 h-5 animate-spin mr-2"></i> Loading tasks...
                </div>`;
    lucide.createIcons();

    try {
        let url = '/api/tasks';
        if (currentClient && currentClient.id) {
            url += `?campaign_id=${currentClient.id}`;
        }

        const response = await fetch(url);
        const data = await response.json();
        const tasks = data.tasks || [];

        if (tasks.length === 0) {
            contentArea.innerHTML = `
                        <div class="flex flex-col items-center justify-center h-64 text-center">
                            <i data-lucide="check-square" class="w-12 h-12 text-gray-600 mb-4"></i>
                            <h3 class="text-lg font-semibold text-white mb-2">No tasks found</h3>
                            <p class="text-gray-400 mb-4">Run an audit to generate tasks automatically.</p>
                        </div>`;
        } else {
            contentArea.innerHTML = `
                        <div class="space-y-6">
                            <div class="flex items-center justify-between">
                                <h2 class="text-xl font-semibold text-white">Tasks</h2>
                                <div class="flex gap-2">
                                     <span class="bg-gray-800 text-gray-300 px-3 py-1 rounded-lg text-sm">${tasks.length} Total</span>
                                </div>
                            </div>

                            <div class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
                                <table class="w-full">
                                    <thead class="bg-gray-900/50 text-xs uppercase text-gray-400 border-b border-gray-800">
                                        <tr>
                                            <th class="px-6 py-4 text-left font-medium">Task</th>
                                            <th class="px-6 py-4 text-left font-medium">Type</th>
                                            <th class="px-6 py-4 text-left font-medium">Role</th>
                                            <th class="px-6 py-4 text-left font-medium">Priority</th>
                                            <th class="px-6 py-4 text-left font-medium">Status</th>
                                        </tr>
                                    </thead>
                                    <tbody class="divide-y divide-gray-800 text-sm">
                                        ${tasks.map(t => `
                                            <tr class="hover:bg-gray-800/30 transition-colors">
                                                <td class="px-6 py-4">
                                                    <div class="font-medium text-white">${t.title}</div>
                                                    <div class="text-gray-500 text-xs mt-1">${t.description}</div>
                                                </td>
                                                <td class="px-6 py-4">
                                                    <span class="px-2 py-1 rounded text-xs bg-gray-800 text-gray-300 border border-gray-700">
                                                        ${t.type}
                                                    </span>
                                                </td>
                                                <td class="px-6 py-4 text-gray-400 capitalize">${t.assigned_role?.replace('_', ' ') || '-'}</td>
                                                 <td class="px-6 py-4">
                                                    <span class="px-2 py-1 rounded text-xs ${t.priority === 'high' || t.priority === 'critical' ? 'bg-red-500/10 text-red-400 border border-red-500/20' :
                    t.priority === 'medium' ? 'bg-orange-500/10 text-orange-400 border border-orange-500/20' :
                        'bg-blue-500/10 text-blue-400 border border-blue-500/20'
                }">
                                                        ${t.priority}
                                                    </span>
                                                </td>
                                                <td class="px-6 py-4">
                                                    <select onchange="updateTaskStatus('${t.id}', this.value)" class="bg-gray-900 border border-gray-700 text-gray-300 text-xs rounded px-2 py-1 focus:outline-none focus:border-violet-500">
                                                        <option value="pending" ${t.status === 'pending' ? 'selected' : ''}>Pending</option>
                                                        <option value="in_progress" ${t.status === 'in_progress' ? 'selected' : ''}>In Progress</option>
                                                        <option value="done" ${t.status === 'done' ? 'selected' : ''}>Done</option>
                                                    </select>
                                                </td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            </div>
                        </div>`;
        }
    } catch (error) {
        contentArea.innerHTML = `<div class="text-red-400 p-8 text-center">Error loading tasks: ${error.message}</div>`;
    }
    lucide.createIcons();
}

async function updateTaskStatus(taskId, newStatus) {
    try {
        await fetch(`/api/tasks/${taskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus })
        });
    } catch (e) {
        console.error(e);
        alert('Failed to update status');
    }
}

async function generateSlides(auditId) {
    const btn = document.getElementById(`btn-slides-${auditId}`);
    if (btn) {
        btn.innerHTML = `<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i> Generating...`;
        btn.disabled = true;
        lucide.createIcons();
    }

    try {
        const response = await fetch(`/api/audits/${auditId}/generate-slides`, { method: 'POST' });
        const data = await response.json();

        if (!response.ok) throw new Error(data.error || 'Failed to generate slides');

        // Update local model
        if (activeAudit && activeAudit.id === auditId) {
            activeAudit.slides_url = data.slides_url;
        }

        // Re-render header to show specific view button
        if (activeAudit) {
            renderAuditDetail();
        }

        window.open(data.slides_url, '_blank');

    } catch (error) {
        alert('Error: ' + error.message);
        if (btn) {
            btn.innerHTML = `<i data-lucide="presentation" class="w-4 h-4"></i> Generate Slides`;
            btn.disabled = false;
            lucide.createIcons();
        }
    }
}

async function exportAudit(auditId) {
    try {
        const btn = event.currentTarget;
        const originalText = btn.innerHTML;
        btn.innerHTML = `<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i> Exporting...`;
        btn.disabled = true;
        lucide.createIcons();

        const response = await fetch(`/api/audits/${auditId}/export`);

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.error || 'Export failed');
        }

        // Trigger download
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;

        // Try to get filename from header
        const contentDisposition = response.headers.get('Content-Disposition');
        let filename = 'audit_report.xlsx';
        if (contentDisposition) {
            const filenameMatch = contentDisposition.match(/filename="?([^"]+)"?/);
            if (filenameMatch.length === 2)
                filename = filenameMatch[1];
        }

        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        btn.innerHTML = originalText;
        btn.disabled = false;
        lucide.createIcons();

    } catch (error) {
        console.error("Export error:", error);
        alert("Failed to export: " + error.message);
        // Reset button state if available in scope, heavily context dependent
        // Reloading content is safest to reset UI state
        // loadContent(); 
    }
}

function renderAuditDetail() {
    const contentArea = document.getElementById('contentArea');
    const results = activeAudit.results || {};
    const summary = results.summary || activeAudit.summary || {};
    const categorized = results.categorized || null; // New structure

    // Back button and header
    const pageActions = document.getElementById('pageActions');
    pageActions.innerHTML = `
                <div class="flex items-center gap-3">
                    <button onclick="activeAudit=null; loadContent()" class="text-gray-400 hover:text-white text-sm flex items-center gap-1">
                        <i data-lucide="arrow-left" class="w-4 h-4"></i> Back to Audits
                    </button>
                    <div class="h-4 w-px bg-gray-700"></div>
                    <span class="text-sm text-gray-400">Viewing Audit: ${new Date(activeAudit.created_at).toLocaleDateString()}</span>
                    
                    <button onclick="exportAudit('${activeAudit.id}')" class="bg-gray-800 hover:bg-gray-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1.5 ml-auto border border-gray-700 transition-colors">
                        <i data-lucide="download" class="w-4 h-4"></i> Export Excel
                    </button>

                    ${activeAudit.slides_url ?
            `<a href="${activeAudit.slides_url}" target="_blank" class="bg-violet-600 hover:bg-violet-500 text-white px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1.5 ml-2">
                            <i data-lucide="presentation" class="w-4 h-4"></i> View Slides
                        </a>` :
            `<button id="btn-slides-${activeAudit.id}" onclick="generateSlides('${activeAudit.id}')" class="bg-gray-800 hover:bg-gray-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1.5 ml-2 border border-gray-700">
                             <i data-lucide="presentation" class="w-4 h-4"></i> Generate Slides
                         </button>`
        }
                </div>`;

    if (!categorized) {
        // Fallback for old audits or incomplete data
        contentArea.innerHTML = `
                    <div class="text-center py-12">
                        <i data-lucide="alert-circle" class="w-12 h-12 text-yellow-500 mx-auto mb-4"></i>
                        <h3 class="text-lg font-semibold text-white mb-2">Legacy Audit Format</h3>
                        <p class="text-gray-400 mb-6">This audit uses an older data format. Please run a new audit to see the detailed 3-column breakdown.</p>
                        <button onclick="activeAudit=null; runAudit()" class="bg-violet-600 hover:bg-violet-500 text-white px-4 py-2 rounded-lg text-sm font-medium">
                            Run New Audit
                        </button>
                    </div>`;
        lucide.createIcons();
        return;
    }

    // Helper to render a category column
    const renderCategoryColumn = (title, icon, itemsKey) => {
        const categoryData = categorized[itemsKey] || {};
        const checks = Object.entries(categoryData);

        return `
                    <div class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden flex flex-col h-full">
                        <div class="p-4 border-b border-gray-800 flex items-center gap-3 bg-gray-800/50">
                            <div class="p-2 bg-gray-800 rounded-lg">
                                <i data-lucide="${icon}" class="w-5 h-5 text-gray-400"></i>
                            </div>
                            <h3 class="font-semibold text-white">${title}</h3>
                        </div>
                        <div class="p-4 space-y-3 flex-1">
                            ${checks.length > 0 ? checks.map(([key, data]) => {
            const label = key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
            const isPass = data.status === 'pass' || data.issues === 0;
            const hasIssues = data.issues > 0;
            const statusColor = hasIssues ? 'text-red-400' : 'text-emerald-400';
            const iconName = hasIssues ? 'alert-circle' : 'check-circle';

            return `
                                    <div class="bg-black/20 rounded-lg p-3 border border-gray-800 hover:border-gray-700 transition-colors">
                                        <div class="flex items-center justify-between mb-1">
                                            <span class="text-sm font-medium text-gray-300">${label}</span>
                                            <i data-lucide="${iconName}" class="w-4 h-4 ${statusColor}"></i>
                                        </div>
                                        <div class="flex items-center justify-between">
                                            <span class="text-xs text-gray-500">${data.items?.length || 0} items</span>
                                            ${data.score !== undefined ? `<span class="text-xs font-mono text-gray-400">Score: ${data.score}</span>` : ''}
                                            ${hasIssues ? `<span class="text-xs bg-red-500/10 text-red-400 px-1.5 py-0.5 rounded">${data.issues} issues</span>` : ''}
                                        </div>
                                        ${data.items && data.items.length > 0 ? `
                                            <div class="mt-2 pt-2 border-t border-gray-800/50">
                                                <div class="text-xs text-gray-500 font-mono truncate cursor-pointer hover:text-gray-300" title="${data.items[0]}">
                                                    ${data.items[0]}
                                                </div>
                                                ${data.items.length > 1 ? `<div class="text-[10px] text-gray-600 mt-0.5">+${data.items.length - 1} more</div>` : ''}
                                            </div>
                                        ` : ''}
                                    </div>
                                `;
        }).join('') : '<div class="text-gray-500 text-sm text-center py-4">No data available</div>'}
                        </div>
                    </div>
                `;
    };

    contentArea.innerHTML = `
                <div class="grid grid-cols-1 md:grid-cols-3 gap-6 pb-8">
                    ${renderCategoryColumn('Architecture', 'server', 'architecture')}
                    ${renderCategoryColumn('Accessibility', 'accessibility', 'accessibility')}
                    ${renderCategoryColumn('Usability', 'mouse-pointer-click', 'usability')}
                </div>
            `;

    lucide.createIcons();
}

// Start
init();
