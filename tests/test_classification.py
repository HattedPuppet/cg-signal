import unittest

from cg_signal.classification import classify_lane


class LaneClassificationTests(unittest.TestCase):
    def test_official_unreal_release_is_technical(self):
        self.assertEqual(
            classify_lane("Unreal Engine 5.8 is now available", "Rendering and workflow features", "unreal-engine"),
            "Tech & Development",
        )

    def test_substance_workflow_is_technical(self):
        self.assertEqual(
            classify_lane("Substance Designer workflow breakdown", "A procedural material technique tutorial", "80-level"),
            "Tech & Development",
        )

    def test_layoff_report_is_industry(self):
        self.assertEqual(
            classify_lane("Animation studio announces layoffs", "The company is restructuring its business", "cartoon-brew"),
            "Business",
        )

    def test_explicit_jobs_story_overrides_technical_source_tie(self):
        self.assertEqual(
            classify_lane("Top job picks for artists", "New studio openings and careers", "80-level"),
            "Business",
        )

    def test_japanese_earnings_report_is_industry(self):
        self.assertEqual(
            classify_lane("ゲーム会社が決算を発表", "売上と利益、市場の見通しを説明", "gamebusiness"),
            "Business",
        )

    def test_strong_technical_story_overrides_business_source_prior(self):
        self.assertEqual(
            classify_lane("Houdini VFX workflow breakdown", "Rendering tutorial and pipeline technique", "gamebusiness"),
            "Tech & Development",
        )

    def test_gacha_event_is_not_technical(self):
        self.assertEqual(
            classify_lane(
                "新キャラ登場の期間限定ガチャイベントを開催",
                "ピックアップキャンペーンをゲーム内で実施",
                "automaton",
            ),
            "Industry",
        )

    def test_gacha_upgrade_materials_stay_in_industry_lane(self):
        self.assertEqual(
            classify_lane(
                "期間限定ガチャ開催、育成素材をプレゼント",
                "ゲーム内イベントで新キャラを入手できるキャンペーン",
                "automaton",
            ),
            "Industry",
        )

    def test_limited_event_game_assets_stay_in_industry_lane(self):
        self.assertEqual(
            classify_lane(
                "Limited-time event adds free game assets",
                "The content update includes new characters and materials",
                "automaton",
            ),
            "Industry",
        )

    def test_singular_asset_in_limited_events_is_industry(self):
        cases = (
            ("Limited event offers a character asset", "Players can claim it this week"),
            ("期間限定イベントでアセットを配布", "新キャラ向けの報酬として受け取れる"),
        )
        for title, summary in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    classify_lane(title, summary, "automaton"),
                    "Industry",
                )

    def test_player_facing_materials_are_industry_but_tool_materials_are_technical(self):
        cases = (
            ("Limited event offers a character material", "Players can claim it this week", "Industry"),
            ("Content update adds materials", "The event starts tomorrow", "Tech & Development"),
        )
        for title, summary, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    classify_lane(title, summary, "automaton"),
                    expected,
                )

    def test_asset_breakdown_remains_technical_despite_event_context(self):
        self.assertEqual(
            classify_lane(
                "Breakdown of assets made for a limited-time game event",
                "A technical workflow explains the modeling and rendering process",
                "80-level",
            ),
            "Tech & Development",
        )

    def test_consumer_game_update_is_not_technical(self):
        self.assertEqual(
            classify_lane(
                "『ぽこ あ ポケモン』水中実装の無料大型アプデ、8月5日配信へ",
                "DLC第1弾で新しい街とキャラクターが登場",
                "automaton",
            ),
            "Industry",
        )

    def test_consumer_game_launch_is_not_technical(self):
        self.assertEqual(
            classify_lane(
                "New co-op RPG is out now",
                "The release date trailer introduces its playable characters",
                "80-level",
            ),
            "Industry",
        )

    def test_source_prior_cannot_classify_a_no_signal_story(self):
        self.assertEqual(
            classify_lane(
                "A beautiful new fantasy adventure is announced",
                "Players explore a mysterious world together",
                "automaton",
            ),
            "Industry",
        )

    def test_engine_mention_does_not_override_a_consumer_game_event(self):
        self.assertEqual(
            classify_lane(
                "Unreal Engine game launches a limited-time gacha event",
                "The update adds new characters and a season pass",
                "automaton",
            ),
            "Industry",
        )

    def test_research_conference_is_technical(self):
        self.assertEqual(
            classify_lane(
                "SIGGRAPH neural rendering research",
                "The conference paper explains a new industry lighting technique",
                "siggraph",
            ),
            "Tech & Development",
        )

    def test_game_ai_explanation_is_technical(self):
        self.assertEqual(
            classify_lane(
                "Game AI expert explains where AI belongs in development",
                "A technical discussion of artificial intelligence and its risks",
                "80-level",
            ),
            "Tech & Development",
        )

    def test_asset_and_tool_release_is_technical(self):
        self.assertEqual(
            classify_lane(
                "Free Blender asset pack and procedural material add-on released",
                "The tool adds a production-ready environment workflow",
                "80-level",
            ),
            "Tech & Development",
        )

    def test_promotional_addon_article_remains_technical(self):
        self.assertEqual(
            classify_lane(
                "Blenderに階層ノードビューを実装するアドオンが登場、限定セール中",
                "Nukeライクな制作ツールの機能を紹介",
                "3dnchu",
            ),
            "Tech & Development",
        )

    def test_japanese_production_conference_is_technical(self):
        self.assertEqual(
            classify_lane(
                "CEDEC講演資料を公開",
                "開発チームがレンダリング技術と制作ワークフローを解説",
                "gamemakers",
            ),
            "Tech & Development",
        )

    def test_japanese_iteration_production_feature_is_technical(self):
        self.assertEqual(
            classify_lane(
                "ショートアニメ制作、アイデア出しとくり返しによるイテレーション制作スタイル",
                "チームの制作工程を紹介する特集",
                "cgworld",
            ),
            "Tech & Development",
        )

    def test_representative_live_feed_titles(self):
        cases = (
            (
                "オープンソースGUIライブラリ「raygui」、新版でAPI刷新",
                "gamemakers",
                "Tech & Development",
            ),
            (
                "『ウマ娘』から学ぶ創造的翻訳術【CEDEC2026】",
                "gamebusiness",
                "Tech & Development",
            ),
            (
                "森をテーマにした3Dモデルパック『Mini Forest』を無料公開",
                "gamemakers",
                "Tech & Development",
            ),
            ("Remote Asset Libraries", "blender-developers", "Tech & Development"),
            (
                "Capcom is Aiming to Release a Resident Evil Game Every Year",
                "80-level",
                "Industry",
            ),
            (
                "大規模ゲーム開発へ最大15億円補助する経産省の支援事業",
                "gamemakers",
                "Business",
            ),
        )
        for title, source_id, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_lane(title, "", source_id), expected)

    def test_closure_word_requires_programming_context(self):
        self.assertEqual(
            classify_lane(
                "Animation studio closures accelerate",
                "Companies face layoffs and difficult market conditions",
                "cartoon-brew",
            ),
            "Business",
        )

    def test_game_product_events_are_industry(self):
        cases = (
            ("DLC expansion roadmap announced", "Players get a new story chapter"),
            ("The new battle pass launches next week", "In-game pricing is unchanged"),
            ("Limited gacha banner adds playable characters", "A seasonal event starts Friday"),
            ("Game release date moves to October", "The player roadmap adds two updates"),
        )
        for title, summary in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_lane(title, summary, "automaton"), "Industry")

    def test_corporate_matters_are_business(self):
        cases = (
            ("EA to acquire Respawn", "The publisher plans to acquire the studio"),
            ("Microsoft acquires a game studio", "The deal expands its first-party portfolio"),
            ("Sony acquired a developer", "The transaction closed this quarter"),
            ("Publisher announces acquisition", "The merger closes this quarter"),
            ("Studio reports quarterly earnings", "Revenue and profit beat guidance"),
            ("Game company cuts its workforce", "Hundreds of employees are laid off"),
            ("CEO steps down after a lawsuit", "The company updates its policy"),
        )
        for title, summary in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_lane(title, summary, "gamebusiness"), "Business")

    def test_technical_company_explainer_stays_technical(self):
        self.assertEqual(
            classify_lane(
                "Company explains its real-time rendering pipeline",
                "A technical breakdown covers shader optimization and workflow",
                "gamebusiness",
            ),
            "Tech & Development",
        )

    def test_corporate_events_override_incidental_technical_terms(self):
        cases = (
            (
                "Studio layoffs hit the technical art team",
                "The workforce reduction follows a restructuring breakdown",
            ),
            (
                "Publisher acquisition changes the studio pipeline",
                "The merger closes after regulatory review",
            ),
        )
        for title, summary in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_lane(title, summary, "gamebusiness"), "Business")

    def test_context_qualified_studio_closures_are_business(self):
        cases = (
            ("Animation studio closure announced", "Operations will end this month"),
            ("Publisher closes its studio", "The location will cease operations"),
            ("Company closures continue", "Two subsidiaries will stop operating"),
        )
        for title, summary in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_lane(title, summary, "cartoon-brew"), "Business")
        self.assertEqual(
            classify_lane(
                "Bundles and Closures",
                "An API implementation note for Blender developers",
                "blender-developers",
            ),
            "Tech & Development",
        )


if __name__ == "__main__":
    unittest.main()
