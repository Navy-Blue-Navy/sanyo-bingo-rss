import os
import re
import time
import hashlib
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


SOURCE_URL = "https://www.sanyonews.jp/area/area_bingo"
OUTPUT_FILE = "sanyo_bingo.xml"

JST = timezone(
    timedelta(hours=9)
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


DATE_PATTERN = re.compile(
    r"(20\d{2})年"
    r"(\d{1,2})月"
    r"(\d{1,2})日"
    r"\s*"
    r"(\d{1,2}):(\d{2})"
)


ARTICLE_PATTERN = re.compile(
    r"^https://www\.sanyonews\.jp/article/\d+/?$"
)


def normalize_text(text):
    return re.sub(
        r"\s+",
        " ",
        text or ""
    ).strip()


def fetch_html():
    last_error = None

    for attempt in range(1, 4):
        try:
            print(
                f"取得 {attempt}/3:",
                SOURCE_URL
            )

            response = requests.get(
                SOURCE_URL,
                headers=HEADERS,
                timeout=30
            )

            print(
                "HTTP:",
                response.status_code
            )

            response.raise_for_status()

            if len(response.content) < 10000:
                raise RuntimeError(
                    "HTMLが異常に短いため更新を中止します"
                )

            return response.content

        except Exception as e:
            last_error = e

            print(
                "取得失敗:",
                repr(e)
            )

            if attempt < 3:
                print(
                    "10秒待って再試行します"
                )
                time.sleep(10)

    raise RuntimeError(
        f"3回とも取得に失敗しました: {last_error}"
    )


def parse_datetime(text):
    match = DATE_PATTERN.search(
        text
    )

    if not match:
        return None

    year = int(
        match.group(1)
    )

    month = int(
        match.group(2)
    )

    day = int(
        match.group(3)
    )

    hour = int(
        match.group(4)
    )

    minute = int(
        match.group(5)
    )

    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=JST
    )


