#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创建测试用角色的脚本
用法: python3 setup_test_role.py <角色名>
"""

import os
import sys
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("用法: python3 setup_test_role.py <角色名>")
        print("示例: python3 setup_test_role.py TestRole")
        sys.exit(1)

    role_name = sys.argv[1]
    neko_root = Path.home() / ".local" / "share" / "N.E.K.O"

    # 检查 N.E.K.O 目录是否存在
    if not neko_root.exists():
        print(f"错误: N.E.K.O 目录不存在: {neko_root}")
        sys.exit(1)

    dirs = ['memory', 'character_cards', 'vrm', 'live2d']
    created = []

    for d in dirs:
        target = neko_root / d / role_name
        target.mkdir(parents=True, exist_ok=True)
        created.append(target)

    # 创建测试记忆文件
    memory_dir = neko_root / 'memory' / role_name
    memory_file = memory_dir / 'memory.txt'
    memory_file.write_text(f"# 测试角色: {role_name}\n\n这是由测试脚本生成的测试记忆文件。\n创建时间: {__import__('datetime').datetime.now().isoformat()}\n", encoding='utf-8')

    # 创建测试角色卡
    card_dir = neko_root / 'character_cards' / role_name
    card_file = card_dir / 'character.json'
    card_file.write_text(f'{{\n    "name": "{role_name}",\n    "description": "测试角色卡",\n    "creator": "MemoryCat Test Script"\n}}', encoding='utf-8')

    print(f"✓ 已创建测试角色: {role_name}")
    print(f"  NEKO 目录: {neko_root}")
    for c in created:
        print(f"  - {c}")
    print(f"  - {memory_file}")
    print(f"  - {card_file}")

if __name__ == '__main__':
    main()
