from datetime import datetime, timedelta

import requests


POLICY_API = "https://sousuo.www.gov.cn/search-gov/data"
TOPIC_KEYWORDS = {
    "消费维权": ["消费者", "维权", "退换", "三包", "虚假宣传", "直播带货"],
    "生态环保": ["环保", "污染", "排放", "油烟", "垃圾分类", "碳排放"],
    "新能源": ["新能源", "充电", "电池", "光伏", "风电", "电动车"],
    "动物保护": ["动物", "流浪", "宠物", "野生动物", "防疫"],
    "食品安全": ["食品", "安全", "过期", "添加剂", "外卖"],
    "劳动权益": ["劳动", "社保", "工伤", "加班", "工资"],
}

_knowledge_cache_data: dict[str, object] | None = None
_knowledge_cache_updated: datetime | None = None


def fetch_policies(keyword: str, page_size: int = 10) -> list[dict[str, str]]:
    """Search the State Council policy library for policies matching a keyword."""
    params = {
        "t": "zhengcelibrary",
        "q": keyword,
        "searchfield": "title",
        "sort": "pubtime",
        "sortType": 1,
        "p": 0,
        "n": page_size,
        "type": "gwyzcwjk",
    }
    response = requests.get(POLICY_API, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("国务院政策库返回了非对象格式的数据")

    items = []
    search_vo = data.get("searchVO", {})
    category_map = search_vo.get("catMap", {})
    for category in ("gongwen", "bumenfile", "otherfile"):
        category_data = category_map.get(category, {})
        for item in category_data.get("listVO", []):
            items.append(
                {
                    "title": item.get("title", "").replace("<em>", "").replace("</em>", ""),
                    "source": item.get("source", ""),
                    "pubtime": item.get("pubtime", ""),
                    "summary": item.get("summary", ""),
                    "content": item.get("content", ""),
                    "pcode": item.get("pcode", ""),
                    "category": category,
                }
            )
    return items


def build_debate_from_policy(policy: dict[str, str]) -> dict[str, str]:
    """Build a debate prompt from a policy search result."""
    title = policy.get("title", "")
    summary = policy.get("summary") or policy.get("content", "")[:120]
    source = policy.get("source", "")
    pcode = policy.get("pcode", "")

    story = summary[:100] + "..." if len(summary) > 100 else summary
    return {
        "q": f"政策背景：{title}",
        "law": f"来源：{source}（{pcode}）",
        "story": story,
        "policy_title": title,
        "policy_source": source,
        "policy_url": f"https://www.gov.cn/zhengce/{pcode}",
    }


def get_policy_knowledge() -> dict[str, object]:
    """Return policy-based debates, refreshing the cache every six hours."""
    global _knowledge_cache_data, _knowledge_cache_updated
    now = datetime.now()
    cached_at = _knowledge_cache_updated
    if (
        _knowledge_cache_data is not None
        and cached_at is not None
        and now - cached_at < timedelta(hours=6)
    ):
        return _knowledge_cache_data

    all_debates = []
    for direction, keywords in TOPIC_KEYWORDS.items():
        for keyword in keywords[:2]:
            for policy in fetch_policies(keyword, page_size=3):
                debate = build_debate_from_policy(policy)
                debate["direction"] = direction
                all_debates.append(debate)

    seen = set()
    unique_debates = []
    for debate in all_debates:
        title = debate["policy_title"]
        if title not in seen:
            seen.add(title)
            unique_debates.append(debate)

    result = {
        "updated": now.isoformat(),
        "count": len(unique_debates[:20]),
        "debates": unique_debates[:20],
    }
    _knowledge_cache_data = result
    _knowledge_cache_updated = now
    return result


def clear_policy_cache() -> None:
    """Invalidate the cached policy-based debates."""
    global _knowledge_cache_data, _knowledge_cache_updated
    _knowledge_cache_data = None
    _knowledge_cache_updated = None
