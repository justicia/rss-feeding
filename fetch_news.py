import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from html import escape
from openai import OpenAI

client = OpenAI()


def get_soup(url):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=20
    )

    response.raise_for_status()

    return BeautifulSoup(
        response.text,
        "html.parser"
    )


def extract_article_text(url):
    soup = get_soup(url)

    paragraphs = soup.find_all("p")

    text_parts = []

    for p in paragraphs:
        text = p.get_text(" ", strip=True)

        if len(text) > 40:
            text_parts.append(text)

    full_text = "\n".join(text_parts)

    # 控制发送给 OpenAI 的正文长度
    return full_text[:10000]


def summarize_article(title, article_text):
    if not article_text:
        return "未能提取文章正文。"

    response = client.responses.create(
        model="gpt-5-mini",
        input=f"""
你是一名古典音乐新闻编辑。

请根据下面的文章生成一段简体中文摘要。

要求：
1. 控制在100到180字左右。
2. 只总结文章中的信息，不要自行补充。
3. 人名、作品名、乐团名、音乐节名称尽量准确。
4. 如果是演出评论，重点概括作者对演出、指挥、歌手、乐团或舞台制作的评价。
5. 如果是新闻，优先说明发生了什么，以及为什么重要。
6. 不要写“本文介绍了”“文章认为”等套话。
7. 不要翻译原标题。
8. 只输出摘要正文。

标题：
{title}

正文：
{article_text}
"""
    )

    return response.output_text.strip()


def is_valid_article(title, full_url):
    if not title:
        return False

    if len(title) < 25:
        return False

    blocked_parts = [
        "/author/",
        "/category/",
        "/tag/",
        "/contact",
        "/about",
        "/privacy",
        "/newsletter"
    ]

    for part in blocked_parts:
        if part in full_url:
            return False

    return True


def fetch_articles(source_name, homepage_url):
    print()
    print("=" * 60)
    print("正在抓取：", source_name)
    print("=" * 60)

    soup = get_soup(homepage_url)

    articles = []
    seen = set()

    for link in soup.find_all("a"):

        title = link.get_text(
            " ",
            strip=True
        )

        href = link.get("href")

        if not href:
            continue

        full_url = urljoin(
            homepage_url,
            href
        )

        if full_url in seen:
            continue

        if not is_valid_article(
            title,
            full_url
        ):
            continue

        if source_name == "BackstageClassical":
            if "backstageclassical.com" not in full_url:
                continue

        if source_name == "Slipped Disc":
            if "slippedisc.com" not in full_url:
                continue

        if source_name == "Scherzo":
            if "scherzo.es" not in full_url:
                continue

        print()
        print("文章：", title)
        print("链接：", full_url)

        try:
            print("正在读取正文...")

            article_text = extract_article_text(
                full_url
            )

            print(
                "正文长度：",
                len(article_text)
            )

            print("正在生成中文摘要...")

            summary = summarize_article(
                title,
                article_text
            )

            print("摘要完成")

        except Exception as e:
            print("摘要失败：")
            print(e)

            summary = "摘要生成失败。"

        articles.append({
            "source": source_name,
            "title": title,
            "summary": summary,
            "url": full_url
        })

        seen.add(full_url)

        # 先每站抓3篇测试
        if len(articles) >= 3:
            break

    return articles


def generate_html(all_articles):
    html_content = """
<!DOCTYPE html>

<html lang="zh-CN">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1.0">

<title>My Classical Daily</title>

<style>

body {
    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        "Microsoft YaHei",
        Arial,
        sans-serif;

    max-width: 900px;
    margin: 60px auto;
    padding: 0 24px;

    background: #fafafa;
    color: #222;

    line-height: 1.7;
}


h1 {
    font-size: 38px;
    margin-bottom: 60px;
    font-weight: 600;
}


h2 {
    margin-top: 70px;
    margin-bottom: 30px;
    padding-bottom: 12px;

    border-bottom: 1px solid #ccc;

    font-size: 24px;
}


.article {
    margin-bottom: 50px;
}


.title {
    font-size: 21px;
    font-weight: 600;

    margin-bottom: 12px;

    line-height: 1.4;
}


.summary {
    font-size: 16px;
    line-height: 1.9;

    margin-bottom: 12px;

    color: #444;
}


.source-link {
    font-size: 14px;
}


a {
    color: #333;
    text-decoration: underline;
}


a:hover {
    color: #000;
}

</style>

</head>


<body>

<h1>My Classical Daily</h1>
"""

    current_source = None

    for article in all_articles:

        if article["source"] != current_source:

            current_source = article["source"]

            html_content += f"""
<h2>{escape(current_source)}</h2>
"""

        title = escape(
            article["title"]
        )

        summary = escape(
            article["summary"]
        )

        url = escape(
            article["url"],
            quote=True
        )

        html_content += f"""
<div class="article">

<div class="title">
{title}
</div>

<div class="summary">
{summary}
</div>

<div class="source-link">

<a
href="{url}"
target="_blank"
rel="noopener noreferrer">

阅读原文

</a>

</div>

</div>
"""

    html_content += """

</body>

</html>
"""

    with open(
        "index.html",
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            html_content
        )


def main():

    backstage = fetch_articles(
        "BackstageClassical",
        "https://backstageclassical.com/"
    )

    slippedisc = fetch_articles(
        "Slipped Disc",
        "https://slippedisc.com/"
    )

    scherzo = fetch_articles(
        "Scherzo",
        "https://scherzo.es/noticias/criticas/"
    )

    all_articles = (
        backstage
        + slippedisc
        + scherzo
    )

    generate_html(
        all_articles
    )

    print()
    print("=" * 60)
    print("全部完成")
    print("已经生成 index.html")
    print("=" * 60)


if __name__ == "__main__":
    main()