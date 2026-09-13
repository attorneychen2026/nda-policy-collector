# 国家数据局政策法规采集与脱敏工具

大模型数据治理与应用课程结课作业：对国家数据局官网「政策发布」栏目进行
**自动化采集 → 格式清洗 → 元信息标注 → 数据脱敏**，输出结构化 Markdown 语料。

## 数据流水线

```
列表页(自动翻页) → 正文页 → HTML清洗 → 纯文本/Markdown
                              ↓
                  元信息(YAML front matter)：标题/文号/发文机关/发布日期/原文链接/采集时间
                              ↓
                  脱敏：手机号/座机/邮箱/身份证号 → 掩码 + 明细记录
                              ↓
                  data/policies/*.md（正文） + data/索引.md + data/index.json（索引）
```

## 使用方法

```bash
pip install -r requirements.txt
python collect.py            # 全量采集（41 篇，约 1 分钟，限速 1s/次）
python collect.py --max 5    # 试跑 5 篇
python collect.py --out data --delay 1.5
```

## 输出说明

- `data/policies/*.md`：每篇政策一个文件，YAML 头含文号、发文机关、发布日期、
  原文链接、采集时间、脱敏明细（Obsidian 可直接识别 front matter 与 tags）
- `data/索引.md` / `data/index.json`：全量索引（标题、文号、日期、附件数）

## 合规说明

仅自用学习、小批量采集、限速 1 秒/次；不用于商业传播，不给服务器造成负担。
