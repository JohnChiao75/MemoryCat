// ---------------------- 多语言引擎 ----------------------
let langDict = {};
let currentLang = 'en';

function loadLanguage() {
    return fetch('/api/lang')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                langDict = data.data.dict;
                currentLang = data.data.current;
                // 填充下拉
                const select = document.getElementById('langSelect');
                select.innerHTML = '';
                data.data.available.forEach(lang => {
                    const opt = document.createElement('option');
                    opt.value = lang;
                    opt.textContent = lang;
                    if (lang === currentLang) opt.selected = true;
                    select.appendChild(opt);
                });
                // 开关
                document.getElementById('langP').checked = data.data.extensions.p || false;
                document.getElementById('langD').checked = data.data.extensions.d || false;
                // 翻译所有元素
                translatePage();
            } else {
                console.error('加载语言失败', data.error);
            }
        });
}

function translatePage() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (langDict[key] !== undefined) {
            if (el.tagName === 'INPUT' && el.type === 'text') {
                el.placeholder = langDict[key];
            } else if (el.tagName === 'TEXTAREA') {
                el.placeholder = langDict[key];
            } else {
                el.textContent = langDict[key];
            }
        }
    });
    if (langDict['title']) {
        document.title = langDict['title'];
    }
}

function saveLanguage() {
    const lang = document.getElementById('langSelect').value;
    const p = document.getElementById('langP').checked;
    const d = document.getElementById('langD').checked;
    fetch('/api/lang/set', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({current: lang, extensions: {p, d}})
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            document.getElementById('langResult').innerHTML = '<span class="text-success">语言已更新</span>';
            loadLanguage(); // 重新加载并翻译
        } else {
            document.getElementById('langResult').innerHTML = '<span class="text-danger">保存失败: ' + data.error + '</span>';
        }
    });
}

// ---------------------- 备份组管理 ----------------------
let currentGroupId = null;
let snapshots = [];
let importToken = null;
let conflictData = [];

function fetchGroups() {
    fetch('/api/groups').then(r=>r.json()).then(data=>{
        if (data.success) renderGroups(data.data);
        else alert((langDict['load_fail'] || '加载失败') + ': ' + data.error);
    });
}

function renderGroups(groups) {
    const container = document.getElementById('groupList');
    if (!groups.length) {
        container.innerHTML = '<p class="text-muted">' + (langDict['no_groups'] || '暂无备份组') + '</p>';
        return;
    }
    let html = '<div class="list-group">';
    groups.forEach(g => {
        const last = g.last_backup ? new Date(g.last_backup*1000).toLocaleString() : (langDict['never'] || '从未');
        html += `<div class="list-group-item list-group-item-action d-flex justify-content-between align-items-center" onclick="selectGroup(${g.id})">
            <div><strong>${g.name}</strong> <span class="text-muted ms-2">${g.description||''}</span>
            <br><small>${langDict['paths']||'路径'}: ${g.paths.join(', ')} | ${langDict['interval']||'间隔'}: ${g.interval}s | ${langDict['retention']||'保留'}: ${g.retention}</small></div>
            <div>
                <span class="badge ${g.enabled?'bg-success':'bg-secondary'}">${g.enabled?(langDict['enabled']||'启用'):(langDict['disabled']||'禁用')}</span>
                <span class="badge bg-info">${langDict['last']||'上次'}: ${last}</span>
                <button class="btn btn-sm btn-outline-primary ms-2" onclick="event.stopPropagation(); backupGroup(${g.id})" data-i18n="backup_now_btn">备份</button>
                <button class="btn btn-sm btn-outline-secondary" onclick="event.stopPropagation(); editGroup(${g.id})" data-i18n="edit">编辑</button>
                <button class="btn btn-sm btn-outline-danger" onclick="event.stopPropagation(); deleteGroup(${g.id})" data-i18n="delete">删除</button>
            </div>
        </div>`;
    });
    html += '</div>';
    container.innerHTML = html;
}

