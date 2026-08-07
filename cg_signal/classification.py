from __future__ import annotations

import re
from typing import Any


ASCII_STOPWORDS = {
    "about", "after", "also", "and", "are", "asset", "assets", "available",
    "best", "cg", "dev", "development", "engine", "for", "free", "from",
    "game", "games", "gets", "how", "into", "latest", "new", "news", "now",
    "release", "released", "software", "the", "this", "tool", "tools", "using",
    "version", "video", "with",
}

def classify_topic(title: str, summary: str) -> str:
    value = f"{title} {summary}".lower()
    groups = (
        ("Engines", ("unreal", "unity", "godot", "ue5", "ue 5", "ゲームエンジン")),
        ("3D & Art", ("blender", "maya", "houdini", "zbrush", "substance 3d", "substance painter", "substance designer", "spine 2d", "esoteric software", "animation", "vfx", "render", "modeling", "modelling", "sculpt", "3dcg", "アニメーション", "モデリング", "レンダリング")),
        ("Tools & Assets", ("plugin", "asset", "software", "tool", "adobe", "substance", "nuke", "プラグイン", "アセット", "ツール", "ソフト")),
        ("Game Development", ("game dev", "gamedev", "indie", "steam", "nintendo", "playstation", "xbox", "ゲーム開発", "インディー", "ゲーム制作")),
        ("Industry", ("studio", "career", "jobs", "business", "event", "interview", "スタジオ", "求人", "イベント", "インタビュー")),
    )
    for topic, keywords in groups:
        if any(keyword in value for keyword in keywords):
            return topic
    return "General"


BUSINESS_TERMS = (
    "acquisition", "acquire", "acquires", "acquired", "bankruptcy", "bankrupt", "ceo", "cfo",
    "coo", "copyright", "earnings", "executive", "executives",
    "funding", "hiring", "insolvency", "investment", "investor", "job cuts", "job picks", "jobs",
    "layoff", "layoffs", "legal action", "lawsuit", "liquidation", "merger", "m&a",
    "market cap", "profit", "revenue", "restructuring", "retirement", "share price",
    "shareholder", "stock", "stocks", "studio closes", "studio closure", "studio closures",
    "company closes", "company closure", "company closures", "closes studio", "closes its studio",
    "shutdown", "shuts down",
    "steps down", "workforce", "workforce reduction", "union", "company policy", "regulation",
    "regulatory", "compliance", "terms of service", "事業", "企業", "労働", "合併",
    "売上", "投資", "採用", "株主", "株価", "決算", "利益", "社長", "経営",
    "著作権", "解雇", "訴訟", "買収", "資金", "資金調達", "閉鎖", "倒産", "退任",
    "引退", "人員削減", "法的措置", "規制", "方針", "利用規約", "破産",
)

# Product/player-facing game news belongs in the Industry lane.  Keep these
# terms separate from corporate evidence so a DLC or roadmap cannot be mistaken
# for a company story merely because its publisher is mentioned.
INDUSTRY_TERMS = (
    "battle pass", "beta", "console", "content update", "demo", "dlc",
    "downloadable content", "early access", "expansion", "expansion pack", "game launch",
    "game release", "game releases", "launch date", "live service", "loot box",
    "microtransaction", "new character", "new characters", "new content", "patch",
    "playable character", "pre-order", "preorder", "price increase", "pricing",
    "release date", "roadmap", "season pass", "title update", "update", "gacha",
    "in-game purchase", "in game purchase", "in-game pricing", "in game pricing",
    "ゲーム内課金", "ゲーム内価格", "ゲーム発売", "ゲームリリース", "発売日", "ロードマップ",
    "アップデート", "パッチ", "追加コンテンツ", "シーズンパス", "バトルパス", "ガチャ",
    "課金", "新キャラクター", "早期アクセス", "事前予約", "先行予約", "無料プレイ",
)

GENERIC_INDUSTRY_TERMS = {
    "beta", "console", "content update", "game launch", "game release", "game releases",
    "launch date", "patch", "price increase", "pricing", "release date", "roadmap",
    "launch", "release", "released", "title update", "update", "version", "アップデート", "パッチ", "ロードマップ",
    "ゲーム発売", "ゲームリリース", "発売日", "価格改定",
}

GAME_CONTEXT_TERMS = (
    "game", "games", "gameplay", "rpg", "mmo", "mmorpg", "player", "players",
    "playable", "character", "characters", "console", "steam", "playstation", "xbox",
    "nintendo", "mobile game", "live service", "fantasy adventure", "video game",
    "ゲーム", "ゲームプレイ", "プレイヤー", "キャラクター", "新キャラ", "新作", "作品", "発売",
)