def make_guid(link):
    return hashlib.sha256(
        (
            "sanyo-bingo|"
            + link
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def extract_description(
    post,
    title,
    dt
):
    full_text = normalize_text(
        post.get_text(
            " ",
            strip=True
        )
    )

    if title and full_text.startswith(title):
        full_text = full_text[
            len(title):
        ].strip()

    date_match = DATE_PATTERN.search(
        full_text
    )

    if date_match:
        after_date = full_text[
            date_match.end():
        ].strip()

        after_date = re.sub(
            r"\s*続き(?:を)?見る\s*$",
            "",
            after_date
        ).strip()

        if after_date:
            return after_date

    full_text = re.sub(
        r"\s*続き(?:を)?見る\s*$",
        "",
        full_text
    ).strip()

    return full_text


def parse_current_articles(html):
    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    posts = soup.select(
        "div.post_item"
    )

    print(
        "post_item候補:",
        len(posts)
    )

    items = []
    seen_links = set()

    for post in posts:
        heading = post.find(
            "h3"
        )

        if heading is None:
            continue

        link_tag = heading.find(
            "a",
            href=True
        )

        if link_tag is None:
            continue

        title = normalize_text(
            link_tag.get_text(
                " ",
                strip=True
            )
        )

        link = urljoin(
            SOURCE_URL,
            link_tag.get(
                "href"
            )
        )

        if not title:
            continue

        if not ARTICLE_PATTERN.match(
            link
        ):
            continue

        if link in seen_links:
            continue

        post_text = normalize_text(
            post.get_text(
                " ",
                strip=True
            )
        )

        dt = parse_datetime(
            post_text
        )

        if dt is None:
            print(
                "日時取得失敗:",
                title
            )
            continue

        description = extract_description(
            post,
            title,
            dt
        )

        guid = make_guid(
            link
        )

        items.append(
            {
                "title": title,
                "link": link,
                "guid": guid,
                "description": description,
                "pubDate": format_datetime(
                    dt
                ),
                "_datetime": dt,
            }
        )

        seen_links.add(
            link
        )

    return items


def load_existing_items():
    items = {}

    if not os.path.exists(
        OUTPUT_FILE
    ):
        print(
            "既存RSS件数: 0"
        )
        return items

    try:
        tree = ET.parse(
            OUTPUT_FILE
        )

        root = tree.getroot()

        for item in root.findall(
            "./channel/item"
        ):
            guid = normalize_text(
                item.findtext(
                    "guid"
                )
            )

            if not guid:
                continue

            pub_date = normalize_text(
                item.findtext(
                    "pubDate"
                )
            )

            try:
                dt = parsedate_to_datetime(
                    pub_date
                )

                if dt.tzinfo is None:
                    dt = dt.replace(
                        tzinfo=JST
                    )

            except Exception:
                dt = datetime.min.replace(
                    tzinfo=timezone.utc
                )

            items[guid] = {
                "title": (
                    item.findtext(
                        "title"
                    )
                    or ""
                ),
                "link": (
                    item.findtext(
                        "link"
                    )
                    or ""
                ),
                "guid": guid,
                "description": (
                    item.findtext(
                        "description"
                    )
                    or ""
                ),
                "pubDate": pub_date,
                "_datetime": dt,
            }

        print(
            "既存RSS件数:",
            len(items)
        )

    except Exception as e:
        print(
            "既存RSS読み込み失敗:",
            repr(e)
        )

    return items


def build_rss(items):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0"
        }
    )

    channel = ET.SubElement(
        rss,
        "channel"
    )

    ET.SubElement(
        channel,
        "title"
    ).text = "山陽新聞 備後"

    ET.SubElement(
        channel,
        "link"
    ).text = SOURCE_URL

    ET.SubElement(
        channel,
        "description"
    ).text = (
        "山陽新聞デジタル「備後」の"
        "新着記事を配信する非公式RSS"
    )

    ET.SubElement(
        channel,
        "language"
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate"
    ).text = format_datetime(
        datetime.now(
            timezone.utc
        )
    )

    for data in items:
        item = ET.SubElement(
            channel,
            "item"
        )

        ET.SubElement(
            item,
            "title"
        ).text = data[
            "title"
        ]

        ET.SubElement(
            item,
            "link"
        ).text = data[
            "link"
        ]

        guid = ET.SubElement(
            item,
            "guid",
            {
                "isPermaLink": "false"
            }
        )

        guid.text = data[
            "guid"
        ]

        ET.SubElement(
            item,
            "description"
        ).text = data[
            "description"
        ]

        ET.SubElement(
            item,
            "pubDate"
        ).text = data[
            "pubDate"
        ]

    tree = ET.ElementTree(
        rss
    )

    ET.indent(
        tree,
        space="  "
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True
    )


def main():
    print(
        "=" * 70
    )
    print(
        "山陽新聞 備後 RSS"
    )
    print(
        "=" * 70
    )

    existing = load_existing_items()

    try:
        html = fetch_html()

        current = parse_current_articles(
            html
        )

        print(
            "現在ページ取得件数:",
            len(current)
        )

        if len(current) < 10:
            raise RuntimeError(
                "現在ページの記事取得数が"
                "10件未満のため更新を中止します"
            )

        merged = dict(
            existing
        )

        new_count = 0

        for item in current:
            guid = item[
                "guid"
            ]

            if guid not in merged:
                new_count += 1

            merged[
                guid
            ] = item

        items = list(
            merged.values()
        )

        items.sort(
            key=lambda x: x[
                "_datetime"
            ],
            reverse=True
        )

        print(
            "今回の新規記事:",
            new_count,
            "件"
        )

        print(
            "RSS保存件数:",
            len(items)
        )

        print()
        print(
            "上位20件"
        )

        for number, item in enumerate(
            items[:20],
            start=1
        ):
            print(
                f"{number:2}. "
                f"{item['pubDate']} | "
                f"{item['title']}"
            )

        build_rss(
            items
        )

        print()
        print(
            "RSS作成成功"
        )

        print(
            "保存先:",
            os.path.abspath(
                OUTPUT_FILE
            )
        )

    except Exception as e:
        print()
        print(
            "エラー:",
            repr(e)
        )

        print(
            "既存RSSは変更しません"
        )

        raise


if __name__ == "__main__":
    main()