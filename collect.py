#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国家数据局「政策发布」栏目采集脚本
================================
课程结课作业：数据采集 → 清洗 → 脱敏 → 元信息标注 → 结构化输出

流程：
  1. 采集：自动遍历 https://www.nda.gov.cn/sjj/zwgk/zcfb/ 列表页（自动探测页数，礼貌限速）
  2. 清洗：HTML 正文 → 纯文本 Markdown，去除脚本/样式/导航/多余空行等格式噪音
  3. 元信息：提取 标题、文号、发文机关、发布日期、原文链接、附件清单、采集时间
     以 YAML front matter 写入文件头（兼容 Obsidian 双链知识库）
  4. 脱敏：正则匹配手机号/座机号/邮箱/身份证号并掩码处理，记录脱敏明细

合规声明：仅自用、小批量、限速 1 秒/次，不用于商业传播，不增加服务器负担。
用法：python collect.py [--out data] [--delay 1.0]
"""

import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.nda.gov.cn"
LIST_URL = BASE + "/sjj/zwgk/zcfb/list/index_pc_{page}.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

# ---------------- 脱敏规则 ----------------
PII_RULES = [
    # 18 位身份证号（含末位 X）
    ("身份证号", re.compile(r"(?<!\d)\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"),
     lambda m: m.group(0)[:4] + "***********" + m.group(0)[-2:]),
    # 11 位手机号
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
     lambda m: m.group(0)[:3] + "****" + m.group(0)[7:]),
    # 座机：区号-号码
    ("座机号", re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)"),
     lambda m: m.group(0)[:7] + "****"),
    # 邮箱
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
     lambda m: "***@***" + m.group(0).split("@")[1]),
]


def mask_pii(text: str):
    """对文本做脱敏，返回 (新文本, 脱敏明细列表)"""
    details = []
    for name, pattern, mask in PII_RULES:
        hits = pattern.findall(text)
        if hits:
            new_text, n = pattern.subn(mask, text)
            details.append({"类型": name, "数量": n})
            text = new_text
    return text, details


# ---------------- HTTP ----------------
def fetch(session: requests.Session, url: str) -> str:
    r = session.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


# ---------------- 列表页解析 ----------------
def parse_list(html: str):
    """返回 [(标题, 相对链接, 列表页日期)]"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for li in soup.select("ul.u-list > li"):
        a = li.find("a", href=re.compile(r"/sjj/zwgk/zcfb/.+_pc\.html"))
        if not a:
            continue
        span = li.find("span")
        date = span.get_text(strip=True) if span else ""
        items.append((a.get_text(strip=True), a["href"], date))
    return items


def collect_links(session, delay: float):
    """自动翻页，404 即停止"""
    all_items, page = [], 1
    while True:
        url = LIST_URL.format(page=page)
        try:
            html = fetch(session, url)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                break
            raise
        items = parse_list(html)
        print(f"  列表页 {page}: {len(items)} 篇")
        all_items.extend(items)
        page += 1
        time.sleep(delay)
        if page > 50:  # 安全上限
            break
    # 按链接去重，保持顺序
    seen, uniq = set(), []
    for t, h, d in all_items:
        if h not in seen:
            seen.add(h)
            uniq.append((t, h, d))
    return uniq


# ---------------- 正文页解析 ----------------
DOC_NO = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9（）]{2,20}〔\d{4}〕\d{1,4}号$")


def find_doc_no(body: str) -> str:
    """逐行精确匹配文号，避免贪婪匹配带入正文"""
    for line in body.splitlines():
        line = line.strip()
        if DOC_NO.match(line):
            return line
    m = re.search(r"[\u4e00-\u9fa5]{2,12}〔\d{4}〕\d{1,4}号", body)
    return m.group(0) if m else ""