// 新增：手动备份指定组
function backupGroup(groupId) {
    if (!confirm(langDict['confirm_backup'] || '确认立即备份该组？')) return;
    fetch(`/api/groups/${groupId}/backup`, {method:'POST'})
        .then(r=>r.json()).then(res=>{
            if (res.success) {
                alert(langDict['backup_complete'] || '备份完成！');
                if (currentGroupId == groupId) loadSnapshots(groupId);
                fetchGroups(); // 更新上次备份时间
            } else {
                alert((langDict['backup_fail'] || '备份失败') + ': ' + res.error);
            }
        });
}

function selectGroup(id) {
    currentGroupId = id;
    document.getElementById('snapshotDetail').style.display = 'block';
    fetch(`/api/groups/${id}`).then(r=>r.json()).then(data=>{
        if (data.success) document.getElementById('detailGroupName').innerText = data.data.name;
    });
    loadSnapshots(id);
}

function loadSnapshots(groupId) {
    fetch(`/api/groups/${groupId}/snapshots`).then(r=>r.json()).then(data=>{
        if (data.success) { snapshots = data.data; renderSnapshots(snapshots); }
        else alert((langDict['load_snapshots_fail']||'加载快照失败') + ': ' + data.error);
    });
}

function renderSnapshots(snapshots) {
    const container = document.getElementById('snapshotList');
    if (!snapshots.length) {
        container.innerHTML = '<p class="text-muted">' + (langDict['no_snapshots']||'暂无快照') + '</p>';
        return;
    }
    let html = '<table class="table table-sm table-striped"><thead><tr><th>'+(langDict['time']||'时间')+'</th><th>'+(langDict['actions']||'操作')+'</th></tr></thead><tbody>';
    snapshots.forEach(s => {
        const dt = new Date(s.timestamp*1000).toLocaleString();
        html += `<tr><td>${dt}</td><td>
            <button class="btn btn-sm btn-warning" onclick="rollback(${s.id})">${langDict['rollback']||'回滚'}</button>
            <button class="btn btn-sm btn-info" onclick="browseSnapshot(${s.id})">${langDict['browse']||'浏览'}</button>
        </td></tr>`;
    });
    html += '</tbody></table>';
    container.innerHTML = html;
}

function rollback(snapshotId) {
    if (!confirm(langDict['confirm_rollback']||'确认回滚到该快照？此操作将覆盖当前数据！')) return;
    fetch(`/api/groups/${currentGroupId}/rollback/${snapshotId}`, {method:'POST'})
        .then(r=>r.json()).then(data=>{
            if (data.success) alert(langDict['rollback_success']||'回滚成功！');
            else alert((langDict['rollback_fail']||'回滚失败') + ': ' + data.error);
        });
}

function browseSnapshot(snapshotId) {
    // 直接跳转到文件浏览器页面，传入快照ID
    window.open(`/browse?snapshot=${snapshotId}`, '_blank');
}

// 新增/编辑组
document.getElementById('btnAddGroup').onclick = function() {
    document.getElementById('editGroupId').value = '';
    document.getElementById('groupModalTitle').innerText = langDict['add_group'] || '新增备份组';
    document.getElementById('groupName').value = '';
    document.getElementById('groupDesc').value = '';
    document.getElementById('groupPaths').value = '';
    document.getElementById('groupInterval').value = 86400;
    document.getElementById('groupRetention').value = 10;
    document.getElementById('groupEnabled').checked = true;
    groupModal.show();
};

function editGroup(id) {
    fetch(`/api/groups/${id}`).then(r=>r.json()).then(data=>{
        if (data.success) {
            const g = data.data;
            document.getElementById('editGroupId').value = g.id;
            document.getElementById('groupModalTitle').innerText = langDict['edit_group'] || '编辑备份组';
            document.getElementById('groupName').value = g.name;
            document.getElementById('groupDesc').value = g.description || '';
            document.getElementById('groupPaths').value = g.paths.join(', ');
            document.getElementById('groupInterval').value = g.interval;
            document.getElementById('groupRetention').value = g.retention;
            document.getElementById('groupEnabled').checked = g.enabled;
            groupModal.show();
        }
    });
}

