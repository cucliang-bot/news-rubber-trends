# News Rubber Trends

橡胶行业新闻自动采集与翻译系统。从8个行业来源自动抓取新闻，使用 DeepSeek AI 筛选、生成中文摘要，经同事选题后自动翻译改写。

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions                                              │
│                                                              │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐ │
│  │ Daily Collect │   │  Auto Write  │   │ Weekly Summary   │ │
│  │  14:30 CST   │   │  Every hour  │   │  Fri 14:00 CST   │ │
│  └──────┬───────┘   └──────┬───────┘   └────────┬─────────┘ │
│         │                   │                     │           │
│         ▼                   ▼                     ▼           │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              Cloud Storage (jsonbin)                  │    │
│  │  candidates / confirm / final / history               │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              GitHub Gist (backup)                     │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Web Selection Page (colleagues)                             │
│  Read confirm queue → Select articles → Write back status    │
└─────────────────────────────────────────────────────────────┘
```

## 工作流程

### 每日采集 (Daily Collect)
1. 从8个新闻源抓取最新文章列表
2. DeepSeek AI 筛选橡胶/轮胎行业相关文章
3. 生成中文标题和摘要
4. 合并多源重复报道
5. 与历史记录去重
6. 下载文章配图
7. 生成 candidates.json / xlsx / html
8. 推送到云端存储，等待同事选题

### 自动改写 (Auto Write)
1. 每小时检查确认队列
2. 找到同事已选的文章
3. 抓取原文全文
4. 按翻译规范改写为中文新闻稿
5. 检查相似度确保非直接复制
6. 推送最终稿件

### 每周汇总 (Weekly Summary)
1. 汇总本周所有候选新闻
2. 跨天去重合并
3. 生成周报

## 新闻来源

| 来源 | 语言 | 说明 |
|------|------|------|
| 炭黑产业网 (tanhei.com) | 中文 | 国内炭黑及橡胶行业 |
| 轮胎世界网 (tireworld.com.cn) | 中文 | 国内轮胎及橡胶行业 |
| European Rubber Journal | English | 欧洲橡胶杂志 |
| Rubber World | English | 橡胶世界 |
| Just-Auto | English | 汽车行业 |
| Chemanalyst | English | 化工行业（含合成橡胶/炭黑） |
| Tyrepress | English | 轮胎行业 |
| Tyre Trends | English | 轮胎趋势 |

## 项目结构

```
├── .github/workflows/     # GitHub Actions 工作流
│   ├── daily-collect.yml  # 每日采集
│   ├── auto-write.yml     # 自动改写
│   └── weekly-summary.yml # 每周汇总
├── config/
│   ├── sources.json       # 新闻源配置
│   ├── translation_guide.md  # 翻译规范
│   └── cloud_config.template.json  # 云存储配置模板
├── scripts/
│   ├── fetch_news.py      # 多源新闻抓取
│   ├── ai_filter.py       # DeepSeek AI 筛选/摘要/改写
│   ├── dedup.py           # 历史去重
│   ├── fetch_image.py     # 文章图片下载
│   ├── push_to_cloud.py   # 云存储推送 (jsonbin + gist)
│   ├── daily_collect.py   # 每日采集主流程
│   ├── auto_write.py      # 自动改写主流程
│   └── weekly_summary.py  # 每周汇总主流程
├── server/cloud-app/      # Web 选题页面
├── data/                  # 生成的数据（gitignore）
│   ├── selected_history.json
│   └── YYYY-MM-DD/
│       ├── candidates.json
│       ├── candidates.html
│       ├── final.json
│       └── images/
└── requirements.txt
```

## 配置

### 1. 复制云存储配置
```bash
cp config/cloud_config.template.json config/cloud_config.json
# 编辑填入实际的 key 和 bin ID
```

### 2. 设置 GitHub Secrets
在 GitHub 仓库 Settings → Secrets 中添加：
- `DEEPSEEK_API_KEY` — DeepSeek API 密钥
- `JSONBIN_MASTER_KEY` — jsonbin.io 主密钥
- `GIST_TOKEN` — GitHub Personal Access Token（gist scope）
- `GIST_ID` — 用于备份的 Gist ID

### 3. 创建 jsonbin Bins
在 [jsonbin.io](https://jsonbin.io) 创建以下 bins：
- candidates — 存储每日候选新闻
- final — 存储最终稿件
- confirm — 确认队列（Web 选题页面读写）
- history — 历史记录
- config — 运行配置

## 本地运行

```bash
pip install -r requirements.txt

# 每日采集
python scripts/daily_collect.py --days-back 3 --skip-push

# 自动改写
python scripts/auto_write.py --dry-run

# 每周汇总
python scripts/weekly_summary.py --week 2026-W39
```

## 依赖

- Python 3.11+
- requests
- beautifulsoup4 + lxml
- openpyxl
- DeepSeek API
