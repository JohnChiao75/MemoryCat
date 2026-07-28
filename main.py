#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MemoryCat - N.E.K.O 数据备份与管理工具
Web 控制台运行在端口 48920
支持持久化配置、角色管理、导入导出
"""

import os
import sys
import json
import sqlite3
import shutil
import hashlib
import time
import threading
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import Flask, request, jsonify, render_template_string, send_file, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

# ---------------------------- 配置管理 ----------------------------
APP_NAME = "MemoryCat"
PORT = 48920

CONFIG_DIR = Path.cwd() / '.memcat'
CONFIG_FILE = CONFIG_DIR / 'config.json'

def detect_neko_root():
    home = Path.home()
    if sys.platform == 'win32':
        return home / 'AppData' / 'Local' / 'N.E.K.O'
    else:
        return home / '.local' / 'share' / 'N.E.K.O'

def get_default_config():
    return {
        "neko_root": str(detect_neko_root()),
        "backup_root": str(Path.home() / '.local' / 'share' / 'MemoryCat')
    }

def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        default = get_default_config()
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(default, f, indent=2)
        return default
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        default = get_default_config()
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(default, f, indent=2)
        return default

def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)

CONFIG = load_config()
NEKO_ROOT = Path(CONFIG['neko_root'])
BACKUP_ROOT = Path(CONFIG['backup_root'])
DB_PATH = BACKUP_ROOT / 'memorycat.db'

# ---------------------------- 数据库 ----------------------------
def init_db():
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS backup_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            paths TEXT NOT NULL,
            interval INTEGER NOT NULL,
            enabled INTEGER DEFAULT 1,
            retention INTEGER DEFAULT 10,
            last_backup INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            path TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES backup_groups(id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    DEFAULT_GROUPS = [
        {'name': 'main', 'description': '核心数据：角色卡、配置、记忆',
         'paths': ['character_cards', 'config', 'memory'], 'interval': 86400, 'enabled': 1, 'retention': 10},
        {'name': 'assets', 'description': '资源文件：card_faces, vrm, mmd, live2d',
         'paths': ['card_faces', 'vrm', 'mmd', 'live2d'], 'interval': 86400, 'enabled': 1, 'retention': 10}
    ]
    for g in DEFAULT_GROUPS:
        c.execute('SELECT id FROM backup_groups WHERE name=?', (g['name'],))
        if not c.fetchone():
            c.execute('INSERT INTO backup_groups (name, description, paths, interval, enabled, retention, last_backup) '
                      'VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (g['name'], g['description'], json.dumps(g['paths']),
                       g['interval'], g['enabled'], g['retention'], 0))
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(str(DB_PATH))

# ---------------------------- 备份核心（同前，略） ----------------------------
def get_file_hash(path, block_size=65536):
    hasher = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(block_size), b''):
            hasher.update(block)
    return hasher.hexdigest()

def file_changed(src, dst):
    if not dst.exists():
        return True
    return src.stat().st_mtime != dst.stat().st_mtime or src.stat().st_size != dst.stat().st_size

def create_snapshot(group_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT name, paths FROM backup_groups WHERE id=?', (group_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise ValueError('备份组不存在')
    group_name, paths_json = row
    paths = json.loads(paths_json)

    timestamp = int(time.time())
    snapshot_dir = BACKUP_ROOT / 'snapshots' / group_name / str(timestamp)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    c.execute('SELECT path FROM snapshots WHERE group_id=? ORDER BY timestamp DESC LIMIT 1', (group_id,))
    prev_row = c.fetchone()
    prev_snapshot = Path(prev_row[0]) if prev_row else None

    for rel_path in paths:
        src = NEKO_ROOT / rel_path
        if not src.exists():
            continue
        for root, dirs, files in os.walk(src):
            rel_root = Path(root).relative_to(NEKO_ROOT)
            for f in files:
                src_file = Path(root) / f
                rel_file = rel_root / f
                dst_file = snapshot_dir / rel_file
                dst_file.parent.mkdir(parents=True, exist_ok=True)

                if prev_snapshot:
                    prev_file = prev_snapshot / rel_file
                    if prev_file.exists() and not file_changed(src_file, prev_file):
                        try:
                            os.link(prev_file, dst_file)
                            continue
                        except OSError:
                            shutil.copy2(src_file, dst_file)
                            continue
                shutil.copy2(src_file, dst_file)

    c.execute('INSERT INTO snapshots (group_id, timestamp, path) VALUES (?, ?, ?)',
              (group_id, timestamp, str(snapshot_dir)))
    c.execute('UPDATE backup_groups SET last_backup=? WHERE id=?', (timestamp, group_id))
    c.execute('SELECT retention FROM backup_groups WHERE id=?', (group_id,))
    retention = c.fetchone()[0]
    c.execute('SELECT id, path FROM snapshots WHERE group_id=? ORDER BY timestamp DESC', (group_id,))
    all_snapshots = c.fetchall()
    if len(all_snapshots) > retention:
        for snap_id, snap_path in all_snapshots[retention:]:
            try:
                shutil.rmtree(snap_path)
            except Exception:
                pass
            c.execute('DELETE FROM snapshots WHERE id=?', (snap_id,))
    conn.commit()
    conn.close()
    return str(snapshot_dir)

def rollback(group_id, snapshot_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT name, paths FROM backup_groups WHERE id=?', (group_id,))
    group = c.fetchone()
    if not group:
        conn.close()
        raise ValueError('备份组不存在')
    group_name, paths_json = group
    paths = json.loads(paths_json)

    c.execute('SELECT path FROM snapshots WHERE id=? AND group_id=?', (snapshot_id, group_id))
    row = c.fetchone()
    if not row:
        conn.close()
        raise ValueError('快照不存在')
    snapshot_path = Path(row[0])

    for rel_path in paths:
        src = snapshot_path / rel_path
        dst = NEKO_ROOT / rel_path
        if not src.exists():
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            continue
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        shutil.copytree(src, dst)
    conn.close()
    return True

def get_snapshots(group_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, timestamp, path FROM snapshots WHERE group_id=? ORDER BY timestamp DESC', (group_id,))
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'timestamp': r[1], 'path': r[2]} for r in rows]

def get_group(group_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, name, description, paths, interval, enabled, retention, last_backup FROM backup_groups WHERE id=?', (group_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        'id': row[0], 'name': row[1], 'description': row[2],
        'paths': json.loads(row[3]), 'interval': row[4],
        'enabled': bool(row[5]), 'retention': row[6], 'last_backup': row[7]
    }

def list_groups():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, name, description, paths, interval, enabled, retention, last_backup FROM backup_groups')
    rows = c.fetchall()
    conn.close()
    groups = []
    for r in rows:
        groups.append({
            'id': r[0], 'name': r[1], 'description': r[2],
            'paths': json.loads(r[3]), 'interval': r[4],
            'enabled': bool(r[5]), 'retention': r[6], 'last_backup': r[7]
        })
    return groups

def add_group(name, description, paths, interval, enabled=True, retention=10):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute('INSERT INTO backup_groups (name, description, paths, interval, enabled, retention, last_backup) '
                  'VALUES (?, ?, ?, ?, ?, ?, 0)',
                  (name, description, json.dumps(paths), interval, 1 if enabled else 0, retention))
        conn.commit()
        group_id = c.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError('备份组名称已存在')
    conn.close()
    return group_id

def update_group(group_id, name=None, description=None, paths=None, interval=None, enabled=None, retention=None):
    conn = get_db()
    c = conn.cursor()
    fields = []
    values = []
    if name is not None:
        fields.append('name=?'); values.append(name)
    if description is not None:
        fields.append('description=?'); values.append(description)
    if paths is not None:
        fields.append('paths=?'); values.append(json.dumps(paths))
    if interval is not None:
        fields.append('interval=?'); values.append(interval)
    if enabled is not None:
        fields.append('enabled=?'); values.append(1 if enabled else 0)
    if retention is not None:
        fields.append('retention=?'); values.append(retention)
    if fields:
        sql = f"UPDATE backup_groups SET {', '.join(fields)} WHERE id=?"
        values.append(group_id)
        c.execute(sql, values)
        conn.commit()
    conn.close()

def delete_group(group_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT path FROM snapshots WHERE group_id=?', (group_id,))
    for row in c.fetchall():
        try:
            shutil.rmtree(row[0])
        except:
            pass
    c.execute('DELETE FROM snapshots WHERE group_id=?', (group_id,))
    c.execute('DELETE FROM backup_groups WHERE id=?', (group_id,))
    conn.commit()
    conn.close()

def diff_snapshots(group_id, snap1_id, snap2_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT path FROM snapshots WHERE id=? AND group_id=?', (snap1_id, group_id))
    row1 = c.fetchone()
    c.execute('SELECT path FROM snapshots WHERE id=? AND group_id=?', (snap2_id, group_id))
    row2 = c.fetchone()
    conn.close()
    if not row1 or not row2:
        raise ValueError('快照不存在')
    path1 = Path(row1[0]); path2 = Path(row2[0])
    def get_all_files(base):
        files = set()
        if not base.exists():
            return files
        for root, dirs, files_list in os.walk(base):
            rel = Path(root).relative_to(base)
            for f in files_list:
                files.add(rel / f)
        return files
    files1 = get_all_files(path1)
    files2 = get_all_files(path2)
    added = files2 - files1
    removed = files1 - files2
    modified = []
    for f in files1 & files2:
        p1 = path1 / f; p2 = path2 / f
        if p1.stat().st_mtime != p2.stat().st_mtime or p1.stat().st_size != p2.stat().st_size:
            modified.append(f)
    return {
        'added': [str(f) for f in added],
        'removed': [str(f) for f in removed],
        'modified': [str(f) for f in modified]
    }

# ---------------------------- 调度器 ----------------------------
scheduler = BackgroundScheduler()

def scheduled_backup():
    conn = get_db()
    c = conn.cursor()
    now = int(time.time())
    c.execute('SELECT id, interval, last_backup FROM backup_groups WHERE enabled=1')
    rows = c.fetchall()
    conn.close()
    for group_id, interval, last_backup in rows:
        if now - last_backup >= interval:
            try:
                create_snapshot(group_id)
                print(f"[{datetime.now()}] 自动备份完成: 组 {group_id}")
            except Exception as e:
                print(f"[{datetime.now()}] 自动备份失败: {e}")

def init_scheduler():
    scheduler.add_job(scheduled_backup, trigger=IntervalTrigger(minutes=5), id='backup_job')
    scheduler.start()

# ---------------------------- 新增：角色管理 ----------------------------
def get_roles():
    """返回角色列表（从memory和character_cards的子目录名并集，加上内置YUI）"""
    roles = set()
    for base in ['memory', 'character_cards']:
        dir_path = NEKO_ROOT / base
        if dir_path.exists() and dir_path.is_dir():
            for item in dir_path.iterdir():
                if item.is_dir():
                    roles.add(item.name)
    # 添加内置YUI
    roles.add('YUI')
    # 构建详细信息
    result = []
    for name in sorted(roles):
        info = {
            'name': name,
            'builtin': name == 'YUI',
            'has_memory': (NEKO_ROOT / 'memory' / name).exists(),
            'has_character': (NEKO_ROOT / 'character_cards' / name).exists(),
            'has_vrm': (NEKO_ROOT / 'vrm' / name).exists(),
            'has_mmd': (NEKO_ROOT / 'mmd' / name).exists(),
            'has_live2d': (NEKO_ROOT / 'live2d' / name).exists(),
        }
        result.append(info)
    return result

def delete_role(role_name):
    """删除角色所有相关目录（memory, character_cards, vrm, mmd, live2d）"""
    if role_name == 'YUI':
        raise ValueError('不能删除内置角色 YUI')
    dirs_to_delete = ['memory', 'character_cards', 'vrm', 'mmd', 'live2d']
    deleted = []
    for d in dirs_to_delete:
        target = NEKO_ROOT / d / role_name
        if target.exists():
            shutil.rmtree(target)
            deleted.append(str(target))
    if not deleted:
        raise ValueError(f'角色 {role_name} 不存在任何关联目录')
    return deleted

# ---------------------------- 新增：导入导出 ----------------------------
def export_config():
    """打包整个 NEKO_ROOT 为 ZIP，返回临时文件路径"""
    if not NEKO_ROOT.exists():
        raise ValueError('N.E.K.O 目录不存在')
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, 'neko_config.zip')
    # 打包时，将 NEKO_ROOT 下的所有内容放入压缩包根目录（即压缩包内目录结构从 NEKO_ROOT 开始）
    # 但为了符合“压缩包中先是N.E.K.O目录”的要求，我们在压缩包内创建一个 N.E.K.O 目录，然后放入内容
    # 使用 shutil.make_archive 的 root_dir 参数
    # 我们创建一个临时目录，将 NEKO_ROOT 复制到 N.E.K.O 子目录，再打包
    temp_root = Path(temp_dir) / 'N.E.K.O'
    shutil.copytree(NEKO_ROOT, temp_root, symlinks=False)
    archive_path = shutil.make_archive(os.path.join(temp_dir, 'neko_config'), 'zip', temp_dir, 'N.E.K.O')
    return archive_path

def import_analyze(zip_path):
    """分析导入冲突，返回冲突文件列表"""
    conflicts = []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            # 跳过目录
            if info.is_dir():
                continue
            # 压缩包内路径以 N.E.K.O/ 开头，去掉前缀
            arcname = info.filename
            if arcname.startswith('N.E.K.O/'):
                rel_path = arcname[len('N.E.K.O/'):]
            else:
                rel_path = arcname  # 容错
            if not rel_path:
                continue
            local_path = NEKO_ROOT / rel_path
            if local_path.exists() and local_path.is_file():
                # 比较内容是否相同（通过哈希或修改时间）
                # 为避免读取大文件，先比较大小和修改时间
                # 但 ZIP 中无修改时间，我们比较哈希
                # 读取 zip 中的文件内容计算哈希
                with zf.open(info) as f:
                    zip_hash = hashlib.sha256(f.read()).hexdigest()
                local_hash = get_file_hash(local_path)
                if zip_hash != local_hash:
                    conflicts.append({
                        'path': rel_path,
                        'zip_hash': zip_hash,
                        'local_hash': local_hash,
                        'local_size': local_path.stat().st_size,
                        'zip_size': info.file_size
                    })
            else:
                # 本地不存在，直接导入，不算冲突
                pass
    return conflicts

def import_apply(zip_path, decisions):
    """根据决策列表应用导入，decisions: list of {path, action} action: 'local', 'import', 'skip'"""
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            arcname = info.filename
            if arcname.startswith('N.E.K.O/'):
                rel_path = arcname[len('N.E.K.O/'):]
            else:
                rel_path = arcname
            if not rel_path:
                continue
            # 检查是否在决策中
            decision = next((d for d in decisions if d['path'] == rel_path), None)
            if decision:
                action = decision['action']
                if action == 'skip':
                    continue
                elif action == 'local':
                    continue  # 保留本地
                elif action == 'import':
                    # 覆盖本地
                    local_path = NEKO_ROOT / rel_path
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(local_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                else:
                    continue
            else:
                # 没有冲突，直接导入
                local_path = NEKO_ROOT / rel_path
                local_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(local_path, 'wb') as dst:
                    shutil.copyfileobj(src, dst)

# ---------------------------- Flask 应用 ----------------------------
app = Flask(__name__)
app.secret_key = 'memorycat-secret-key-change-me'

def json_response(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            return jsonify({'success': True, 'data': result})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 400
    return wrapper

# ---------------------------- 路由 ----------------------------
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

# 配置 API
@app.route('/api/config', methods=['GET'])
@json_response
def api_get_config():
    return CONFIG

@app.route('/api/config', methods=['PUT'])
@json_response
def api_update_config():
    data = request.json
    new_neko = data.get('neko_root')
    new_backup = data.get('backup_root')
    if not new_neko or not new_backup:
        raise ValueError('neko_root 和 backup_root 都必须提供')
    CONFIG['neko_root'] = str(Path(new_neko).resolve())
    CONFIG['backup_root'] = str(Path(new_backup).resolve())
    save_config(CONFIG)
    return {'message': '配置已保存，请重启服务使所有更改生效'}

# 备份组 API（略，同前）
@app.route('/api/groups', methods=['GET'])
@json_response
def api_list_groups():
    return list_groups()

@app.route('/api/groups', methods=['POST'])
@json_response
def api_add_group():
    data = request.json
    name = data.get('name')
    description = data.get('description', '')
    paths = data.get('paths', [])
    interval = data.get('interval', 86400)
    enabled = data.get('enabled', True)
    retention = data.get('retention', 10)
    if not name or not paths:
        raise ValueError('名称和路径列表不能为空')
    group_id = add_group(name, description, paths, interval, enabled, retention)
    return {'id': group_id}

@app.route('/api/groups/<int:group_id>', methods=['GET'])
@json_response
def api_get_group(group_id):
    g = get_group(group_id)
    if not g:
        raise ValueError('备份组不存在')
    return g

@app.route('/api/groups/<int:group_id>', methods=['PUT'])
@json_response
def api_update_group(group_id):
    data = request.json
    update_group(group_id,
                 name=data.get('name'),
                 description=data.get('description'),
                 paths=data.get('paths'),
                 interval=data.get('interval'),
                 enabled=data.get('enabled'),
                 retention=data.get('retention'))
    return {'id': group_id}

@app.route('/api/groups/<int:group_id>', methods=['DELETE'])
@json_response
def api_delete_group(group_id):
    delete_group(group_id)
    return {'id': group_id}

@app.route('/api/groups/<int:group_id>/backup', methods=['POST'])
@json_response
def api_backup_now(group_id):
    snap_path = create_snapshot(group_id)
    return {'snapshot_path': snap_path}

@app.route('/api/groups/<int:group_id>/snapshots', methods=['GET'])
@json_response
def api_list_snapshots(group_id):
    return get_snapshots(group_id)

@app.route('/api/groups/<int:group_id>/rollback/<int:snapshot_id>', methods=['POST'])
@json_response
def api_rollback(group_id, snapshot_id):
    rollback(group_id, snapshot_id)
    return {'status': 'ok'}

@app.route('/api/groups/<int:group_id>/diff', methods=['GET'])
@json_response
def api_diff(group_id):
    snap1 = request.args.get('snap1', type=int)
    snap2 = request.args.get('snap2', type=int)
    if not snap1 or not snap2:
        raise ValueError('需要提供 snap1 和 snap2 参数')
    return diff_snapshots(group_id, snap1, snap2)

@app.route('/api/snapshots/<int:snapshot_id>/files', methods=['GET'])
@json_response
def api_list_files(snapshot_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT path FROM snapshots WHERE id=?', (snapshot_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise ValueError('快照不存在')
    base = Path(row[0])
    if not base.exists():
        return []
    files = []
    for root, dirs, files_list in os.walk(base):
        rel = Path(root).relative_to(base)
        for f in files_list:
            files.append(str(rel / f))
    return files

@app.route('/api/snapshots/<int:snapshot_id>/file', methods=['GET'])
@json_response
def api_get_file_content(snapshot_id):
    relpath = request.args.get('path')
    if not relpath:
        raise ValueError('需要 path 参数')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT path FROM snapshots WHERE id=?', (snapshot_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise ValueError('快照不存在')
    base = Path(row[0])
    file_path = base / relpath
    if not file_path.exists() or not file_path.is_file():
        raise ValueError('文件不存在')
    try:
        content = file_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        content = '[二进制文件，无法预览]'
    return {'content': content}

# ---------------------------- 新增：角色管理 API ----------------------------
@app.route('/api/roles', methods=['GET'])
@json_response
def api_get_roles():
    return get_roles()

@app.route('/api/roles/<role_name>', methods=['DELETE'])
@json_response
def api_delete_role(role_name):
    # 前端应传递 confirm 参数
    confirm = request.args.get('confirm', 'false').lower() == 'true'
    if not confirm:
        raise ValueError('删除角色需要确认')
    deleted = delete_role(role_name)
    return {'deleted': deleted}

# ---------------------------- 新增：导入导出 API ----------------------------
@app.route('/api/export', methods=['GET'])
def api_export():
    try:
        zip_path = export_config()
        return send_file(zip_path, as_attachment=True, download_name='neko_config.zip')
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/import/analyze', methods=['POST'])
@json_response
def api_import_analyze():
    if 'file' not in request.files:
        raise ValueError('未上传文件')
    file = request.files['file']
    if file.filename == '':
        raise ValueError('文件名为空')
    # 保存到临时文件
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, 'upload.zip')
    file.save(temp_path)
    try:
        conflicts = import_analyze(temp_path)
        # 返回冲突列表，同时返回临时文件路径以便后续应用（用session或缓存，这里简单返回临时路径给前端，但出于安全考虑，最好用session）
        # 我们将临时路径存入全局字典，用token标识
        token = hashlib.sha256(os.urandom(16)).hexdigest()
        app.config['IMPORT_TEMP'] = app.config.get('IMPORT_TEMP', {})
        app.config['IMPORT_TEMP'][token] = temp_path
        return {'conflicts': conflicts, 'token': token}
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise e

@app.route('/api/import/apply', methods=['POST'])
@json_response
def api_import_apply():
    data = request.json
    token = data.get('token')
    decisions = data.get('decisions', [])
    if not token or token not in app.config.get('IMPORT_TEMP', {}):
        raise ValueError('无效的导入会话')
    zip_path = app.config['IMPORT_TEMP'][token]
    try:
        import_apply(zip_path, decisions)
        return {'status': 'ok'}
    finally:
        # 清理临时文件
        shutil.rmtree(os.path.dirname(zip_path), ignore_errors=True)
        if token in app.config.get('IMPORT_TEMP', {}):
            del app.config['IMPORT_TEMP'][token]

# ---------------------------- 启动 ----------------------------
if __name__ == '__main__':
    init_db()
    init_scheduler()
    print(f"MemoryCat 启动，监听端口 {PORT}")
    print(f"N.E.K.O 数据目录: {NEKO_ROOT}")
    print(f"备份存储目录: {BACKUP_ROOT}")
    print(f"配置文件: {CONFIG_FILE}")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ---------------------------- HTML 模板（已整合） ----------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MemoryCat - N.E.K.O 备份管理</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; }
        .snapshot-list { max-height: 300px; overflow-y: auto; }
        .tab-content { margin-top: 20px; }
        .role-badge { margin-right: 5px; }
    </style>
</head>
<body>
<div class="container">
    <h1 class="mb-4">🐱 MemoryCat 备份控制台</h1>

    <!-- 导航标签 -->
    <ul class="nav nav-tabs" id="mainTab" role="tablist">
        <li class="nav-item"><button class="nav-link active" id="groups-tab" data-bs-toggle="tab" data-bs-target="#groups" type="button">备份组</button></li>
        <li class="nav-item"><button class="nav-link" id="roles-tab" data-bs-toggle="tab" data-bs-target="#roles" type="button">角色管理</button></li>
        <li class="nav-item"><button class="nav-link" id="import-tab" data-bs-toggle="tab" data-bs-target="#import" type="button">导入导出</button></li>
        <li class="nav-item"><button class="nav-link" id="settings-tab" data-bs-toggle="tab" data-bs-target="#settings" type="button">设置</button></li>
    </ul>
    <div class="tab-content">
        <!-- 备份组面板 -->
        <div class="tab-pane fade show active" id="groups">
            <div class="card mt-3">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span>备份组</span>
                    <button class="btn btn-primary btn-sm" id="btnAddGroup">+ 新增组</button>
                </div>
                <div class="card-body" id="groupList"><p>加载中...</p></div>
            </div>
            <div class="card mt-4" id="snapshotDetail" style="display:none;">
                <div class="card-header">
                    <span id="detailGroupName"></span> - 快照列表
                    <button class="btn btn-success btn-sm float-end" id="btnBackupNow">立即备份</button>
                </div>
                <div class="card-body">
                    <div class="snapshot-list" id="snapshotList"></div>
                    <div class="mt-3">
                        <button class="btn btn-outline-secondary btn-sm" id="btnDiff">比较两个快照</button>
                        <span class="ms-2" id="diffResult"></span>
                    </div>
                    <div class="mt-2" id="fileBrowser" style="display:none;">
                        <h6>文件浏览</h6>
                        <div id="fileList" style="max-height:200px; overflow-y:auto;"></div>
                        <div id="fileContent" style="background:#f8f9fa; padding:10px; border-radius:5px; margin-top:5px; white-space:pre-wrap;"></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- 角色管理面板 -->
        <div class="tab-pane fade" id="roles">
            <div class="card mt-3">
                <div class="card-header">角色列表</div>
                <div class="card-body" id="roleList"><p>加载中...</p></div>
            </div>
        </div>

        <!-- 导入导出面板 -->
        <div class="tab-pane fade" id="import">
            <div class="card mt-3">
                <div class="card-header">导出配置</div>
                <div class="card-body">
                    <button class="btn btn-primary" id="btnExport">导出当前配置 (ZIP)</button>
                </div>
            </div>
            <div class="card mt-3">
                <div class="card-header">导入配置</div>
                <div class="card-body">
                    <form id="importForm" enctype="multipart/form-data">
                        <div class="mb-3">
                            <label for="importFile" class="form-label">选择 ZIP 文件</label>
                            <input class="form-control" type="file" id="importFile" accept=".zip">
                        </div>
                        <button type="button" class="btn btn-warning" id="btnImportAnalyze">分析冲突</button>
                    </form>
                    <div id="importResult" class="mt-3"></div>
                    <div id="conflictList" style="display:none; max-height:400px; overflow-y:auto;"></div>
                    <button id="btnApplyImport" style="display:none;" class="btn btn-success mt-2">应用合并</button>
                </div>
            </div>
        </div>

        <!-- 设置面板 -->
        <div class="tab-pane fade" id="settings">
            <div class="card mt-3">
                <div class="card-header">全局配置</div>
                <div class="card-body">
                    <div class="mb-3">
                        <label for="nekoRootInput" class="form-label">N.E.K.O 数据目录</label>
                        <input type="text" class="form-control" id="nekoRootInput">
                        <div class="form-text">修改后需要重启服务才能生效</div>
                    </div>
                    <div class="mb-3">
                        <label for="backupRootInput" class="form-label">备份存储目录</label>
                        <input type="text" class="form-control" id="backupRootInput">
                        <div class="form-text">修改后需要重启服务才能生效</div>
                    </div>
                    <button class="btn btn-primary" id="saveSettingsBtn">保存配置</button>
                    <span id="settingsResult" class="ms-2"></span>
                </div>
            </div>
        </div>
    </div>

    <!-- 新增/编辑组 Modal -->
    <div class="modal fade" id="groupModal" tabindex="-1">
        <div class="modal-dialog">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="groupModalTitle">编辑备份组</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <input type="hidden" id="editGroupId">
                    <div class="mb-3"><label>名称</label><input class="form-control" id="groupName" placeholder="名称"></div>
                    <div class="mb-3"><label>描述</label><input class="form-control" id="groupDesc" placeholder="描述"></div>
                    <div class="mb-3"><label>包含路径（逗号分隔）</label><input class="form-control" id="groupPaths" placeholder="如: character_cards, config, memory"></div>
                    <div class="mb-3"><label>备份间隔（秒）</label><input class="form-control" id="groupInterval" type="number" value="86400"></div>
                    <div class="mb-3"><label>保留数量</label><input class="form-control" id="groupRetention" type="number" value="10"></div>
                    <div class="form-check mb-3"><input class="form-check-input" type="checkbox" id="groupEnabled" checked><label class="form-check-label">启用</label></div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                    <button class="btn btn-primary" id="saveGroupBtn">保存</button>
                </div>
            </div>
        </div>
    </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
    // ---------------------- 全局状态 ----------------------
    let currentGroupId = null;
    let snapshots = [];
    let importToken = null;
    let conflictData = [];

    // ---------------------- 备份组功能（同前）---------------------
    function fetchGroups() {
        fetch('/api/groups').then(r=>r.json()).then(data=>{
            if (data.success) renderGroups(data.data);
            else alert('加载失败: ' + data.error);
        });
    }

    function renderGroups(groups) {
        const container = document.getElementById('groupList');
        if (!groups.length) { container.innerHTML = '<p class="text-muted">暂无备份组</p>'; return; }
        let html = '<div class="list-group">';
        groups.forEach(g => {
            const last = g.last_backup ? new Date(g.last_backup*1000).toLocaleString() : '从未';
            html += `<div class="list-group-item list-group-item-action d-flex justify-content-between align-items-center" onclick="selectGroup(${g.id})">
                <div><strong>${g.name}</strong> <span class="text-muted ms-2">${g.description||''}</span>
                <br><small>路径: ${g.paths.join(', ')} | 间隔: ${g.interval}s | 保留: ${g.retention}</small></div>
                <div>
                    <span class="badge ${g.enabled?'bg-success':'bg-secondary'}">${g.enabled?'启用':'禁用'}</span>
                    <span class="badge bg-info">上次: ${last}</span>
                    <button class="btn btn-sm btn-outline-secondary ms-2" onclick="event.stopPropagation(); editGroup(${g.id})">编辑</button>
                    <button class="btn btn-sm btn-outline-danger ms-1" onclick="event.stopPropagation(); deleteGroup(${g.id})">删除</button>
                </div>
            </div>`;
        });
        html += '</div>';
        container.innerHTML = html;
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
            else alert('加载快照失败: ' + data.error);
        });
    }

    function renderSnapshots(snapshots) {
        const container = document.getElementById('snapshotList');
        if (!snapshots.length) { container.innerHTML = '<p class="text-muted">暂无快照</p>'; return; }
        let html = '<table class="table table-sm table-striped"><thead><tr><th>时间</th><th>操作</th></tr></thead><tbody>';
        snapshots.forEach(s => {
            const dt = new Date(s.timestamp*1000).toLocaleString();
            html += `<tr><td>${dt}</td><td>
                <button class="btn btn-sm btn-warning" onclick="rollback(${s.id})">回滚</button>
                <button class="btn btn-sm btn-info" onclick="browseSnapshot(${s.id})">浏览</button>
            </td></tr>`;
        });
        html += '</tbody></table>';
        container.innerHTML = html;
    }

    function rollback(snapshotId) {
        if (!confirm('确认回滚到该快照？此操作将覆盖当前数据！')) return;
        fetch(`/api/groups/${currentGroupId}/rollback/${snapshotId}`, {method:'POST'})
            .then(r=>r.json()).then(data=>{
                if (data.success) alert('回滚成功！'); else alert('回滚失败: ' + data.error);
            });
    }

    function browseSnapshot(snapshotId) {
        const container = document.getElementById('fileBrowser');
        container.style.display = 'block';
        const fileList = document.getElementById('fileList');
        const contentDiv = document.getElementById('fileContent');
        contentDiv.innerText = '';
        fetch(`/api/snapshots/${snapshotId}/files`).then(r=>r.json()).then(data=>{
            if (data.success) {
                let html = '<ul class="list-unstyled" style="font-size:0.9em;">';
                data.data.forEach(f => {
                    html += `<li><a href="#" onclick="viewFile(${snapshotId}, '${f}'); return false;">${f}</a></li>`;
                });
                html += '</ul>';
                fileList.innerHTML = html;
            } else alert('加载文件列表失败');
        });
    }

    function viewFile(snapshotId, path) {
        fetch(`/api/snapshots/${snapshotId}/file?path=${encodeURIComponent(path)}`)
            .then(r=>r.json()).then(data=>{
                if (data.success) document.getElementById('fileContent').innerText = data.data.content || '[空]';
                else alert('读取文件失败: ' + data.error);
            });
    }

    document.getElementById('btnAddGroup').onclick = function() {
        document.getElementById('editGroupId').value = '';
        document.getElementById('groupModalTitle').innerText = '新增备份组';
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
                document.getElementById('groupModalTitle').innerText = '编辑备份组';
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
        if (!data.name || !data.paths.length) { alert('名称和路径不能为空'); return; }
        const url = id ? `/api/groups/${id}` : '/api/groups';
        const method = id ? 'PUT' : 'POST';
        fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})
            .then(r=>r.json()).then(res=>{
                if (res.success) { groupModal.hide(); fetchGroups(); if (id && currentGroupId==id) loadSnapshots(currentGroupId); }
                else alert('保存失败: ' + res.error);
            });
    };

    function deleteGroup(id) {
        if (!confirm('确定删除该备份组及其所有快照吗？')) return;
        fetch(`/api/groups/${id}`, {method:'DELETE'}).then(r=>r.json()).then(res=>{
            if (res.success) { fetchGroups(); if (currentGroupId==id) { document.getElementById('snapshotDetail').style.display='none'; currentGroupId=null; } }
            else alert('删除失败: ' + res.error);
        });
    }

    document.getElementById('btnBackupNow').onclick = function() {
        if (!currentGroupId) return;
        fetch(`/api/groups/${currentGroupId}/backup`, {method:'POST'})
            .then(r=>r.json()).then(res=>{
                if (res.success) { alert('备份完成！'); loadSnapshots(currentGroupId); }
                else alert('备份失败: ' + res.error);
            });
    };

    document.getElementById('btnDiff').onclick = function() {
        if (snapshots.length < 2) { alert('至少需要两个快照'); return; }
        const s1 = prompt('第一个快照 ID', snapshots[0]?.id);
        const s2 = prompt('第二个快照 ID', snapshots[1]?.id);
        if (!s1 || !s2) return;
        fetch(`/api/groups/${currentGroupId}/diff?snap1=${s1}&snap2=${s2}`)
            .then(r=>r.json()).then(res=>{
                if (res.success) {
                    const d = res.data;
                    let msg = `差异: 新增 ${d.added.length}, 删除 ${d.removed.length}, 修改 ${d.modified.length}`;
                    if (d.added.length) msg += '\\n新增: ' + d.added.slice(0,5).join(', ') + (d.added.length>5?'...':'');
                    if (d.removed.length) msg += '\\n删除: ' + d.removed.slice(0,5).join(', ') + (d.removed.length>5?'...':'');
                    if (d.modified.length) msg += '\\n修改: ' + d.modified.slice(0,5).join(', ') + (d.modified.length>5?'...':'');
                    document.getElementById('diffResult').innerText = msg;
                } else alert('Diff 失败: ' + res.error);
            });
    };

    // ---------------------- 角色管理 ----------------------
    function fetchRoles() {
        fetch('/api/roles').then(r=>r.json()).then(data=>{
            if (data.success) renderRoles(data.data);
            else alert('加载角色失败: ' + data.error);
        });
    }

    function renderRoles(roles) {
        const container = document.getElementById('roleList');
        if (!roles.length) { container.innerHTML = '<p class="text-muted">暂无角色</p>'; return; }
        let html = '<div class="row row-cols-1 row-cols-md-3 g-3">';
        roles.forEach(r => {
            const status = [];
            if (r.has_memory) status.push('💾记忆');
            if (r.has_character) status.push('📇角色卡');
            if (r.has_vrm) status.push('🤖VRM');
            if (r.has_mmd) status.push('🎮MMD');
            if (r.has_live2d) status.push('🎭Live2D');
            const statusStr = status.length ? status.join(' ') : '（无文件）';
            const builtinBadge = r.builtin ? '<span class="badge bg-secondary ms-2">内置</span>' : '';
            html += `<div class="col"><div class="card h-100">
                <div class="card-body">
                    <h5 class="card-title">${r.name} ${builtinBadge}</h5>
                    <p class="card-text small">${statusStr}</p>
                    ${r.builtin ? '' : `<button class="btn btn-danger btn-sm" onclick="deleteRole('${r.name}')">删除</button>`}
                </div>
            </div></div>`;
        });
        html += '</div>';
        container.innerHTML = html;
    }

    function deleteRole(name) {
        const confirmName = prompt(`请输入角色名 "${name}" 以确认删除：`);
        if (confirmName !== name) { alert('输入不匹配，取消删除'); return; }
        fetch(`/api/roles/${name}?confirm=true`, {method:'DELETE'})
            .then(r=>r.json()).then(res=>{
                if (res.success) { alert('删除成功！'); fetchRoles(); }
                else alert('删除失败: ' + res.error);
            });
    }

    // ---------------------- 导入导出 ----------------------
    document.getElementById('btnExport').onclick = function() {
        window.location.href = '/api/export';
    };

    document.getElementById('btnImportAnalyze').onclick = function() {
        const fileInput = document.getElementById('importFile');
        if (!fileInput.files || fileInput.files.length === 0) {
            alert('请选择 ZIP 文件');
            return;
        }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        document.getElementById('importResult').innerHTML = '分析中...';
        fetch('/api/import/analyze', {method:'POST', body:formData})
            .then(r=>r.json()).then(res=>{
                if (res.success) {
                    const conflicts = res.data.conflicts;
                    importToken = res.data.token;
                    if (conflicts.length === 0) {
                        document.getElementById('importResult').innerHTML = '<span class="text-success">无冲突，可直接导入</span>';
                        // 直接显示应用按钮
                        document.getElementById('conflictList').style.display = 'none';
                        document.getElementById('btnApplyImport').style.display = 'inline-block';
                        conflictData = [];
                    } else {
                        document.getElementById('importResult').innerHTML = `<span class="text-warning">发现 ${conflicts.length} 个冲突文件，请选择处理方式：</span>`;
                        renderConflictList(conflicts);
                        document.getElementById('conflictList').style.display = 'block';
                        document.getElementById('btnApplyImport').style.display = 'inline-block';
                        conflictData = conflicts;
                    }
                } else {
                    document.getElementById('importResult').innerHTML = `<span class="text-danger">分析失败: ${res.error}</span>`;
                }
            });
    };

    function renderConflictList(conflicts) {
        const container = document.getElementById('conflictList');
        let html = '<table class="table table-sm table-bordered"><thead><tr><th>文件</th><th>本地大小</th><th>导入大小</th><th>选择</th></tr></thead><tbody>';
        conflicts.forEach((c, idx) => {
            html += `<tr>
                <td>${c.path}</td>
                <td>${c.local_size}</td>
                <td>${c.zip_size}</td>
                <td>
                    <select class="form-select form-select-sm" data-idx="${idx}">
                        <option value="local">保留本地</option>
                        <option value="import">采用导入</option>
                        <option value="skip">跳过</option>
                    </select>
                </td>
            </tr>`;
        });
        html += '</tbody></table>';
        container.innerHTML = html;
    }

    document.getElementById('btnApplyImport').onclick = function() {
        if (!importToken) { alert('请先分析冲突'); return; }
        // 收集决策
        const selects = document.querySelectorAll('#conflictList select');
        let decisions = [];
        if (selects.length) {
            selects.forEach(sel => {
                const idx = parseInt(sel.dataset.idx);
                const action = sel.value;
                decisions.push({path: conflictData[idx].path, action});
            });
        } else {
            // 无冲突，所有文件直接导入（无决策，应用时全部采用导入）
            decisions = [];
        }
        fetch('/api/import/apply', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({token: importToken, decisions: decisions})
        }).then(r=>r.json()).then(res=>{
            if (res.success) {
                alert('导入合并完成！');
                document.getElementById('importResult').innerHTML = '<span class="text-success">导入成功</span>';
                document.getElementById('conflictList').style.display = 'none';
                document.getElementById('btnApplyImport').style.display = 'none';
                importToken = null;
                conflictData = [];
            } else {
                alert('应用失败: ' + res.error);
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
        if (!neko || !backup) { alert('路径不能为空'); return; }
        fetch('/api/config', {
            method: 'PUT',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({neko_root: neko, backup_root: backup})
        }).then(r=>r.json()).then(res=>{
            const resultSpan = document.getElementById('settingsResult');
            if (res.success) resultSpan.innerHTML = '<span class="text-success">配置已保存，请重启服务生效</span>';
            else resultSpan.innerHTML = '<span class="text-danger">保存失败: ' + res.error + '</span>';
        });
    };

    // ---------------------- 初始化 ----------------------
    const groupModal = new bootstrap.Modal(document.getElementById('groupModal'));
    fetchGroups();
    fetchRoles();
    loadSettings();

    // 定时刷新快照
    setInterval(() => { if (currentGroupId) loadSnapshots(currentGroupId); }, 30000);
</script>
</body>
</html>
"""