document.getElementById('saveGroupBtn').onclick = function() {
    const id = document.getElementById('editGroupId').value;
    const data = {
        name: document.getElementById('groupName').value.trim(),
        description: document.getElementById('groupDesc').value.trim(),
        paths: document.getElementById('groupPaths').value.split(',').map(s=>s.trim()).filter(Boolean),
        interval: parseInt(document.getElementById('groupInterval').value),
        retention: parseInt(document.getElementById('groupRetention').value),
        enabled: document.getElementById('groupEnabled').checked
    };
    if (!data.name || !data.paths.length) {
        alert(langDict['name_paths_required']||'名称和路径不能为空');
        return;
    }
    const url = id ? `/api/groups/${id}` : '/api/groups';
    const method = id ? 'PUT' : 'POST';
    fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})
        .then(r=>r.json()).then(res=>{
            if (res.success) { groupModal.hide(); fetchGroups(); if (id && currentGroupId==id) loadSnapshots(currentGroupId); }
            else alert((langDict['save_fail']||'保存失败') + ': ' + res.error);
        });
};

function deleteGroup(id) {
    if (!confirm(langDict['confirm_delete_group']||'确定删除该备份组及其所有快照吗？')) return;
    fetch(`/api/groups/${id}`, {method:'DELETE'}).then(r=>r.json()).then(res=>{
        if (res.success) { fetchGroups(); if (currentGroupId==id) { document.getElementById('snapshotDetail').style.display='none'; currentGroupId=null; } }
        else alert((langDict['delete_fail']||'删除失败') + ': ' + res.error);
    });
}

document.getElementById('btnBackupNow').onclick = function() {
    if (!currentGroupId) return;
    backupGroup(currentGroupId);
};

document.getElementById('btnDiff').onclick = function() {
    if (snapshots.length < 2) { alert(langDict['need_two_snapshots']||'至少需要两个快照'); return; }
    const s1 = prompt(langDict['first_snapshot_id']||'第一个快照 ID', snapshots[0]?.id);
    const s2 = prompt(langDict['second_snapshot_id']||'第二个快照 ID', snapshots[1]?.id);
    if (!s1 || !s2) return;
    fetch(`/api/groups/${currentGroupId}/diff?snap1=${s1}&snap2=${s2}`)
        .then(r=>r.json()).then(res=>{
            if (res.success) {
                const d = res.data;
                let msg = `${langDict['diff']||'差异'}: ${langDict['added']||'新增'} ${d.added.length}, ${langDict['removed']||'删除'} ${d.removed.length}, ${langDict['modified']||'修改'} ${d.modified.length}`;
                if (d.added.length) msg += '\n' + (langDict['added']||'新增') + ': ' + d.added.slice(0,5).join(', ') + (d.added.length>5?'...':'');
                if (d.removed.length) msg += '\n' + (langDict['removed']||'删除') + ': ' + d.removed.slice(0,5).join(', ') + (d.removed.length>5?'...':'');
                if (d.modified.length) msg += '\n' + (langDict['modified']||'修改') + ': ' + d.modified.slice(0,5).join(', ') + (d.modified.length>5?'...':'');
                document.getElementById('diffResult').innerText = msg;
            } else alert((langDict['diff_fail']||'Diff 失败') + ': ' + res.error);
        });
};

// ---------------------- 角色管理（增加目录链接） ----------------------
function fetchRoles() {
    fetch('/api/roles').then(r=>r.json()).then(data=>{
        if (data.success) renderRoles(data.data);
        else alert((langDict['load_roles_fail']||'加载角色失败') + ': ' + data.error);
    });
}