# A source is useful context, but it is not evidence that every item it publishes
# is technical.  Keep this list deliberately affirmative: ordinary release,
# update, gameplay, or developer wording belongs in neither lane by itself.
TECHNICAL_DETAIL_TERMS = (
    "api", "artificial intelligence", "benchmark", "conference", "course",
    "bundles and closures", "dev days", "documentation", "extension", "game ai",
    "implementation", "keynote", "lecture", "library", "masterclass",
    "middleware", "modeling", "modelling", "open source", "presentation", "sdk",
    "seminar", "source code", "translation", "vfx", "workshop",
    "technical", "technology", "technique", "technical art", "tech art",
    "tutorial", "how to", "research", "researcher", "paper",
    "symposium", "siggraph", "breakdown", "making of", "behind the scenes",
    "case study", "workflow", "pipeline", "rendering", "ray tracing",
    "path tracing", "global illumination", "shader", "rigging", "motion capture",
    "procedural", "simulation", "optimization", "optimisation", "profiling",
    "plugin", "add-on", "addon", "tool", "software", "asset", "assets",
    "material", "materials",
    "asset pack", "character art", "environment art", "concept art", "portfolio",
    "3d model", "技術", "技法", "手法", "チュートリアル", "解説", "研究",
    "論文", "カンファレンス", "学会", "講演", "講義", "講座", "セミナー",
    "メイキング", "制作事例",
    "制作の裏側", "ワークフロー", "パイプライン", "レンダリング", "レイトレーシング",
    "パストレーシング", "グローバルイルミネーション", "シェーダー", "リギング",
    "モーションキャプチャ", "プロシージャル", "シミュレーション", "最適化",
    "パフォーマンス", "ベンチマーク", "プラグイン", "アドオン", "ツール", "ソフトウェア",
    "アセット", "素材", "作品紹介", "コンセプトアート", "フォトグラメトリ", "新技術",
    "オープンソース", "ミドルウェア", "拡張機能", "実装方法",
    "ソースコード", "モデリング", "3dモデル", "モデルパック", "移植", "作り方",
    "cedec", "workstation", "クリエイティブイベント", "ビジュアライゼーション",
    "ワークステーション", "翻訳",
    "iteration", "production process", "production style", "イテレーション",
    "制作工程", "制作スタイル",
)

# Explanatory and research-oriented terms are the strongest technical evidence.
# Resource terms below are also allowed to qualify incidentally, per the product
# preference for surfacing asset and material information.
TECHNICAL_EXPLANATION_TERMS = (
    "api", "benchmark", "case study", "conference", "course", "documentation",
    "how to", "implementation", "keynote", "lecture", "masterclass", "middleware",
    "open source", "optimization", "optimisation", "paper", "pipeline", "profiling",
    "production process", "production style", "procedural", "ray tracing", "rendering",
    "research", "sdk", "seminar", "shader", "simulation", "source code", "symposium",
    "technical", "technique", "technology", "translation", "tutorial", "vfx", "workshop", "workflow",
    "behind the scenes", "breakdown", "making of", "motion capture", "path tracing",
    "bundles and closures", "dev days", "global illumination", "iteration", "rigging", "siggraph",
    "cedec", "オープンソース", "カンファレンス", "シェーダー",
    "シミュレーション", "シンポジウム", "セミナー", "チュートリアル", "テクニック",
    "パイプライン", "プロシージャル", "ベンチマーク", "メイキング", "リギング",
    "レンダリング", "ワークフロー", "イテレーション", "制作の裏側", "制作事例",
    "制作工程", "制作スタイル", "実装方法", "学会", "技法", "技術", "手法", "最適化",
    "研究", "移植", "作り方", "翻訳", "講座", "講演", "講義", "解説", "論文",
)

TECHNICAL_RESOURCE_TERMS = (
    "3d asset", "3d model", "add-on", "addon", "asset", "assets", "asset pack", "extension",
    "library", "material", "materials", "middleware", "plugin", "software", "tool",
    "アセット", "アセットパック", "アドオン", "ソフトウェア", "ツール", "プラグイン",
    "ミドルウェア", "モデルパック", "素材",
)

# Software names can appear incidentally in consumer-game announcements, so
# they count as evidence but do not bypass the consumer-event guard by themselves.
TECHNICAL_PRODUCT_TERMS = (
    "blender", "fab", "gamemaker", "godot", "houdini", "maya", "nuke",
    "spine 2d", "substance 3d", "substance designer", "substance painter",
    "unity engine", "unreal", "unreal engine", "zbrush",
)

