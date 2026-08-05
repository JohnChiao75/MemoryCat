# Test 目录

测试角色生成脚本，用于快速创建测试用角色数据。

## 用法

```bash
python3 setup_test_role.py <角色名>
```

## 示例

```bash
python3 setup_test_role.py TestRole
```

会在 NEKO_ROOT 下创建：
- memory/TestRole/ - 测试记忆文件
- character_cards/TestRole/ - 测试角色卡
- vrm/TestRole/ - 空 VRM 目录
- live2d/TestRole/ - 空 Live2D 目录