function renderRoles(roles) {
    const container = document.getElementById('roleList');
    if (!roles.length) {
        container.innerHTML = '<p class="text-muted">' + (langDict['no_roles']||'暂无角色') + '</p>';
        return;
    }
    let html = '<div class="row row-cols-1 row-cols-md-3 g-3">';
    roles.forEach(r => {
        const status = [];
        // 将关联目录变为可点击链接
        const dirMap = {
            'memory': r.has_memory ? 'memory' : null,
            'character_cards': r.has_character ? 'character_cards' : null,
            'vrm': r.has_vrm ? 'vrm' : null,
            'mmd': r.has_mmd ? 'mmd' : null,
            'live2d': r.has_live2d ? 'live2d' : null
        };
        let statusHtml = '';
        for (const [dir, exists] of Object.entries(dirMap)) {
            if (exists) {
                const url = `/browse?path=${encodeURIComponent(dir + '/' + r.name)}`;
                statusHtml += `<a href="${url}" target="_blank" class="badge bg-info me-1">${dir}</a>`;
            }
        }
        if (!statusHtml) statusHtml = '<span class="text-muted">' + (langDict['no_files']||'（无文件）') + '</span>';

        const builtinBadge = r.builtin ? '<span class="badge bg-secondary ms-2">'+(langDict['builtin']||'内置')+'</span>' : '';
        const currentBadge = r.is_current ? '<span class="badge bg-success ms-2">'+(langDict['current']||'当前')+'</span>' : '';
        
        // 角色详情
        let detailHtml = '';
        if (r.json_info && Object.keys(r.json_info).length > 0) {
            detailHtml = '<details class="mt-2"><summary class="cursor-pointer text-primary small">'+(langDict['view_detail']||'查看详情')+'</summary>';
            detailHtml += '<div class="mt-1 small">';
            for (const [k, v] of Object.entries(r.json_info)) {
                if (k.startsWith('_')) continue;
                const val = Array.isArray(v) ? v.join(', ') : String(v);
                detailHtml += `<div><strong>${k}:</strong> ${val}</div>`;
            }
            detailHtml += '</div></details>';
        }
        
        // 切换按钮
        const switchBtn = r.is_current ? '' : `<button class="btn btn-sm btn-outline-primary me-1" onclick="switchCharacter('${r.name}')">${langDict['switch']||'切换'}</button>`;
        
        html += `<div class="col"><div class="card h-100">
            <div class="card-body">
                <h5 class="card-title">${r.name} ${builtinBadge} ${currentBadge}</h5>
                <p class="card-text small">${statusHtml}</p>
                ${detailHtml}
                <div class="mt-2">
                    ${switchBtn}
                    ${r.builtin || r.is_current ? '' : `<button class="btn btn-danger btn-sm" onclick="deleteRole('${r.name}')">${langDict['delete']||'删除'}</button>`}
                </div>
            </div>
        </div></div>`;
    });
    html += '</div>';
    container.innerHTML = html;
}

function switchCharacter(name) {
    fetch('/api/characters/switch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name})
    }).then(r=>r.json()).then(res => {
        if (res.success) {
            alert((langDict['switch_success']||'切换成功') + ': ' + name);
            fetchRoles();
        } else alert((langDict['switch_fail']||'切换失败') + ': ' + res.error);
    });
}

function deleteRole(name) {
    const deleteSnaps = confirm(`${langDict['confirm_delete_role']||'请输入角色名以确认删除'}: "${name}"\n\n${langDict['delete_snaps_confirm']||'是否同时删除相关快照？'}`) === true;
    const confirmName = prompt(`${langDict['confirm_delete_role']||'请输入角色名以确认删除'}: "${name}"`);
    if (confirmName !== name) { alert(langDict['input_mismatch']||'输入不匹配，取消删除'); return; }
    const params = new URLSearchParams({confirm: 'true', delete_snapshots: deleteSnaps});
    fetch(`/api/roles/${name}?${params}`, {method:'DELETE'})
        .then(r=>r.json()).then(res=>{
            if (res.success) {
                let msg = langDict['delete_success']||'删除成功！';
                if (res.data.backup_path) msg += `\n${langDict['backup_saved']||'已备份至'}: ${res.data.backup_path}`;
                alert(msg);
                fetchRoles();
            } else alert((langDict['delete_fail']||'删除失败') + ': ' + res.error);
        });
}