# Consumer-facing game announcements frequently contain words such as
# "release", "update", or "developer".  These generic event words only become
# Industry evidence when a game context is present, while explicit DLC/gacha
# terms classify directly.
CONSUMER_EVENT_TERMS = (
    "banner", "game launch", "game launches", "game release", "game released", "new game",
    "launch", "release", "released", "roadmap", "update", "version", "limited event",
    "limited-time event", "limited time event", "in-game event", "in game event",
    "イベント開催", "ゲーム内イベント", "期間限定イベント", "コンテンツアップデート",
    "新作ゲーム", "キャラクター追加",
)

INTEREST_TERMS = (
    ("Unreal Engine", ("unreal engine", "unreal", "ue5", "ue 5"), 28),
    (
        "Substance 3D",
        (
            "substance 3d", "adobe substance", "substance painter",
            "substance 3d painter", "substance designer", "substance 3d designer",
        ),
        25,
    ),
    ("Blender", ("blender",), 25),
    ("Houdini", ("houdini", "sidefx"), 22),
    ("Spine", ("spine 2d", "esoteric software", "spine animation"), 22),
    ("Unity", ("unity", "unity engine", "unity 6", "unity technologies", "unity editor", "ユニティ"), 20),
    (
        "AI",
        (
            "ai", "artificial intelligence", "generative ai", "genai", "machine learning",
            "neural network", "diffusion model", "large language model", "生成ai", "生成 ai",
            "人工知能", "機械学習",
        ),
        12,
    ),
)

SOFTWARE_MATCH_TERMS = (
    *((label, terms) for label, terms, _points in INTEREST_TERMS if label != "Spine"),
)

PRODUCTION_TOPIC_TERMS = (
    (
        "Modeling & sculpting",
        (
            "modeling", "modelling", "sculpt", "zbrush", "retopology",
            "photogrammetry", "モデリング", "スカルプト", "造形", "フォトグラメトリ",
        ),
    ),
    (
        "Materials & texturing",
        (
            "material", "texture", "texturing", "substance", "lookdev",
            "look development", "材質", "質感", "テクスチャ", "マテリアル", "ルックデブ",
        ),
    ),
    (
        "Animation, rigging & mocap",
        (
            "animation", "animating", "rigging", "motion capture", "mocap",
            "facial capture", "facial animation", "character motion", "アニメーション",
            "リギング", "モーションキャプチャ", "フェイシャル", "モーション制作",
        ),
    ),
    (
        "Lighting & rendering",
        (
            "lighting", "render", "ray tracing", "path tracing", "global illumination",
            "lumen", "cycles", "ライティング", "レンダリング", "レイトレーシング",
            "パストレーシング", "照明",
        ),
    ),
    (
        "VFX, simulation & procedural",
        (
            "vfx", "visual effects", "simulation", "procedural", "particle", "fluid",
            "destruction", "niagara", "geometry nodes", "houdini", "エフェクト",
            "シミュレーション", "プロシージャル", "パーティクル", "流体", "破壊表現",
        ),
    ),
    (
        "Technical art & optimization",
        (
            "technical art", "tech art", "optimization", "optimisation", "performance",
            "shader", "benchmark", "profiling", "frame rate", "lod", "nanite",
            "テクニカルアート", "最適化", "パフォーマンス", "シェーダー", "ベンチマーク",
        ),
    ),
    (
        "Pipeline, tools & automation",
        (
            "pipeline", "workflow", "plugin", "add-on", "addon", "automation", "scripting",
            "export", "import", "integration", "tool development", "パイプライン",
            "ワークフロー", "プラグイン", "アドオン", "自動化", "スクリプト", "連携",
        ),
    ),
    (
        "Game design & development",
        (
            "game design", "game development", "gameplay", "level design", "multiplayer",
            "prototype", "postmortem", "development diary", "ゲームデザイン", "ゲーム開発",
            "ゲーム制作", "ゲームプレイ", "レベルデザイン", "プロトタイプ", "開発日誌",
        ),
    ),
    (
        "Breakdowns & production stories",
        (
            "breakdown", "making of", "behind the scenes", "case study", "production story",
            "production diary", "dev diary", "メイキング", "制作事例", "制作の裏側", "事例紹介",
            "開発秘話", "制作工程", "インタビュー",
        ),
    ),
    (
        "Research & emerging tech",
        (
            "research", "researcher", "paper", "siggraph", "machine learning", "neural",
            "gaussian splatting", "radiance field", "generative", "artificial intelligence",
            "研究", "論文", "機械学習", "ニューラル", "生成ai", "生成 ai", "新技術",
        ),
    ),
    (
        "Releases & product updates",
        (
            "release", "released", "update", "version", "beta", "roadmap", "new feature",
            "now available", "リリース", "アップデート", "バージョン", "ベータ", "新機能",
            "提供開始", "公開", "ロードマップ",
        ),
    ),
    (
        "Assets & inspiration",
        (
            "character art", "environment art", "concept art", "asset pack", "showcase",
            "gallery", "portfolio", "artstation", "inspiration", "キャラクターアート",
            "背景アート", "コンセプトアート", "アセット", "作品紹介", "ショーケース",
        ),
    ),
)

