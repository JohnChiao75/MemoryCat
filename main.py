#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MemoryCat - N.E.K.O 数据备份与管理工具
Web 控制台运行在端口 48921
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
import importlib.util
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import Flask, request, jsonify, render_template, send_file, abort
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

# ---------------------------- 配置管理 ----------------------------
APP_NAME = "MemoryCat"
PORT = 48921  # 修改端口

CONFIG_DIR = Path.cwd() / '.memcat'
CONFIG_FILE = CONFIG_DIR / 'config.json'
LANG_DIR = Path(__file__).parent / 'lang'

def detect_neko_root():
    home = Path.home()
    if sys.platform == 'win32':
        return home / 'AppData' / 'Local' / 'N.E.K.O'
    else:
        return home / '.local' / 'share' / 'N.E.K.O'

def get_default_config():
    return {
        "neko_root": str(detect_neko_root()),
        "backup_root": str(Path.home() / '.local' / 'share' / 'MemoryCat'),
        "language": {
            "current": "zh",   # 默认中文
            "extensions": {"p": False, "d": False}
        }
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

# ---------------------------- 多语言加载 ----------------------------
def get_available_languages():
    if not LANG_DIR.exists():
        return ['en']
    langs = set()
    for f in LANG_DIR.glob('*.py'):
        name = f.stem
        if '.' not in name:
            langs.add(name)
    return sorted(langs)

def load_lang_dict(lang_code, use_p=False, use_d=False):
    base_file = LANG_DIR / f"{lang_code}.py"
    if not base_file.exists():
        return {}
    final_dict = {}
    # 先加载基础文件
    if base_file.exists():
        try:
            spec = importlib.util.spec_from_file_location("lang_module", base_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, 'LANG'):
                final_dict.update(module.LANG)
        except Exception:
            pass
    # 然后按优先级加载扩展：.d > .p（后加载的覆盖先加载的）
    if use_p:
        p_file = LANG_DIR / f"{lang_code}.p.py"
        if p_file.exists():
            try:
                spec = importlib.util.spec_from_file_location("lang_p", p_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, 'LANG'):
                    final_dict.update(module.LANG)
            except Exception:
                pass
    if use_d:
        d_file = LANG_DIR / f"{lang_code}.d.py"
        if d_file.exists():
            try:
                spec = importlib.util.spec_from_file_location("lang_d", d_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, 'LANG'):
                    final_dict.update(module.LANG)
            except Exception:
                pass
    return final_dict

def reload_language():
    global CURRENT_LANG_DICT
    lang_cfg = CONFIG.get('language', {})
    current = lang_cfg.get('current', 'en')
    ext = lang_cfg.get('extensions', {})
    use_p = ext.get('p', False)
    use_d = ext.get('d', False)
    CURRENT_LANG_DICT = load_lang_dict(current, use_p, use_d)

CURRENT_LANG_DICT = {}
reload_language()

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

# ---------------------------- 备份核心 ----------------------------
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

# ---------------------------- characters.json 管理 ----------------------------
CHARACTERS_JSON = NEKO_ROOT / 'config' / 'characters.json'

def load_characters_json():
    """加载 characters.json"""
    if not CHARACTERS_JSON.exists():
        return {'主人': {}, '猫娘': {}, '当前猫娘': None}
    try:
        return json.loads(CHARACTERS_JSON.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, Exception):
        return {'主人': {}, '猫娘': {}, '当前猫娘': None}

def save_characters_json(data):
    """保存 characters.json"""
    CHARACTERS_JSON.parent.mkdir(parents=True, exist_ok=True)
    CHARACTERS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def get_current_character():
    """获取当前字符"""
    data = load_characters_json()
    return data.get('当前猫娘')

def switch_character(name):
    """切换到指定角色"""
    data = load_characters_json()
    # 检查角色是否存在于 json 中
    found = False
    for cat in ['主人', '猫娘']:
        if name in data.get(cat, {}):
            found = True
            break
    if not found:
        # 角色不在 json 中，添加到猫娘组
        if '猫娘' not in data:
            data['猫娘'] = {}
        data['猫娘'][name] = {'昵称': name}
    data['当前猫娘'] = name
    save_characters_json(data)
    return name

def delete_from_characters_json(role_name):
    """从 characters.json 删除角色"""
    data = load_characters_json()
    removed = False
    for cat in ['主人', '猫娘']:
        if role_name in data.get(cat, {}):
            del data[cat][role_name]
            removed = True
    # 如果删除的是当前角色，清空当前角色
    if data.get('当前猫娘') == role_name:
        data['当前猫娘'] = None
    if removed:
        save_characters_json(data)
    return removed

# ---------------------------- 角色管理 ----------------------------
def get_roles():
    roles = set()
    for base in ['memory', 'character_cards']:
        dir_path = NEKO_ROOT / base
        if dir_path.exists() and dir_path.is_dir():
            for item in dir_path.iterdir():
                if item.is_dir():
                    roles.add(item.name)
    roles.add('YUI')
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

def backup_role(role_name):
    """备份指定角色到 BACKUP_ROOT/role_backups/<name>/<timestamp>.zip"""
    import zipfile
    backup_dir = BACKUP_ROOT / 'role_backups' / role_name
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_path = backup_dir / f'{role_name}_{ts}.zip'
    dirs_to_backup = ['memory', 'character_cards', 'vrm', 'mmd', 'live2d']
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for d in dirs_to_backup:
            src = NEKO_ROOT / d / role_name
            if src.exists():
                for root, _, files in os.walk(src):
                    for f in files:
                        fp = Path(root) / f
                        arcname = fp.relative_to(NEKO_ROOT)
                        zf.write(fp, arcname)
    return str(zip_path)

def delete_role(role_name, delete_snapshots=False):
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
    if delete_snapshots:
        conn = get_db()
        c = conn.cursor()
        # 查找包含该角色路径的快照并删除
        c.execute('SELECT id, path FROM snapshots')
        for sid, spath in c.fetchall():
            sp = Path(spath)
            if any(role_name in str(p) for p in sp.rglob('*')):
                try:
                    shutil.rmtree(spath)
                except Exception:
                    pass
                c.execute('DELETE FROM snapshots WHERE id=?', (sid,))
        conn.commit()
        conn.close()
    return deleted

# ---------------------------- 导入导出 ----------------------------
def export_config():
    if not NEKO_ROOT.exists():
        raise ValueError('N.E.K.O 目录不存在')
    temp_dir = tempfile.mkdtemp()
    temp_root = Path(temp_dir) / 'N.E.K.O'
    shutil.copytree(NEKO_ROOT, temp_root, symlinks=False)
    archive_path = shutil.make_archive(os.path.join(temp_dir, 'neko_config'), 'zip', temp_dir, 'N.E.K.O')
    return archive_path

def import_analyze(zip_path):
    conflicts = []
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
            local_path = NEKO_ROOT / rel_path
            if local_path.exists() and local_path.is_file():
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
    return conflicts

def import_apply(zip_path, decisions):
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
            decision = next((d for d in decisions if d['path'] == rel_path), None)
            if decision:
                action = decision['action']
                if action == 'skip':
                    continue
                elif action == 'local':
                    continue
                elif action == 'import':
                    local_path = NEKO_ROOT / rel_path
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(local_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
            else:
                local_path = NEKO_ROOT / rel_path
                local_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(local_path, 'wb') as dst:
                    shutil.copyfileobj(src, dst)

# ---------------------------- 文件浏览器 ----------------------------
def safe_path(relative_path):
    """确保路径在 NEKO_ROOT 下，防止目录遍历攻击"""
    # 规范化路径
    root = NEKO_ROOT.resolve()
    target = (root / relative_path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("路径越界")
    return target

# ---------------------------- Flask 应用 ----------------------------
app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = 'memorycat-secret-key-change-me'

@app.template_filter('timestamp_to_datetime')
def timestamp_to_datetime(ts):
    """将 Unix 时间戳格式化为可读日期时间"""
    if not ts:
        return ''
    return datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M:%S')

@app.route('/browse')
def browse_page():
    """文件浏览器页面 - 支持 NEKO 目录和快照目录"""
    rel_path = request.args.get('path', '')
    snapshot_id = request.args.get('snapshot', type=int)

    # 如果指定了 snapshot，则浏览快照目录
    if snapshot_id:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT path FROM snapshots WHERE id=?', (snapshot_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            abort(404)
        base = Path(row[0])
        if rel_path:
            target = base / rel_path
        else:
            target = base
        root_name = f'snapshot_{snapshot_id}'
    else:
        # 浏览 NEKO 目录
        target = safe_path(rel_path) if rel_path else NEKO_ROOT
        root_name = NEKO_ROOT.name

    if not target.exists():
        abort(404)
    if not target.is_dir():
        # 如果是文件，直接下载
        return send_file(target, as_attachment=True)
    # 获取目录内容
    items = []
    for item in target.iterdir():
        is_dir = item.is_dir()
        stat = item.stat() if item.exists() else None
        items.append({
            'name': item.name,
            'is_dir': is_dir,
            'size': stat.st_size if stat else 0,
            'mtime': stat.st_mtime if stat else 0
        })
    # 排序：目录在前，按名称排序
    items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    # 计算父路径
    parent_path = ''
    if rel_path:
        parent_path = str(Path(rel_path).parent)
        if parent_path == '.':
            parent_path = ''
    # 当前路径显示
    current_path = rel_path if rel_path else '根目录'
    return render_template('browse.html',
                           current_path=current_path,
                           rel_path=rel_path,
                           parent_path=parent_path,
                           items=items,
                           root_name=root_name,
                           is_snapshot=bool(snapshot_id))

@app.route('/api/browse')
def api_browse():
    """返回目录内容的 JSON 数据（供前端 AJAX 使用）- 支持快照"""
    rel_path = request.args.get('path', '')
    snapshot_id = request.args.get('snapshot', type=int)

    # 如果指定了 snapshot，则浏览快照目录
    if snapshot_id:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT path FROM snapshots WHERE id=?', (snapshot_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({'success': False, 'error': '快照不存在'}), 404
        base = Path(row[0])
        if rel_path:
            target = base / rel_path
        else:
            target = base
    else:
        try:
            target = safe_path(rel_path) if rel_path else NEKO_ROOT
        except ValueError:
            return jsonify({'success': False, 'error': '路径越界'}), 400

    if not target.exists() or not target.is_dir():
        return jsonify({'success': False, 'error': '目录不存在'}), 404
    items = []
    for item in target.iterdir():
        is_dir = item.is_dir()
        stat = item.stat() if item.exists() else None
        items.append({
            'name': item.name,
            'is_dir': is_dir,
            'size': stat.st_size if stat else 0,
            'mtime': stat.st_mtime if stat else 0
        })
    items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    parent = ''
    if rel_path:
        parent = str(Path(rel_path).parent)
        if parent == '.':
            parent = ''
    return jsonify({
        'success': True,
        'items': items,
        'rel_path': rel_path,
        'parent': parent,
        'is_snapshot': bool(snapshot_id)
    })

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
    return render_template('index.html')

# 多语言 API
@app.route('/api/lang', methods=['GET'])
@json_response
def api_get_lang():
    return {
        'current': CONFIG['language']['current'],
        'extensions': CONFIG['language']['extensions'],
        'dict': CURRENT_LANG_DICT,
        'available': get_available_languages()
    }

@app.route('/api/lang/set', methods=['POST'])
@json_response
def api_set_lang():
    data = request.json
    current = data.get('current')
    ext = data.get('extensions', {})
    if current not in get_available_languages():
        raise ValueError('不支持的语言')
    CONFIG['language']['current'] = current
    CONFIG['language']['extensions'] = {
        'p': bool(ext.get('p', False)),
        'd': bool(ext.get('d', False))
    }
    save_config(CONFIG)
    reload_language()
    return {'status': 'ok'}

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

# 备份组 API
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

# 角色 API
@app.route('/api/roles', methods=['GET'])
@json_response
def api_get_roles():
    roles = get_roles()
    # 添加当前角色标记和 json 详情
    current = get_current_character()
    char_json = load_characters_json()
    for r in roles:
        r['is_current'] = r['name'] == current
        # 从 json 获取角色详情
        r['json_info'] = {}
        for cat in ['主人', '猫娘']:
            if r['name'] in char_json.get(cat, {}):
                r['json_info'] = char_json[cat][r['name']]
                r['json_category'] = cat
                break
    return roles

@app.route('/api/characters/current', methods=['GET'])
@json_response
def api_get_current_character():
    return {'current': get_current_character()}

@app.route('/api/characters/switch', methods=['POST'])
@json_response
def api_switch_character():
    name = request.json.get('name', '') if request.is_json else request.args.get('name', '')
    if not name:
        raise ValueError('需要指定角色名')
    switched = switch_character(name)
    return {'switched': switched, 'current': get_current_character()}

@app.route('/api/roles/<role_name>', methods=['DELETE'])
@json_response
def api_delete_role(role_name):
    confirm = request.args.get('confirm', 'false').lower() == 'true'
    if not confirm:
        raise ValueError('删除角色需要确认')
    if role_name == get_current_character():
        raise ValueError('不能删除当前活跃角色，请先切换到其他角色')
    delete_snaps = request.args.get('delete_snapshots', 'false').lower() == 'true'
    backup_path = backup_role(role_name)
    deleted = delete_role(role_name, delete_snapshots=delete_snaps)
    # 同时从 characters.json 删除
    delete_from_characters_json(role_name)
    return {'deleted': deleted, 'backup_path': backup_path}

# 账户/主人管理 API
@app.route('/api/accounts', methods=['GET'])
@json_response
def api_get_accounts():
    data = load_characters_json()
    result = {}
    for cat in ['主人', '猫娘']:
        result[cat] = list(data.get(cat, {}).keys())
    return result

@app.route('/api/accounts/<category>/<name>', methods=['GET'])
@json_response
def api_get_account(name, category):
    data = load_characters_json()
    account = data.get(category, {}).get(name, {})
    return {'name': name, 'category': category, 'fields': account}

@app.route('/api/accounts/<category>/<name>', methods=['PUT'])
@json_response
def api_update_account(name, category):
    data = load_characters_json()
    if category not in data:
        data[category] = {}
    # 获取更新字段
    if request.is_json:
        fields = request.json
    else:
        fields = {k: v for k, v in request.form.items() if k != 'name'}
    data[category][name] = fields
    save_characters_json(data)
    return {'updated': True}

@app.route('/api/accounts/<category>', methods=['POST'])
@json_response
def api_create_account(category):
    data = load_characters_json()
    if category not in data:
        data[category] = {}
    if request.is_json:
        name = request.json.get('name', '')
        fields = request.json.get('fields', {})
    else:
        name = request.form.get('name', '')
        fields = {k: v for k, v in request.form.items() if k != 'name'}
    if not name:
        raise ValueError('需要指定账户名')
    data[category][name] = fields
    save_characters_json(data)
    return {'created': True, 'name': name}

@app.route('/api/accounts/<category>/<name>', methods=['DELETE'])
@json_response
def api_delete_account(name, category):
    data = load_characters_json()
    if name in data.get(category, {}):
        del data[category][name]
        # 如果删除的是当前角色，清空当前角色
        if data.get('当前猫娘') == name:
            data['当前猫娘'] = None
        save_characters_json(data)
        return {'deleted': True}
    raise ValueError('账户不存在')

# 导入导出 API
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
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, 'upload.zip')
    file.save(temp_path)
    try:
        conflicts = import_analyze(temp_path)
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