def parse_article(html: str):
    soup = BeautifulSoup(html, "html.parser")
    meta = {}
    for name in ("ArticleTitle", "PubDate", "ContentSource", "ColumnName"):
        tag = soup.find("meta", attrs={"name": name})
        if tag:
            meta[name] = (tag.get("content") or "").strip()

    article = soup.find("div", class_="article")
    body_text, attachments = "", []
    if article:
        for dd in article.select(".filelist dd a"):
            href = dd.get("href", "")
            attachments.append({"名称": dd.get_text(strip=True),
                                "链接": BASE + href if href.startswith("/") else href})
        # 附件清单单独保存，从正文中剔除，避免重复
        for fl in article.select(".filelist"):
            fl.decompose()
        for s in article.select("script, style"):
            s.decompose()
        for br in article.find_all("br"):
            br.replace_with("\n")
        for p in article.find_all(["p", "div"]):
            p.insert_before("\n")
        body_text = article.get_text()

    # 清洗：全角空格、连续空白、页眉页脚类噪音、连续空行
    body_text = body_text.replace("\u3000", " ")
    body_text = re.sub(r"[ \t]+", " ", body_text)
    body_text = re.sub(r"\n\s*\n+", "\n\n", body_text)
    body_text = "\n".join(line.strip() for line in body_text.splitlines()).strip()

    doc_no = find_doc_no(body_text)
    return meta, body_text, attachments, doc_no


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data", help="输出目录")
    ap.add_argument("--delay", type=float, default=1.0, help="请求间隔秒数")
    ap.add_argument("--max", type=int, default=0, help="最多采集篇数（0=全部）")
    args = ap.parse_args()

    out_dir = Path(args.out)
    policies_dir = out_dir / "policies"
    policies_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("① 采集列表页链接 ...")
    items = collect_links(session, args.delay)
    if args.max:
        items = items[: args.max]
    print(f"   共 {len(items)} 篇政策待采集\n")

    index = []
    for i, (title, href, list_date) in enumerate(items, 1):
        url = BASE + href
        try:
            html = fetch(session, url)
        except Exception as e:
            print(f"  [{i}/{len(items)}] 失败: {title[:30]} — {e}")
            continue
        meta, body, attachments, doc_no = parse_article(html)
        body, pii = mask_pii(body)

        title = meta.get("ArticleTitle") or title
        fname = re.sub(r'[\\/:*?"<>|\s]+', "_", title)[:80] + ".md"
        # 元信息 YAML front matter（兼容 Obsidian）
        lines = [
            "---",
            f'title: "{title}"',
            f'文号: "{doc_no}"',
            f'发文机关: "{meta.get("ContentSource", "")}"',
            f'发布日期: "{meta.get("PubDate", list_date)}"',
            '来源网站: "国家数据局"',
            f'原文链接: "{url}"',
            f'采集时间: "{now}"',
            f'脱敏处理: "{("、".join(f"{d["类型"]}x{d["数量"]}" for d in pii)) or "无"}"',
            "tags: [数据治理, 国家数据局, 政策法规]",
            "---",
            "",
            f"# {title}",
            "",
        ]
        lines.extend(body.splitlines())
        if attachments:
            lines += ["", "## 附件", ""]
            lines += [f"- [{a['名称']}]({a['链接']})" for a in attachments]
        (policies_dir / fname).write_text("\n".join(lines), encoding="utf-8")

        index.append({"标题": title, "文号": doc_no,
                      "发文机关": meta.get("ContentSource", ""),
                      "发布日期": meta.get("PubDate", list_date),
                      "原文链接": url, "文件": f"policies/{fname}",
                      "附件数": len(attachments), "脱敏条目": pii})
        print(f"  [{i}/{len(items)}] {title[:36]}  文号:{doc_no or '—'}  脱敏:{len(pii)}项")
        time.sleep(args.delay)

    # 索引
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# 国家数据局「政策发布」采集索引", "",
          f"> 采集时间：{now}｜共 {len(index)} 篇｜来源：{BASE}", ""]
    md.append("| 标题 | 文号 | 发布日期 | 附件 |")
    md.append("| --- | --- | --- | --- |")
    for it in index:
        md.append(f"| [{it['标题']}]({it['文件']}) | {it['文号'] or '—'} "
                  f"| {it['发布日期']} | {it['附件数']} |")
    (out_dir / "索引.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n✔ 完成：{len(index)} 篇正文 → {policies_dir}")
    print(f"  索引 → {out_dir/'索引.md'} / {out_dir/'index.json'}")


if __name__ == "__main__":
    main()
