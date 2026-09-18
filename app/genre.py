"""流派归一化：把各来源（iTunes storefront / 艺人兜底）的流派写法收敛到少量规范值。

只影响新入库 tag（_write_tags 出口统一过一遍）；存量库未归一化，择期另行处理
（重跑归档或歌词回填等操作触碰时自然生效）。
"""
from __future__ import annotations

from .album import t2s

# 映射表：键为归一化后的比较形式（t2s + strip + lower），新增映射直接加一行
_GENRE_MAP = {
    "mandopop": "国语流行",
    "国语流行乐": "国语流行",
    "chinese pop": "国语流行",
    "cantopop": "粤语流行",
    "cantopop/hk-pop": "粤语流行",
    "hk-pop": "粤语流行",
    "粤语流行": "粤语流行",
    "pop": "流行",
    "流行乐": "流行",
}


def normalize_genre(genre: str) -> str:
    """流派归一化：命中映射表返回规范值，其他值原样返回（不做过度归一化）。"""
    if not genre:
        return genre
    return _GENRE_MAP.get(t2s(genre).strip().lower(), genre)