// ---------------------- 账户管理（主人） ----------------------
// 页面加载时同时加载账户
fetchAccounts();

function fetchAccounts() {
    fetch('/api/accounts').then(r=>r.json()).then(data=>{
        if (data.success) renderAccounts(data.data);
        else alert('加载账户失败: ' + data.error);
    });
}

function renderAccounts(data) {
    const container = document.getElementById('accountList');
    const owner = data.owner || {};
    let html = '<h6 class="text-primary mb-3">主人</h6>';
    html += '<div class="card"><div class="card-body">';
    html += '<button class="btn btn-sm btn-outline-primary" onclick="editOwner()">'+(langDict['account_edit']||'编辑')+'</button>';
    html += '</div></div>';
    container.innerHTML = html || '<p class="text-muted">暂无账户</p>';
}

function editOwner() {
    fetch('/api/accounts').then(r=>r.json()).then(data=>{
        const fields = data.data.owner || {};
        let formHtml = '';
        for (const [k, v] of Object.entries(fields)) {
            const val = Array.isArray(v) ? v.join(', ') : String(v || '');
            formHtml += `<div class="mb-2"><label class="form-label small">${k}</label><input class="form-control form-control-sm" name="${k}" value="${val}"></div>`;
        }
        const modalHtml = `
            <div class="modal fade show" id="accountEditModal" tabindex="-1" style="display:block; background:rgba(0,0,0,0.5);">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">${langDict['account_edit']||'编辑主人'}</h5>
                            <button type="button" class="btn-close" onclick="document.getElementById('accountEditModal').remove()"></button>
                        </div>
                        <div class="modal-body">
                            ${formHtml}
                        </div>
                        <div class="modal-footer">
                            <button class="btn btn-secondary" onclick="document.getElementById('accountEditModal').remove()">取消</button>
                            <button class="btn btn-primary" onclick="saveOwner()">${langDict['account_save']||'保存'}</button>
                        </div>
                    </div>
                </div>
            </div>`;
        document.body.insertAdjacentHTML('beforeend', modalHtml);
    });
}

function saveOwner() {
    const modal = document.getElementById('accountEditModal');
    const formData = new FormData(modal);
    const fields = {};
    for (const [k, v] of formData.entries()) fields[k] = v;
    fetch('/api/accounts/owner', {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(fields)})
        .then(r=>r.json()).then(res => {
            if (res.success) {
                modal.remove();
                fetchAccounts();
            } else alert('保存失败: ' + res.error);
        });
}

// ---------------------- 导入导出 ----------------------
document.getElementById('btnExport').onclick = function() {
    window.location.href = '/api/export';
};

document.getElementById('btnImportAnalyze').onclick = function() {
    const fileInput = document.getElementById('importFile');
    if (!fileInput.files || fileInput.files.length === 0) {
        alert(langDict['select_zip_first']||'请选择 ZIP 文件');
        return;
    }
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    document.getElementById('importResult').innerHTML = langDict['analyzing']||'分析中...';
    fetch('/api/import/analyze', {method:'POST', body:formData})
        .then(r=>r.json()).then(res=>{
            if (res.success) {
                const conflicts = res.data.conflicts;
                importToken = res.data.token;
                if (conflicts.length === 0) {
                    document.getElementById('importResult').innerHTML = '<span class="text-success">' + (langDict['no_conflicts']||'无冲突，可直接导入') + '</span>';
                    document.getElementById('conflictList').style.display = 'none';
                    document.getElementById('btnApplyImport').style.display = 'inline-block';
                    conflictData = [];
                } else {
                    document.getElementById('importResult').innerHTML = `<span class="text-warning">${langDict['conflicts_found']||'发现冲突'}: ${conflicts.length}</span>`;
                    renderConflictList(conflicts);
                    document.getElementById('conflictList').style.display = 'block';
                    document.getElementById('btnApplyImport').style.display = 'inline-block';
                    conflictData = conflicts;
                }
            } else {
                document.getElementById('importResult').innerHTML = `<span class="text-danger">${langDict['analyze_fail']||'分析失败'}: ${res.error}</span>`;
            }
        });
};