EVENT_TERM_GROUPS = (
    ("release", ("release", "released", "launch", "リリース", "発売", "公開")),
    ("update", ("update", "version", "アップデート", "バージョン", "新機能")),
    ("acquisition", ("acquisition", "acquires", "acquired", "merger", "買収", "合併")),
    ("layoffs", ("layoff", "job cuts", "workforce reduction", "解雇", "人員削減")),
    ("closure", ("shutdown", "shuts down", "closure", "closes", "閉鎖", "終了", "倒産")),
    ("retirement", ("retires", "retirement", "steps down", "引退", "退任")),
    ("legal", ("lawsuit", "legal action", "copyright", "訴訟", "著作権", "権利侵害")),
    ("funding", ("funding", "investment", "資金調達", "投資")),
    ("partnership", ("partnership", "partners with", "collaboration", "提携", "協業")),
    ("pricing", ("price increase", "pricing", "値上げ", "価格改定")),
    ("delay", ("delayed", "postponed", "延期")),
    ("cancellation", ("cancelled", "canceled", "discontinued", "中止", "開発中止")),
)

DEPTH_TERMS = (
    ("Tutorial or breakdown", ("tutorial", "breakdown", "how to", "making of", "チュートリアル", "メイキング", "解説"), 10),
    ("Workflow or pipeline", ("workflow", "pipeline", "ワークフロー", "パイプライン"), 8),
    ("Rendering or shaders", ("render", "shader", "lighting", "レンダリング", "シェーダー", "ライティング"), 6),
    ("Performance", ("performance", "optimization", "optimisation", "benchmark", "最適化", "パフォーマンス"), 6),
    ("Procedural technique", ("procedural", "simulation", "node", "プロシージャル", "シミュレーション", "ノード"), 6),
    ("Product update", ("release", "update", "version", "beta", "リリース", "アップデート", "バージョン", "ベータ"), 3),
)

PRIORITY_SOURCE_BONUS = {
    "unreal-engine": 8,
    "blender-developers": 8,
    "siggraph": 6,
    "gamemakers": 3,
    "game-developer": 3,
}

PROMOTIONAL_TERMS = (
    "sale", "discount", "giveaway", "sponsored", "job digest", "job picks",
    "bundle", "セール", "割引", "求人まとめ", "プレゼント",
)


def classify_lane(title: str, summary: str, source_id: str) -> str:
    value = f"{title} {summary}".lower()
    # Use boundary-aware matching so terms such as ``tool`` do not match an
    # unrelated word fragment. Source identity never manufactures a lane
    # classification without affirmative evidence in the article itself.
    business_matches = [term for term in BUSINESS_TERMS if contains_term(value, term)]
    industry_matches = [
        term for term in INDUSTRY_TERMS
        if term not in GENERIC_INDUSTRY_TERMS and contains_term(value, term)
    ]
    technical_detail_matches = [
        term for term in TECHNICAL_DETAIL_TERMS if contains_term(value, term)
    ]
    technical_product_matches = [
        term for term in TECHNICAL_PRODUCT_TERMS if contains_term(value, term)
    ]
    has_technical_explanation = any(
        contains_term(value, term) for term in TECHNICAL_EXPLANATION_TERMS
    )
    has_technical_signal = bool(technical_detail_matches or technical_product_matches)
    has_game_context = any(contains_term(value, term) for term in GAME_CONTEXT_TERMS)
    has_consumer_event = any(contains_term(value, term) for term in CONSUMER_EVENT_TERMS)

    # Affirmative corporate events (earnings, M&A, workforce, legal, etc.) stay
    # Business even when a headline also mentions a technical team or pipeline.
    # Generic words such as ``company`` or ``industry`` are intentionally absent.
    if business_matches:
        return "Business"

    # Technical explainers remain technical when they merely mention a company,
    # publisher, or product event without reporting a corporate event.
    if has_technical_explanation:
        return "Tech & Development"

    # Explicit game events classify as Industry. Generic release/update terms
    # require a game context so tool and engine releases can stay technical.
    has_generic_industry_event = any(
        contains_term(value, term) for term in GENERIC_INDUSTRY_TERMS
    )
    if industry_matches or (has_game_context and (has_consumer_event or has_generic_industry_event)):
        return "Industry"

    if has_technical_signal:
        return "Tech & Development"

    # The existing feed is intentionally broad; when no stronger evidence is
    # available, keep an unclassified story in the product/news lane rather
    # than inventing a corporate Business classification.
    return "Industry"


