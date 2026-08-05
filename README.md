# MemoryCat

N.E.K.O 数据备份与管理工具

一个用于管理 N.E.K.O 角色 AI 数据的 Web 控制台，提供自动备份、快照管理、角色实例管理和导入导出功能。

## ✨ 功能特性

### 📦 备份管理
- **自动备份**：基于间隔时间的自动备份，可自定义备份组
- **快照系统**：保留多个历史快照，支持回滚
- **差异比较**：比较两个快照之间的文件差异
- **文件浏览**：直接查看备份中的文件内容

### 🎭 角色管理
- **多实例支持**：管理多个角色实例（如 YUI、其他自定义角色）
- **实例创建/删除**：轻松创建新角色或删除不需要的实例

### 📥 导入导出
- **配置导出**：导出当前所有配置为 ZIP 文件
- **智能导入**：导入时自动检测冲突，可选择保留本地或采用导入版本

### 🌐 多语言支持
- **中文**（默认）
- **英文**
- 支持自定义语言扩展（`.p` 拟人化、`.d` 自定义）

## 🚀 快速开始

### 安装依赖

```bash
cd ~/Projects/MemoryCat
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 启动服务

```bash
python main.py
```

服务默认运行在 `http://localhost:48921`

### 配置

首次运行会自动创建配置文件 `.memcat/config.json`：

```json
{
  "neko_root": "/home/username/.local/share/N.E.K.O",
  "backup_root": "/home/username/.local/share/MemoryCat",
  "language": {
    "current": "zh",
    "extensions": {"p": true, "d": false}
  }
}
```

可在 Web 界面的「设置」标签中修改配置。

## 📁 项目结构

```
MemoryCat/
├── main.py              # 主程序（Flask 应用）
├── README.md            # 项目文档
├── LICENSE              # MIT 许可证
├── .memcat/             # 应用配置目录
│   └── config.json      # 配置文件
├── templates/           # HTML 模板
│   ├── index.html       # 主页面
│   └── browse.html      # 文件浏览页面
├── static/              # 静态资源
│   └── css/
│       └── style.css    # 样式表
├── lang/                # 多语言文件
│   ├── zh.py            # 中文语言包
│   ├── en.py            # 英文语言包
│   └── *.p.py           # 拟人化扩展
└── .venv/               # Python 虚拟环境
```

## 🔧 技术栈

- **后端**：Python 3 + Flask
- **调度**：APScheduler（自动备份调度）
- **数据库**：SQLite（备份元数据）
- **前端**：Bootstrap 5 + Vanilla JS

## 📖 使用指南

### 备份组

备份组定义了要备份的路径和备份策略：

| 默认组 | 包含路径 | 说明 |
|--------|----------|------|
| `main` | character_cards, config, memory | 核心数据：角色卡、配置、记忆 |
| `assets` | card_faces, vrm, mmd, live2d | 资源文件：头像、3D模型等 |

### 快照管理

- **回滚**：将指定快照恢复到当前 N.E.K.O 数据目录
- **删除**：删除不再需要的快照释放空间
- **比较**：查看两个快照之间的文件差异

### 导入导出

1. **导出**：在「导入/导出」页面点击「导出配置」
2. **导入**：选择导出的 ZIP 文件，分析冲突后选择处理策略

## 🔒 安全性

- 备份数据存储在本地的 `~/.local/share/MemoryCat/`
- 配置文件不包含敏感信息
- 导入导出使用临时文件，完成后自动清理

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

## 🔗 相关链接

- [N.E.K.O 项目](https://github.com/)
- [OpenClaw 文档](https://docs.openclaw.ai)

---

*Made with 🐱 by YUI*
