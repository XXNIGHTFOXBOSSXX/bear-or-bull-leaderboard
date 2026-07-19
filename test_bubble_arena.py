import json
import math
import unittest

import pandas as pd

from bear_or_bull_leaderboard_dashboard import (
    BUBBLE_ARENA_MAX_RADIUS,
    BUBBLE_ARENA_MIN_RADIUS,
    build_bubble_arena_payload,
    build_leaderboard,
    filter_leaderboard_by_name,
    get_bubble_arena_ring,
    scale_bubble_radius,
)


def make_game(game_label, players):
    month, week = game_label.split()
    parsed_players = [
        {"name": name, "position": position}
        for position, name in enumerate(players, start=1)
    ]
    return {
        "file_name": f"{game_label} test.txt",
        "month": month,
        "week": week,
        "game": game_label,
        "month_sort": 1,
        "week_sort": int(week.replace("W", "")),
        "game_sort_key": (1, int(week.replace("W", ""))),
        "players": parsed_players,
        "total_entries": len(parsed_players),
        "unique_entries": len(players),
        "winner": players[0],
        "top_5_players": players[:5],
    }


class BubbleArenaTests(unittest.TestCase):
    def test_rank_boundaries_map_to_expected_rings(self):
        expected = {
            1: "champion",
            2: "legends",
            5: "legends",
            6: "contenders",
            20: "contenders",
            21: "challengers",
            50: "challengers",
            51: "field",
        }

        for rank, ring_id in expected.items():
            with self.subTest(rank=rank):
                self.assertEqual(get_bubble_arena_ring(rank)["id"], ring_id)

    def test_bubble_scaling_respects_bounds_and_equal_scores(self):
        low = scale_bubble_radius(1, 1, 100)
        high = scale_bubble_radius(100, 1, 100)
        equal_a = scale_bubble_radius(25, 1, 100)
        equal_b = scale_bubble_radius(25, 1, 100)

        self.assertGreaterEqual(low, BUBBLE_ARENA_MIN_RADIUS)
        self.assertLessEqual(high, BUBBLE_ARENA_MAX_RADIUS)
        self.assertEqual(equal_a, equal_b)
        self.assertEqual(scale_bubble_radius(None, 1, 100), BUBBLE_ARENA_MIN_RADIUS)
        self.assertEqual(scale_bubble_radius("bad", 1, 100), BUBBLE_ARENA_MIN_RADIUS)
        self.assertTrue(math.isfinite(scale_bubble_radius(10, 10, 10)))

    def test_payload_contains_json_safe_values_and_supported_stats(self):
        games = [
            make_game("JAN W1", ["Ace", "Bull", "Night <Fox>", "Emoji ❤️"]),
            make_game("JAN W2", ["Bull", "Ace", "Quote \"Player\"", "Night <Fox>"]),
        ]
        leaderboard, _ = build_leaderboard(games)
        payload = build_bubble_arena_payload(leaderboard, games, "Total Score")

        json.dumps(payload, ensure_ascii=False)
        self.assertEqual(len(payload["players"]), len(leaderboard))
        champion = next(player for player in payload["players"] if player["rank"] == 1)
        self.assertEqual(champion["ring"], "champion")
        self.assertIn("score", champion)
        self.assertIn("recentForm", champion)
        self.assertIn("rankMovement", champion)

    def test_search_is_case_insensitive(self):
        leaderboard = pd.DataFrame(
            [
                {"Rank": 1, "Player Name": "NightFox", "Total Score": 10},
                {"Rank": 2, "Player Name": "Other", "Total Score": 8},
            ]
        )

        matches = filter_leaderboard_by_name(leaderboard, "night")
        self.assertEqual(matches.iloc[0]["Player Name"], "NightFox")

    def test_empty_payload_is_safe(self):
        payload = build_bubble_arena_payload(pd.DataFrame(), [], "Total Score")
        self.assertEqual(payload["players"], [])
        self.assertEqual(payload["meta"]["visiblePlayers"], 0)

    def test_movement_is_omitted_when_no_previous_game_exists(self):
        games = [make_game("JAN W1", ["Ace", "Bull"])]
        leaderboard, _ = build_leaderboard(games)
        payload = build_bubble_arena_payload(leaderboard, games, "Total Score")

        self.assertTrue(all(player["previousRank"] is None for player in payload["players"]))
        self.assertTrue(all(player["rankMovement"] is None for player in payload["players"]))

    def test_movement_uses_previous_canonical_ranking_when_available(self):
        games = [
            make_game("JAN W1", ["Ace", "Bull", "Chip"]),
            make_game("JAN W2", ["Bull", "Ace", "Chip"]),
        ]
        leaderboard, _ = build_leaderboard(games)
        payload = build_bubble_arena_payload(leaderboard, games, "Total Score")
        bull = next(player for player in payload["players"] if player["name"] == "Bull")

        self.assertIsNotNone(bull["previousRank"])
        self.assertIsNotNone(bull["rankMovement"])

    def test_top_five_style_data_is_applied(self):
        games = [make_game("JAN W1", [f"Player {index}" for index in range(1, 60)])]
        leaderboard, _ = build_leaderboard(games)
        payload = build_bubble_arena_payload(leaderboard, games, "Total Score")
        top_five = [player for player in payload["players"] if player["rank"] <= 5]

        self.assertEqual(len(top_five), 5)
        self.assertTrue(all(player["ring"] in {"champion", "legends"} for player in top_five))


if __name__ == "__main__":
    unittest.main()
