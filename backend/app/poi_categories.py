"""The existing five POI categories, shared by live search and region snapshots."""

CATEGORIES = {
    "education": {"label": "学校", "query": "学校$幼儿园", "keywords": ("学校", "幼儿园")},
    "healthcare": {"label": "医院", "query": "医院$社区卫生服务中心", "keywords": ("医院", "社区卫生服务中心")},
    "shopping": {"label": "商超", "query": "超市$便利店", "keywords": ("超市", "便利店")},
    "public_service": {"label": "公共服务", "query": "街道办事处$社区事务受理服务中心$派出所$邮局",
                       "keywords": ("街道办事处", "社区事务受理服务中心", "派出所", "邮局")},
    "dining": {"label": "餐饮", "query": "美食", "keywords": ("美食",)},
}


def cached_categories(item):
    """Recover categories from names/tags when old hashed caches omit queries.

    School subtype tags correspond to the existing school query. Do not expand
    the inventory to training centres, pharmacies, malls or all government POIs.
    """
    detail = item.get('detail_info') or {}
    if not isinstance(detail, dict):
        detail = {}
    text = ' '.join(str(value or '') for value in (item.get('name'), detail.get('tag'),
        detail.get('classified_poi_tag'), detail.get('label')))
    matches = [key for key, config in CATEGORIES.items()
               if any(keyword in text for keyword in config['keywords'])]
    if 'education' not in matches and any(word in text for word in ('小学', '中学', '大学', '高等院校')):
        matches.append('education')
    return [key for key in CATEGORIES if key in matches]