function renderConflictList(conflicts) {
    const container = document.getElementById('conflictList');
    let html = `<table class="table table-sm table-bordered"><thead><tr><th>${langDict['file']||'文件'}</th><th>${langDict['local_size']||'本地大小'}</th><th>${langDict['import_size']||'导入大小'}</th><th>${langDict['action']||'选择'}</th></tr></thead><tbody>`;
    conflicts.forEach((c, idx) => {
        html += `<tr>
            <td>${c.path}</td>
            <td>${c.local_size}</td>
            <td>${c.zip_size}</td>
            <td>
                <select class="form-select form-select-sm" data-idx="${idx}">
                    <option value="local">${langDict['keep_local']||'保留本地'}</option>
                    <option value="import">${langDict['use_import']||'采用导入'}</option>
                    <option value="skip">${langDict['skip']||'跳过'}</option>
                </select>
            </td>
        </tr>`;
    });
    html += '</tbody></table>';
    container.innerHTML = html;
}

document.getElementById('btnApplyImport').onclick = function() {
    if (!importToken) { alert(langDict['analyze_first']||'请先分析冲突'); return; }
    const selects = document.querySelectorAll('#conflictList select');
    let decisions = [];
    if (selects.length) {
        selects.forEach(sel => {
            const idx = parseInt(sel.dataset.idx);
            const action = sel.value;
            decisions.push({path: conflictData[idx].path, action});
        });
    } else {
        decisions = [];
    }
    fetch('/api/import/apply', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({token: importToken, decisions: decisions})
    }).then(r=>r.json()).then(res=>{
        if (res.success) {
            alert(langDict['import_success']||'导入合并完成！');
            document.getElementById('importResult').innerHTML = '<span class="text-success">' + (langDict['import_success']||'导入成功') + '</span>';
            document.getElementById('conflictList').style.display = 'none';
            document.getElementById('btnApplyImport').style.display = 'none';
            importToken = null;
            conflictData = [];
        } else {
            alert((langDict['import_fail']||'应用失败') + ': ' + res.error);
        }
    });
};

// ---------------------- 设置 ----------------------
function loadSettings() {
    fetch('/api/config').then(r=>r.json()).then(data=>{
        if (data.success) {
            document.getElementById('nekoRootInput').value = data.data.neko_root || '';
            document.getElementById('backupRootInput').value = data.data.backup_root || '';
        }
    });
}

document.getElementById('saveSettingsBtn').onclick = function() {
    const neko = document.getElementById('nekoRootInput').value.trim();
    const backup = document.getElementById('backupRootInput').value.trim();
    if (!neko || !backup) { alert(langDict['paths_required']||'路径不能为空'); return; }
    fetch('/api/config', {
        method: 'PUT',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({neko_root: neko, backup_root: backup})
    }).then(r=>r.json()).then(res=>{
        const resultSpan = document.getElementById('settingsResult');
        if (res.success) resultSpan.innerHTML = '<span class="text-success">' + (langDict['config_saved']||'配置已保存，请重启服务生效') + '</span>';
        else resultSpan.innerHTML = '<span class="text-danger">' + (langDict['save_fail']||'保存失败') + ': ' + res.error + '</span>';
    });
};

document.getElementById('saveLangBtn').onclick = saveLanguage;

// ---------------------- 初始化 ----------------------
const groupModal = new bootstrap.Modal(document.getElementById('groupModal'));

loadLanguage().then(() => {
    fetchGroups();
    fetchRoles();
    loadSettings();
    setInterval(() => { if (currentGroupId) loadSnapshots(currentGroupId); }, 30000);
});