def term_position(value: str, term: str) -> int:
    """Find a term without treating ASCII word fragments as product names."""

    if term.isascii():
        following_characters = "a-z" if term[-1:].isalpha() else "a-z0-9"
        match = re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![{following_characters}])",
            value,
        )
        return match.start() if match else -1
    return value.find(term)


def contains_term(value: str, term: str) -> bool:
    return term_position(value, term) >= 0


def score_relevance(
    title: str,
    summary: str,
    source_id: str,
    lane: str,
    source_count: int = 1,
) -> tuple[int, list[str]]:
    """Return a transparent, local priority score tailored to the user's tools."""

    title_value = title.lower()
    value = f"{title} {summary}".lower()
    score = 18 if lane == "Tech & Development" else 6
    reasons: list[str] = []

    for label, terms, points in INTEREST_TERMS:
        if any(contains_term(value, term) for term in terms):
            score += points
            if any(contains_term(title_value, term) for term in terms):
                score += 4
            reasons.append(label)

    for label, terms, points in DEPTH_TERMS:
        if any(term in value for term in terms):
            score += points
            reasons.append(label)

    source_bonus = PRIORITY_SOURCE_BONUS.get(source_id, 0)
    if source_bonus:
        score += source_bonus
        if source_bonus >= 6:
            reasons.append("First-party or research source")

    if source_count > 1:
        score += min(6, (source_count - 1) * 3)
        reasons.append("Multiple sources")

    if any(term in value for term in PROMOTIONAL_TERMS):
        score -= 18

    # Keep explanations compact and deterministic while preserving order.
    unique_reasons = list(dict.fromkeys(reasons))[:3]
    return max(0, min(100, score)), unique_reasons


def classify_software(title: str, summary: str) -> list[str]:
    """Return software tags ordered by prominence without duplicating cards."""

    title_value = title.lower()
    summary_value = summary.lower()
    matches: list[tuple[tuple[int, int, int], str]] = []
    for order, (label, terms) in enumerate(SOFTWARE_MATCH_TERMS):
        title_positions = [position for term in terms if (position := term_position(title_value, term)) >= 0]
        summary_positions = [position for term in terms if (position := term_position(summary_value, term)) >= 0]
        if title_positions:
            matches.append(((0, min(title_positions), order), label))
        elif summary_positions:
            matches.append(((1, min(summary_positions), order), label))

    labels = [label for _rank, label in sorted(matches)]
    return labels


def classify_topics(title: str, summary: str, lane: str) -> list[str]:
    """Return overlapping subcategories only for general production coverage."""

    if lane != "Tech & Development":
        return []
    value = f"{title} {summary}".lower()
    labels = [label for label, terms in PRODUCTION_TOPIC_TERMS if any(term in value for term in terms)]
    if labels:
        return labels
    return ["Other production"]

def apply_article_classification(article: dict[str, Any]) -> dict[str, Any]:
    """Apply every classifier-derived field from the article's stored evidence."""

    title = article.get("title", "")
    summary = article.get("summary", "")
    source_id = article.get("source_id", "")
    lane = classify_lane(title, summary, source_id)
    article["topic"] = classify_topic(title, summary)
    article["lane"] = lane
    priority_score, priority_reasons = score_relevance(
        title,
        summary,
        source_id,
        lane,
        int(article.get("source_count", 1) or 1),
    )
    article["priority_score"] = priority_score
    article["priority_reasons"] = priority_reasons
    software_tags = classify_software(title, summary)
    article["software_tags"] = software_tags
    article["software_group"] = software_tags[0] if software_tags else {
        "Industry": "Industry context",
        "Business": "Business context",
    }.get(lane, "Production techniques")
    article["topic_tags"] = (
        classify_topics(title, summary, lane)
        if article["software_group"] == "Production techniques"
        else []
    )
    return article
