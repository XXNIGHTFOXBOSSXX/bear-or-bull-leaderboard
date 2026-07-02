from collections import Counter, defaultdict
import base64
from html import escape
from pathlib import Path
import re

import pandas as pd
import streamlit as st


# Folder that contains the weekly BEAR OR BULL player list text files.
APP_FOLDER = Path(__file__).parent
PLAYER_FOLDER_OPTIONS = [
    APP_FOLDER / "DATA FOR EACH GAME" / "TOTAL PLAYERS",
    APP_FOLDER.parent / "DATA FOR EACH GAME" / "TOTAL PLAYERS",
    APP_FOLDER / "TOTAL PLAYERS",
]
PLAYERS_FOLDER = next(
    (folder for folder in PLAYER_FOLDER_OPTIONS if folder.exists()),
    PLAYER_FOLDER_OPTIONS[0],
)
LOGO_PATH = APP_FOLDER / "BEARORBULL.png"
LOGO_BASE64_PATH = APP_FOLDER / "BEARORBULL.base64.txt"

# Month aliases let filenames and folder names use short or long month names.
MONTH_ALIASES = {
    "JAN": "JAN",
    "JANUARY": "JAN",
    "FEB": "FEB",
    "FEBRUARY": "FEB",
    "MAR": "MARCH",
    "MARCH": "MARCH",
    "APR": "APRIL",
    "APRIL": "APRIL",
    "MAY": "MAY",
    "JUN": "JUNE",
    "JUNE": "JUNE",
    "JUL": "JULY",
    "JULY": "JULY",
    "AUG": "AUGUST",
    "AUGUST": "AUGUST",
    "SEP": "SEPTEMBER",
    "SEPT": "SEPTEMBER",
    "SEPTEMBER": "SEPTEMBER",
    "OCT": "OCTOBER",
    "OCTOBER": "OCTOBER",
    "NOV": "NOVEMBER",
    "NOVEMBER": "NOVEMBER",
    "DEC": "DECEMBER",
    "DECEMBER": "DECEMBER",
}
MONTH_ORDER = [
    "JAN",
    "FEB",
    "MARCH",
    "APRIL",
    "MAY",
    "JUNE",
    "JULY",
    "AUGUST",
    "SEPTEMBER",
    "OCTOBER",
    "NOVEMBER",
    "DECEMBER",
]
MONTH_SORT_ORDER = {
    month: month_index
    for month_index, month in enumerate(MONTH_ORDER, start=1)
}
MONTH_PATTERN = "|".join(sorted(MONTH_ALIASES, key=len, reverse=True))

def ordinal_number(number):
    """Turn 1 into 1st, 2 into 2nd, and so on."""
    if pd.isna(number):
        return ""

    number = int(number)
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def clean_player_name(name):
    """Remove IDs, tabs, and repeated spaces from a player name."""
    name = re.sub(r"\(ID:.*?\)", "", name)
    name = " ".join(name.replace("\t", " ").split())
    return name.strip()


def player_key(name):
    """Create a case-insensitive key so names count together cleanly."""
    return clean_player_name(name).casefold()


def read_text_file(file_path):
    """Read a text file with a small set of common encoding fallbacks."""
    encodings_to_try = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]

    for encoding in encodings_to_try:
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        "Could not read file with utf-8, cp1252, or latin-1.",
    )


def normalize_month_name(month_text):
    """Convert a short or long month name into the dashboard's canonical name."""
    if not month_text:
        return None

    return MONTH_ALIASES.get(month_text.strip().upper())


def get_month_sort_number(month):
    """Return the calendar order number for a canonical month name."""
    return MONTH_SORT_ORDER.get(month, 99)


def get_week_sort_number(week):
    """Return the numeric part of a week label such as W1 or W5."""
    match = re.search(r"\d+", str(week))
    if not match:
        return 99
    return int(match.group(0))


def get_file_details(file_path):
    """Pull the month and week out of a filename or its parent folder."""
    month = None
    week = None

    filename_month = re.search(
        rf"\b({MONTH_PATTERN})\b",
        file_path.stem,
        re.IGNORECASE,
    )
    if filename_month:
        month = normalize_month_name(filename_month.group(1))

    filename_week = re.search(r"\b(W[1-5])\b", file_path.stem, re.IGNORECASE)
    if filename_week:
        week = filename_week.group(1).upper()

    # If the filename is missing the month, try folder names such as "april".
    if not month:
        for folder_part in reversed(file_path.parent.parts):
            month = normalize_month_name(folder_part)
            if month:
                break

    if not month or not week:
        return None

    return {
        "month": month,
        "week": week,
        "game": f"{month} {week}",
        "month_sort": get_month_sort_number(month),
        "week_sort": get_week_sort_number(week),
        "game_sort_key": (get_month_sort_number(month), get_week_sort_number(week)),
    }


def parse_player_rows(text):
    """
    Parse player rows from a weekly poker export.

    Each row normally starts with a finishing position, then the player name.
    Example:
    1st    PlayerName (ID: abc123)    --    --    Finished
    """
    players_by_key = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        position_match = re.match(r"^(\d+)(?:st|nd|rd|th)\s+", line, re.IGNORECASE)

        if not position_match:
            continue

        position = int(position_match.group(1))
        rest_of_line = line[position_match.end() :]

        # The player name is usually the text before "(ID: ...)".
        id_match = re.search(r"\(ID:.*?\)", rest_of_line)
        if id_match:
            name = clean_player_name(rest_of_line[: id_match.start()])
        else:
            # Fallback for plain tab-separated rows without an ID value.
            name = clean_player_name(rest_of_line.split("\t")[0])

        if not name:
            continue

        key = player_key(name)

        # If the same player appears twice in a file, keep only their best finish.
        if key not in players_by_key or position < players_by_key[key]["position"]:
            players_by_key[key] = {
                "name": name,
                "position": position,
            }

    return list(players_by_key.values())


@st.cache_data(show_spinner=False)
def load_all_games():
    """Scan the player folder and load every usable weekly player list."""
    games = []
    warnings = []
    scan_info = {
        "files_found": 0,
        "folders_scanned": [],
    }

    if not PLAYERS_FOLDER.exists():
        return games, [f"Folder not found: {PLAYERS_FOLDER}"], scan_info

    scanned_folders = {PLAYERS_FOLDER}
    scanned_folders.update(path for path in PLAYERS_FOLDER.rglob("*") if path.is_dir())
    scan_info["folders_scanned"] = sorted(
        str(path.relative_to(PLAYERS_FOLDER)) if path != PLAYERS_FOLDER else "."
        for path in scanned_folders
    )

    player_files = sorted(PLAYERS_FOLDER.rglob("*.txt"))
    scan_info["files_found"] = len(player_files)

    for file_path in player_files:
        details = get_file_details(file_path)
        if not details:
            warnings.append(
                f"Skipped file with unexpected name: {file_path.relative_to(PLAYERS_FOLDER)}"
            )
            continue

        try:
            text = read_text_file(file_path)
        except Exception as error:
            warnings.append(
                f"Skipped unreadable file: {file_path.relative_to(PLAYERS_FOLDER)} ({error})"
            )
            continue

        if not text.strip():
            warnings.append(f"Skipped empty file: {file_path.relative_to(PLAYERS_FOLDER)}")
            continue

        players = parse_player_rows(text)
        if not players:
            warnings.append(
                f"Skipped file with no player rows found: {file_path.relative_to(PLAYERS_FOLDER)}"
            )
            continue

        player_names = [player["name"] for player in players]
        games.append(
            {
                "file_path": str(file_path),
                "file_name": str(file_path.relative_to(PLAYERS_FOLDER)),
                "month": details["month"],
                "week": details["week"],
                "game": details["game"],
                "month_sort": details["month_sort"],
                "week_sort": details["week_sort"],
                "game_sort_key": details["game_sort_key"],
                "players": players,
                "total_entries": len(players),
                "unique_entries": len({player_key(name) for name in player_names}),
                "winner": player_names[0] if player_names else "",
                "top_5_players": player_names[:5],
            }
        )

    games = sorted(
        games,
        key=lambda game: (game["month_sort"], game["week_sort"], game["file_name"]),
    )

    return games, warnings, scan_info


def build_leaderboard(games):
    """Build the ranked leaderboard from the selected game files."""
    player_stats = defaultdict(
        lambda: {
            "display_names": Counter(),
            "games_played": 0,
            "appearance_points": 0,
            "placement_bonus_points": 0,
            "best_finish": None,
            "first_place_finishes": 0,
            "top_5_finishes": 0,
            "top_20_finishes": 0,
            "months": set(),
            "weeks": set(),
        }
    )

    total_player_entries = 0

    for game in games:
        total_player_entries += len(game["players"])

        for player in game["players"]:
            name = player["name"]
            key = player_key(name)
            position = player["position"]
            bonus_points = max(21 - position, 0) if position <= 20 else 0
            stats = player_stats[key]

            stats["display_names"][name] += 1
            stats["games_played"] += 1
            stats["appearance_points"] += 1
            stats["placement_bonus_points"] += bonus_points
            stats["months"].add(game["month"])
            stats["weeks"].add(game["game"])

            if position == 1:
                stats["first_place_finishes"] += 1

            if position <= 5:
                stats["top_5_finishes"] += 1

            if position <= 20:
                stats["top_20_finishes"] += 1

            if stats["best_finish"] is None or position < stats["best_finish"]:
                stats["best_finish"] = position

    rows = []

    for stats in player_stats.values():
        display_name = stats["display_names"].most_common(1)[0][0]
        total_score = stats["appearance_points"] + stats["placement_bonus_points"]

        rows.append(
            {
                "Player Name": display_name,
                "Games Played": stats["games_played"],
                "Appearance Points": stats["appearance_points"],
                "Placement Bonus Points": stats["placement_bonus_points"],
                "Total Score": total_score,
                "Best Placement": stats["best_finish"],
                "1st Place Finishes": stats["first_place_finishes"],
                "Top 5 Finishes": stats["top_5_finishes"],
                "Top 20 Finishes": stats["top_20_finishes"],
                "Weeks Appeared In": ", ".join(sorted(stats["weeks"])),
                "Months Played": ", ".join(sorted(stats["months"])),
            }
        )

    leaderboard = pd.DataFrame(rows)

    if leaderboard.empty:
        return leaderboard, total_player_entries

    leaderboard = leaderboard.sort_values(
        by=[
            "Total Score",
            "Games Played",
            "Placement Bonus Points",
            "Best Placement",
            "Player Name",
        ],
        ascending=[False, False, False, True, True],
    ).reset_index(drop=True)

    leaderboard.insert(0, "Rank", leaderboard.index + 1)
    leaderboard["Best Placement"] = leaderboard["Best Placement"].apply(ordinal_number)

    return leaderboard, total_player_entries


@st.cache_data(show_spinner=False)
def get_logo_data_uri():
    """Return the logo as a browser-safe data URI, or None if it is missing."""
    if LOGO_PATH.exists():
        encoded_logo = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded_logo}"

    if LOGO_BASE64_PATH.exists():
        encoded_logo = "".join(LOGO_BASE64_PATH.read_text(encoding="ascii").split())
        return f"data:image/png;base64,{encoded_logo}"

    return None


def add_page_style():
    """Add premium dark casino styling to the Streamlit page."""
    st.markdown(
        """
        <style>
            :root {
                --bob-black: #050505;
                --bob-panel: rgba(12, 12, 10, 0.92);
                --bob-panel-soft: rgba(20, 17, 10, 0.86);
                --bob-gold: #d6af3b;
                --bob-gold-bright: #f1cf66;
                --bob-gold-soft: rgba(214, 175, 59, 0.28);
                --bob-text: #f7f0dc;
                --bob-muted: #b8aa82;
            }

            .stApp {
                background:
                    radial-gradient(circle at top left, rgba(214, 175, 59, 0.16), transparent 24%),
                    radial-gradient(circle at bottom right, rgba(20, 93, 48, 0.10), transparent 26%),
                    linear-gradient(135deg, #030303 0%, #0d0d0b 45%, #151006 100%);
                color: var(--bob-text);
            }

            div[data-testid="stAppViewContainer"] {
                padding-top: 0 !important;
            }

            .main {
                margin-top: 0 !important;
                padding-top: 0 !important;
            }

            [data-testid="stSidebar"] {
                background:
                    linear-gradient(180deg, #050505 0%, #0d0d0b 72%, #120d05 100%);
                border-right: 1px solid rgba(214, 175, 59, 0.26);
            }

            h1, h2, h3 {
                color: var(--bob-gold-bright);
                letter-spacing: 0;
            }

            .main .block-container {
                max-width: 1200px;
                padding-top: 0.15rem !important;
                padding-bottom: 2rem;
                margin-top: 0 !important;
            }

            header[data-testid="stHeader"] {
                background: transparent !important;
                height: 0 !important;
            }

            .brand-hero {
                border: 1px solid rgba(214, 175, 59, 0.26);
                background:
                    linear-gradient(135deg, rgba(24, 20, 10, 0.96), rgba(4, 4, 4, 0.94)),
                    radial-gradient(circle at top right, rgba(214, 175, 59, 0.18), transparent 36%);
                border-radius: 8px;
                padding: 12px 18px;
                margin-top: 0;
                margin-bottom: 6px;
                box-shadow: 0 18px 48px rgba(0, 0, 0, 0.34);
            }

            .brand-hero-layout {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 22px;
            }

            .brand-hero-copy {
                min-width: 0;
            }

            .brand-logo {
                width: 138px;
                max-width: 24vw;
                height: auto;
                border-radius: 999px;
                filter: drop-shadow(0 12px 28px rgba(214, 175, 59, 0.20));
            }

            .brand-kicker {
                color: var(--bob-gold);
                font-size: 0.72rem;
                font-weight: 800;
                letter-spacing: 0.12em;
                text-transform: uppercase;
                margin-bottom: 0.18rem;
            }

            .brand-title {
                color: #fff7df;
                font-size: clamp(1.7rem, 3.4vw, 2.7rem);
                font-weight: 900;
                line-height: 1.02;
                margin-bottom: 0.25rem;
            }

            .brand-subtitle {
                color: #d8c99a;
                font-size: 0.95rem;
                margin-bottom: 0;
            }

            .sidebar-brand {
                border: 1px solid rgba(214, 175, 59, 0.24);
                background: rgba(214, 175, 59, 0.06);
                border-radius: 8px;
                padding: 10px;
                margin-bottom: 12px;
                display: flex;
                align-items: center;
                gap: 10px;
            }

            .sidebar-logo {
                width: 62px;
                height: 62px;
                object-fit: contain;
                border-radius: 999px;
            }

            .sidebar-brand-title {
                color: var(--bob-gold-bright);
                font-size: 1rem;
                font-weight: 900;
            }

            .sidebar-brand-subtitle {
                color: var(--bob-muted);
                font-size: 0.82rem;
            }

            .section-heading {
                display: flex;
                align-items: center;
                gap: 12px;
                margin: 6px 0 5px 0;
            }

            .section-heading-title {
                color: #fff4cf;
                font-size: 1.22rem;
                font-weight: 900;
            }

            .section-heading-line {
                flex: 1;
                height: 1px;
                background: linear-gradient(90deg, rgba(214, 175, 59, 0.55), transparent);
            }

            .section-note {
                color: var(--bob-muted);
                margin: -4px 0 14px 0;
                font-size: 0.92rem;
            }

            .metric-card {
                border: 1px solid rgba(214, 175, 59, 0.26);
                background:
                    radial-gradient(circle at top right, rgba(214, 175, 59, 0.10), transparent 34%),
                    linear-gradient(180deg, rgba(22, 19, 10, 0.94), rgba(7, 7, 7, 0.95));
                border-radius: 8px;
                padding: 12px 14px;
                min-height: 76px;
                box-shadow: 0 14px 32px rgba(0, 0, 0, 0.24);
                transition: border-color 160ms ease, transform 160ms ease;
                text-align: center;
            }

            .metric-card:hover {
                border-color: rgba(241, 207, 102, 0.48);
                transform: translateY(-1px);
            }

            .metric-label {
                color: var(--bob-gold);
                font-size: 0.78rem;
                text-transform: uppercase;
                letter-spacing: 0.06em;
                margin-bottom: 0.35rem;
                text-align: center;
            }

            .metric-value {
                color: #ffffff;
                font-size: 1.55rem;
                font-weight: 800;
                line-height: 1.25;
                word-break: break-word;
                text-align: center;
            }

            .profile-card {
                border: 1px solid rgba(214, 175, 59, 0.32);
                background:
                    linear-gradient(135deg, rgba(23, 20, 12, 0.96), rgba(6, 6, 6, 0.95));
                border-radius: 8px;
                padding: 22px;
                margin: 8px 0 18px 0;
                box-shadow: 0 18px 42px rgba(0, 0, 0, 0.28);
            }

            .profile-name {
                color: var(--bob-gold-bright);
                font-size: 1.65rem;
                font-weight: 800;
                margin-bottom: 0.6rem;
            }

            .profile-weeks {
                color: #e5d8af;
                font-size: 0.92rem;
                line-height: 1.65;
            }

            .profile-grid {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 10px;
                margin: 12px 0;
            }

            .profile-stat {
                border: 1px solid rgba(214, 175, 59, 0.16);
                background: rgba(214, 175, 59, 0.05);
                border-radius: 8px;
                padding: 10px;
            }

            .profile-stat-label {
                color: var(--bob-muted);
                font-size: 0.72rem;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                margin-bottom: 4px;
            }

            .profile-stat-value {
                color: #ffffff;
                font-weight: 900;
                font-size: 1rem;
                word-break: break-word;
            }

            .insights-panel {
                border: 1px solid rgba(214, 175, 59, 0.28);
                background:
                    radial-gradient(circle at top right, rgba(214, 175, 59, 0.12), transparent 34%),
                    linear-gradient(180deg, rgba(18, 15, 9, 0.96), rgba(6, 6, 6, 0.97));
                border-radius: 8px;
                padding: 14px 16px;
                margin: 8px 0 14px 0;
            }

            .insights-title {
                color: #f1cf66;
                font-size: 1rem;
                font-weight: 900;
                letter-spacing: 0.04em;
                text-transform: uppercase;
                margin-bottom: 8px;
                text-align: center;
            }

            .insights-panel ul {
                margin: 0;
                padding-left: 1.2rem;
                color: #f5ead2;
            }

            .insights-panel li {
                margin-bottom: 5px;
            }

            div[data-testid="stDownloadButton"] button,
            div[data-testid="stButton"] button {
                background: linear-gradient(135deg, #c69a2c, #7b5a16);
                color: #fff4cf;
                border: 1px solid rgba(241, 207, 102, 0.35);
                font-weight: 800;
                border-radius: 7px;
                box-shadow: 0 6px 14px rgba(0, 0, 0, 0.20);
                padding: 0.38rem 0.72rem;
                min-height: 2.15rem;
            }

            div[data-testid="stDownloadButton"] {
                margin-top: -8px;
                margin-bottom: -4px;
                width: fit-content;
            }

            div[data-testid="stDownloadButton"] button:hover,
            div[data-testid="stButton"] button:hover {
                background: linear-gradient(135deg, #d4af37, #8d6718);
                border-color: rgba(241, 207, 102, 0.60);
                color: #ffffff;
            }

            div[data-testid="stTabs"] button {
                color: var(--bob-muted);
                font-weight: 800;
            }

            div[data-testid="stTabs"] button[aria-selected="true"] {
                color: var(--bob-gold-bright);
            }

            div[data-testid="stDataFrame"] {
                border: 1px solid rgba(214, 175, 59, 0.24);
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 16px 34px rgba(0, 0, 0, 0.22);
                background: #eee7d7;
            }

            .performer-card {
                border: 1px solid rgba(214, 175, 59, 0.34);
                background:
                    radial-gradient(circle at top right, rgba(214, 175, 59, 0.14), transparent 38%),
                    linear-gradient(180deg, rgba(17, 15, 9, 0.96), rgba(6, 6, 6, 0.97));
                border-radius: 8px;
                padding: 10px 12px 9px 12px;
                min-height: 92px;
                box-shadow: 0 12px 26px rgba(0, 0, 0, 0.22);
                text-align: center;
            }

            .performer-label {
                color: #bca55e;
                font-size: 0.68rem;
                font-weight: 900;
                letter-spacing: 0.06em;
                line-height: 1.1;
                text-transform: uppercase;
                margin-bottom: 7px;
                text-align: center;
            }

            .performer-name {
                color: #fff4cf;
                font-size: 0.98rem;
                font-weight: 900;
                line-height: 1.15;
                min-height: 34px;
                word-break: break-word;
                text-align: center;
            }

            .performer-value-mark {
                display: inline-block;
                margin-top: 7px;
                padding: 4px 12px;
                border: 1px solid rgba(241, 207, 102, 0.52);
                border-radius: 999px;
                background: linear-gradient(135deg, #e3bd4a, #8b661b);
                color: #101008;
                font-size: 1.35rem;
                font-weight: 950;
                line-height: 1.15;
                min-width: 52px;
                text-align: center;
                box-shadow: 0 8px 18px rgba(0, 0, 0, 0.24);
            }

            .stSelectbox label,
            .stTextInput label,
            .stCheckbox label {
                color: var(--bob-muted) !important;
                font-weight: 700;
            }

            @media (max-width: 980px) {
                .profile-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
            }

            @media (max-width: 640px) {
                .brand-hero-layout {
                    align-items: flex-start;
                }

                .brand-logo {
                    width: 86px;
                }

                .profile-grid {
                    grid-template-columns: 1fr;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def show_hero_header():
    """Show the premium BEAR OR BULL header."""
    logo_html = ""
    logo_data_uri = get_logo_data_uri()
    if logo_data_uri:
        logo_html = f'<img class="brand-logo" src="{logo_data_uri}" alt="BEAR OR BULL logo">'

    st.markdown(
        f"""
        <div class="brand-hero">
            <div class="brand-hero-layout">
                <div class="brand-hero-copy">
                    <div class="brand-kicker">2026 Season | High Stakes Standings</div>
                    <div class="brand-title">BEAR OR BULL Poker Leaderboard</div>
                    <div class="brand-subtitle">
                        Weekly attendance, placement bonuses, and total leaderboard score.
                    </div>
                </div>
                {logo_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def show_sidebar_brand():
    """Show a compact sidebar brand card with a logo when available."""
    logo_html = ""
    logo_data_uri = get_logo_data_uri()
    if logo_data_uri:
        logo_html = f'<img class="sidebar-logo" src="{logo_data_uri}" alt="BEAR OR BULL logo">'

    st.markdown(
        f"""
        <div class="sidebar-brand">
            {logo_html}
            <div>
                <div class="sidebar-brand-title">BEAR OR BULL</div>
                <div class="sidebar-brand-subtitle">Poker Dashboard</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def show_section_heading(title, note=""):
    """Show a premium section heading with an optional support note."""
    st.markdown(
        f"""
        <div class="section-heading">
            <div class="section-heading-title">{escape(title)}</div>
            <div class="section-heading-line"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if note:
        st.markdown(
            f'<div class="section-note">{escape(note)}</div>',
            unsafe_allow_html=True,
        )


def show_metric_card(label, value):
    """Show a compact branded metric card."""
    safe_label = escape(str(label))
    safe_value = escape(str(value))
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{safe_label}</div>
            <div class="metric-value">{safe_value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def filter_games(games, selected_month="ALL", selected_week="ALL"):
    """Return only the games that match the selected month and week."""
    filtered_games = games

    if selected_month != "ALL":
        filtered_games = [game for game in filtered_games if game["month"] == selected_month]
    if selected_week != "ALL":
        filtered_games = [game for game in filtered_games if game["week"] == selected_week]

    return filtered_games


def get_filter_options(games):
    """Build month and week filter options from the files that were loaded."""
    found_months = {game["month"] for game in games}
    found_weeks = {game["week"] for game in games}
    month_options = ["ALL"] + [
        month for month in MONTH_ORDER if month in found_months
    ]
    week_options = ["ALL"] + [
        f"W{week_number}"
        for week_number in range(1, 6)
        if f"W{week_number}" in found_weeks
    ]

    return month_options, week_options


def filter_leaderboard_by_name(leaderboard, search_name):
    """Search the leaderboard by player name without changing the scoring."""
    if not search_name or leaderboard.empty:
        return leaderboard

    return leaderboard[
        leaderboard["Player Name"].str.contains(search_name, case=False, na=False)
    ]


def get_summary_stats(leaderboard):
    """Create the extra summary values used in the top stat cards."""
    if leaderboard.empty:
        return {
            "number_one": "No data",
            "highest_score": 0,
            "most_games_played": 0,
        }

    return {
        "number_one": leaderboard.iloc[0]["Player Name"],
        "highest_score": int(leaderboard["Total Score"].max()),
        "most_games_played": int(leaderboard["Games Played"].max()),
    }


def show_summary_cards(leaderboard, game_count, total_player_entries):
    """Show the clean dashboard stat cards."""
    summary = get_summary_stats(leaderboard)
    total_unique_players = 0 if leaderboard.empty else leaderboard["Player Name"].nunique()

    first_row = st.columns(3)
    with first_row[0]:
        show_metric_card("Total Unique Players", total_unique_players)
    with first_row[1]:
        show_metric_card("Total Game Files Found", game_count)
    with first_row[2]:
        show_metric_card("Total Player Entries", total_player_entries)

    second_row = st.columns(3)
    with second_row[0]:
        show_metric_card("Current #1 Player", summary["number_one"])
    with second_row[1]:
        show_metric_card("Highest Total Score", summary["highest_score"])
    with second_row[2]:
        show_metric_card("Most Games Played", summary["most_games_played"])


def get_top_performer(leaderboard, stat_column):
    """Return the player with the highest value in a stat column."""
    if leaderboard.empty or stat_column not in leaderboard.columns:
        return "No data", 0

    sorted_players = leaderboard.sort_values(
        by=[stat_column, "Total Score", "Games Played", "Player Name"],
        ascending=[False, False, False, True],
    )
    top_player = sorted_players.iloc[0]
    return top_player["Player Name"], int(top_player[stat_column])


def show_top_performers_strip(leaderboard):
    """Show a compact strip of standout leaderboard stats."""
    if leaderboard.empty:
        return

    most_prize_name, most_prize_count = get_top_performer(
        leaderboard,
        "Top 5 Finishes",
    )
    most_first_name, most_first_count = get_top_performer(
        leaderboard,
        "1st Place Finishes",
    )
    most_games_name, most_games_count = get_top_performer(leaderboard, "Games Played")
    highest_score_name, highest_score_count = get_top_performer(
        leaderboard,
        "Total Score",
    )

    performers = [
        ("Most Top 5 Finishes", most_prize_name, most_prize_count),
        ("Most 1st Place Finishes", most_first_name, most_first_count),
        ("Most Games Played", most_games_name, most_games_count),
        ("Highest Total Score", highest_score_name, highest_score_count),
    ]

    columns = st.columns(4)
    for column, (label, name, value) in zip(columns, performers):
        with column:
            st.markdown(
                f"""
                <div class="performer-card">
                    <div class="performer-label">{escape(label)}</div>
                    <div class="performer-name">{escape(str(name))}</div>
                    <div class="performer-value-mark">{value}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def show_player_profile(leaderboard, search_name):
    """Show a profile card for the best matching player search result."""
    if not search_name:
        st.info("Search for a player to see their profile card.")
        return

    matches = filter_leaderboard_by_name(leaderboard, search_name)
    if matches.empty:
        st.warning("No matching player found.")
        return

    # The leaderboard is already sorted by rank, so the first match is the best match.
    player = matches.iloc[0]
    player_name = escape(str(player["Player Name"]))
    weeks_appeared_in = escape(str(player["Weeks Appeared In"]))
    st.markdown(
        f"""
        <div class="profile-card">
            <div class="profile-name">{player_name}</div>
            <div class="profile-grid">
                <div class="profile-stat">
                    <div class="profile-stat-label">Overall Rank</div>
                    <div class="profile-stat-value">{player["Rank"]}</div>
                </div>
                <div class="profile-stat">
                    <div class="profile-stat-label">Total Score</div>
                    <div class="profile-stat-value">{player["Total Score"]}</div>
                </div>
                <div class="profile-stat">
                    <div class="profile-stat-label">Games Played</div>
                    <div class="profile-stat-value">{player["Games Played"]}</div>
                </div>
                <div class="profile-stat">
                    <div class="profile-stat-label">Best Placement</div>
                    <div class="profile-stat-value">{escape(str(player["Best Placement"]))}</div>
                </div>
                <div class="profile-stat">
                    <div class="profile-stat-label">1st Place Finishes</div>
                    <div class="profile-stat-value">{player["1st Place Finishes"]}</div>
                </div>
                <div class="profile-stat">
                    <div class="profile-stat-label">Top 5 Finishes</div>
                    <div class="profile-stat-value">{player["Top 5 Finishes"]}</div>
                </div>
                <div class="profile-stat">
                    <div class="profile-stat-label">Top 20 Finishes</div>
                    <div class="profile-stat-value">{player["Top 20 Finishes"]}</div>
                </div>
                <div class="profile-stat">
                    <div class="profile-stat-label">Appearance Points</div>
                    <div class="profile-stat-value">{player["Appearance Points"]}</div>
                </div>
                <div class="profile-stat">
                    <div class="profile-stat-label">Placement Bonus</div>
                    <div class="profile-stat-value">{player["Placement Bonus Points"]}</div>
                </div>
            </div>
            <div class="profile-weeks">
                <strong>Weeks Appeared In:</strong> {weeks_appeared_in}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def style_leaderboard_table(table):
    """Apply a softer, easier-to-read style to leaderboard tables."""
    if table.empty:
        return table

    rank_highlights = {
        1: "background-color: #e5d092; color: #120f08; font-weight: 900;",
        2: "background-color: #d7d5ce; color: #121212; font-weight: 850;",
        3: "background-color: #d7b486; color: #160f08; font-weight: 850;",
        4: "background-color: #e8dcae; color: #17130a; font-weight: 800;",
        5: "background-color: #e8dcae; color: #17130a; font-weight: 800;",
    }

    def highlight_ranked_rows(row):
        rank = row.get("Rank")
        if rank in rank_highlights:
            return [rank_highlights[rank] for _ in row]
        return ["" for _ in row]

    styled_table = table.style.set_properties(
        **{
            "background-color": "#eee7d7",
            "color": "#17130a",
            "border-color": "#d5c8a7",
            "font-size": "0.94rem",
            "padding": "10px 12px",
            "text-align": "center",
        }
    )
    if "Player Name" in table.columns:
        styled_table = styled_table.set_properties(
            subset=["Player Name"],
            **{
                "text-align": "center",
                "font-weight": "700",
            },
        )

    return styled_table.set_table_styles(
        [
            {
                "selector": "th",
                "props": [
                    ("background-color", "#0d0b07"),
                    ("color", "#f4d778"),
                    ("font-weight", "900"),
                    ("text-align", "center"),
                    ("padding", "12px 12px"),
                    ("border-color", "#544015"),
                    ("border-bottom", "2px solid #b88b22"),
                ],
            },
            {
                "selector": "tbody tr:nth-child(even) td",
                "props": [("background-color", "#e5dcc8")],
            },
            {
                "selector": "td",
                "props": [("border-color", "#d5c8a7")],
            },
        ]
    ).apply(highlight_ranked_rows, axis=1)


def show_scrollable_table(table, height=600):
    """Show a readable table directly, with scrolling only when there are many rows."""
    st.dataframe(
        style_leaderboard_table(table),
        use_container_width=True,
        hide_index=True,
        height=height,
    )


def show_full_height_table(table):
    """Show a short table at full height without an internal vertical scroll."""
    row_height = 36
    header_height = 42
    extra_padding = 18
    table_height = header_height + (len(table) * row_height) + extra_padding

    st.dataframe(
        style_leaderboard_table(table),
        use_container_width=True,
        hide_index=True,
        height=table_height,
    )


def show_leaderboard_tables(
    leaderboard,
    search_name="",
    show_weeks=False,
    export_button_key="leaderboard_csv_export",
):
    """Show the Top 20 table, export button, and full searchable table."""
    if leaderboard.empty:
        st.info("No leaderboard data found for the selected filters.")
        return

    top_20_columns = [
        "Rank",
        "Player Name",
        "Games Played",
        "Total Score",
        "Best Placement",
        "1st Place Finishes",
        "Top 5 Finishes",
        "Top 20 Finishes",
    ]
    full_columns = [
        "Rank",
        "Player Name",
        "Games Played",
        "Appearance Points",
        "Placement Bonus Points",
        "Total Score",
        "Best Placement",
        "1st Place Finishes",
        "Top 5 Finishes",
        "Top 20 Finishes",
        "Months Played",
    ]

    if show_weeks:
        full_columns.append("Weeks Appeared In")

    visible_leaderboard = filter_leaderboard_by_name(leaderboard, search_name)

    show_top_performers_strip(leaderboard)

    show_section_heading("Top 20 Players")
    show_full_height_table(leaderboard[top_20_columns].head(20))

    export_columns = [
        "Rank",
        "Player Name",
        "Games Played",
        "Appearance Points",
        "Placement Bonus Points",
        "Total Score",
        "Best Placement",
        "1st Place Finishes",
        "Top 5 Finishes",
        "Top 20 Finishes",
        "Weeks Appeared In",
    ]
    csv_data = visible_leaderboard[export_columns].to_csv(index=False).encode("utf-8")
    st.download_button(
        "Export leaderboard as CSV",
        data=csv_data,
        file_name="bear_or_bull_leaderboard.csv",
        mime="text/csv",
        key=export_button_key,
    )

    show_section_heading("Full Searchable Leaderboard")
    show_scrollable_table(
        visible_leaderboard[full_columns],
        height=660,
    )


def get_game_attendance_table(games):
    """Build one analytics row per loaded game file."""
    rows = []

    for game in games:
        rows.append(
            {
                "Game": game["game"],
                "Month": game["month"],
                "Week": game["week"],
                "Month Sort Number": game["month_sort"],
                "Week Sort Number": game["week_sort"],
                "Game Sort Key": f"{game['month_sort']:02d}-{game['week_sort']:02d}",
                "Player Entries": game["total_entries"],
                "Unique Players In Game": game["unique_entries"],
                "Winner / 1st Place": game["winner"],
                "Top 5 Players": ", ".join(game["top_5_players"]),
            }
        )

    attendance_table = pd.DataFrame(rows)
    if not attendance_table.empty:
        attendance_table = attendance_table.sort_values(
            ["Month Sort Number", "Week Sort Number", "Game"],
            ascending=[True, True, True],
        ).reset_index(drop=True)
    return attendance_table


def get_monthly_analytics_table(games):
    """Build monthly analytics rows in calendar order."""
    rows = []

    for month in MONTH_ORDER:
        month_games = [game for game in games if game["month"] == month]
        if not month_games:
            continue

        month_entries = sum(len(game["players"]) for game in month_games)
        month_unique_players = {
            player_key(player["name"])
            for game in month_games
            for player in game["players"]
        }
        highest_game = sorted(
            month_games,
            key=lambda game: (-len(game["players"]), game["week_sort"], game["file_name"]),
        )[0]
        lowest_game = sorted(
            month_games,
            key=lambda game: (len(game["players"]), game["week_sort"], game["file_name"]),
        )[0]
        month_leaderboard, _ = build_leaderboard(month_games)

        if month_leaderboard.empty:
            top_player = ""
            top_score = 0
        else:
            top_player = month_leaderboard.iloc[0]["Player Name"]
            top_score = int(month_leaderboard.iloc[0]["Total Score"])

        rows.append(
            {
                "Month": month,
                "Month Sort Number": get_month_sort_number(month),
                "Games Played": len(month_games),
                "Total Entries": month_entries,
                "Unique Players": len(month_unique_players),
                "Average Players Per Game": round(month_entries / len(month_games), 1),
                "Highest Attendance Game": (
                    f"{highest_game['game']} ({len(highest_game['players'])})"
                ),
                "Lowest Attendance Game": (
                    f"{lowest_game['game']} ({len(lowest_game['players'])})"
                ),
                "Top Player": top_player,
                "Top Player Score": top_score,
            }
        )

    monthly_table = pd.DataFrame(rows)
    if not monthly_table.empty:
        monthly_table = monthly_table.sort_values("Month Sort Number").reset_index(drop=True)
    return monthly_table


def show_analytics_core_stats(games, leaderboard):
    """Show high-level season stats for the selected analytics scope."""
    show_section_heading("Core Season Stats")

    if not games or leaderboard.empty:
        st.info("No analytics data available for the selected filters.")
        return

    game_table = get_game_attendance_table(games)
    total_entries = int(game_table["Player Entries"].sum())
    unique_players = {
        player_key(player["name"])
        for game in games
        for player in game["players"]
    }
    highest_game = game_table.sort_values("Player Entries", ascending=False).iloc[0]
    lowest_game = game_table.sort_values("Player Entries", ascending=True).iloc[0]
    monthly_table = get_monthly_analytics_table(games)
    most_active_month = monthly_table.sort_values("Total Entries", ascending=False).iloc[0]
    current_leader = leaderboard.iloc[0]

    cards = [
        ("Total Games Played", len(games)),
        ("Total Player Entries", total_entries),
        ("Total Unique Players", len(unique_players)),
        ("Average Players Per Game", round(total_entries / len(games), 1)),
        (
            "Highest Attendance Game",
            f"{highest_game['Game']} ({highest_game['Player Entries']})",
        ),
        (
            "Lowest Attendance Game",
            f"{lowest_game['Game']} ({lowest_game['Player Entries']})",
        ),
        (
            "Most Active Month",
            f"{most_active_month['Month']} ({most_active_month['Total Entries']})",
        ),
        (
            "Current Leader",
            f"{current_leader['Player Name']} ({current_leader['Total Score']})",
        ),
    ]

    for row_start in range(0, len(cards), 4):
        columns = st.columns(4)
        for column, (label, value) in zip(columns, cards[row_start : row_start + 4]):
            with column:
                show_metric_card(label, value)


def show_season_insights(games, leaderboard):
    """Show concise auto-generated insights for the selected analytics scope."""
    if not games or leaderboard.empty:
        return

    game_table = get_game_attendance_table(games)
    monthly_table = get_monthly_analytics_table(games)
    biggest_game = game_table.sort_values("Player Entries", ascending=False).iloc[0]
    smallest_game = game_table.sort_values("Player Entries", ascending=True).iloc[0]
    most_active_month = monthly_table.sort_values("Total Entries", ascending=False).iloc[0]
    current_leader = leaderboard.iloc[0]
    prize_name, prize_count = get_top_performer(leaderboard, "Top 5 Finishes")
    average_game_size = round(game_table["Player Entries"].mean(), 1)

    insights = [
        f"Biggest game so far: {biggest_game['Game']} with {biggest_game['Player Entries']} players.",
        f"Smallest game so far: {smallest_game['Game']} with {smallest_game['Player Entries']} players.",
        f"Most active month: {most_active_month['Month']} with {most_active_month['Total Entries']} entries.",
        f"Current leader: {current_leader['Player Name']} with {current_leader['Total Score']} points.",
        f"Most top 5 finishes: {prize_name} with {prize_count}.",
        f"Average game size: {average_game_size} players.",
    ]

    insight_items = "".join(f"<li>{escape(insight)}</li>" for insight in insights)
    st.markdown(
        f"""
        <div class="insights-panel">
            <div class="insights-title">Season Insights</div>
            <ul>{insight_items}</ul>
        </div>
        """,
        unsafe_allow_html=True,
    )


def show_chart(chart_data, x_column, y_column):
    """Show a compact dark/gold bar chart or a friendly empty message."""
    if chart_data.empty or x_column not in chart_data or y_column not in chart_data:
        st.info("Not enough data for this chart yet.")
        return

    plot_data = chart_data[[x_column, y_column]].copy()
    plot_data[y_column] = pd.to_numeric(plot_data[y_column], errors="coerce").fillna(0)
    plot_data[x_column] = plot_data[x_column].astype(str)

    chart_spec = {
        "mark": {
            "type": "bar",
            "color": "#d6a739",
            "stroke": "#f1cf66",
            "strokeWidth": 0.6,
            "cornerRadiusTopLeft": 3,
            "cornerRadiusTopRight": 3,
        },
        "encoding": {
            "x": {
                "field": x_column,
                "type": "nominal",
                "sort": None,
                "axis": {
                    "labelAngle": -35,
                    "labelColor": "#f5ead2",
                    "title": None,
                    "grid": False,
                },
            },
            "y": {
                "field": y_column,
                "type": "quantitative",
                "axis": {
                    "labelColor": "#f5ead2",
                    "titleColor": "#f5ead2",
                    "gridColor": "#3a3020",
                },
            },
        },
        "height": 250,
        "background": "#0b0905",
        "config": {
            "view": {"stroke": "#3a3020"},
            "axis": {
                "domainColor": "#3a3020",
                "tickColor": "#3a3020",
                "labelFontSize": 11,
                "titleFontSize": 12,
            },
        },
    }
    st.vega_lite_chart(plot_data, chart_spec, use_container_width=True)


def get_visible_attendance_columns(game_table):
    """Return the public attendance columns without internal sort helpers."""
    return [
        "Game",
        "Month",
        "Week",
        "Player Entries",
        "Unique Players In Game",
        "Winner / 1st Place",
        "Top 5 Players",
    ]


def get_visible_monthly_columns(monthly_table):
    """Return the public monthly columns without internal sort helpers."""
    return [
        "Month",
        "Games Played",
        "Total Entries",
        "Unique Players",
        "Average Players Per Game",
        "Highest Attendance Game",
        "Lowest Attendance Game",
        "Top Player",
        "Top Player Score",
    ]


def show_player_performance_tables(leaderboard):
    """Show compact top-10 player performance analytics tables."""
    show_section_heading("Player Performance Analytics")

    if leaderboard.empty:
        st.info("No player performance data available.")
        return

    tables = [
        (
            "Top 10 By Total Score",
            leaderboard.sort_values("Total Score", ascending=False).head(10),
            [
                "Rank",
                "Player Name",
                "Total Score",
                "Games Played",
                "Best Placement",
                "1st Place Finishes",
                "Top 5 Finishes",
                "Top 20 Finishes",
            ],
        ),
        (
            "Top 10 By Games Played",
            leaderboard.sort_values(
                ["Games Played", "Total Score", "Player Name"],
                ascending=[False, False, True],
            ).head(10),
            ["Rank", "Player Name", "Games Played", "Total Score"],
        ),
        (
            "Top 10 By Top 5 Finishes",
            leaderboard.sort_values(
                ["Top 5 Finishes", "Total Score", "Player Name"],
                ascending=[False, False, True],
            ).head(10),
            ["Rank", "Player Name", "Top 5 Finishes", "Total Score", "Games Played"],
        ),
        (
            "Top 10 By 1st Place Finishes",
            leaderboard.sort_values(
                ["1st Place Finishes", "Total Score", "Player Name"],
                ascending=[False, False, True],
            ).head(10),
            ["Rank", "Player Name", "1st Place Finishes", "Total Score", "Games Played"],
        ),
        (
            "Top 10 By Top 20 Finishes",
            leaderboard.sort_values(
                ["Top 20 Finishes", "Total Score", "Player Name"],
                ascending=[False, False, True],
            ).head(10),
            ["Rank", "Player Name", "Top 20 Finishes", "Total Score", "Games Played"],
        ),
    ]

    for index in range(0, len(tables), 2):
        columns_layout = st.columns(2)
        for layout_column, table_config in zip(columns_layout, tables[index : index + 2]):
            title, table, columns = table_config
            with layout_column:
                st.markdown(f"**{title}**")
                show_scrollable_table(table[columns], height=310)


def show_analytics_tab(analytics_games, all_games):
    """Show the Analytics tab using already-loaded game data."""
    analytics_leaderboard, _ = build_leaderboard(analytics_games)
    monthly_table = get_monthly_analytics_table(all_games)
    game_table = get_game_attendance_table(analytics_games)
    analytics_overview, analytics_attendance, analytics_players, analytics_monthly = st.tabs(
        ["Overview", "Attendance", "Players", "Monthly"]
    )

    with analytics_overview:
        show_analytics_core_stats(analytics_games, analytics_leaderboard)
        show_season_insights(analytics_games, analytics_leaderboard)

    with analytics_attendance:
        show_section_heading("Attendance Trends")
        chart_columns = st.columns(2)
        with chart_columns[0]:
            st.markdown("**Monthly Total Entries**")
            show_chart(monthly_table, "Month", "Total Entries")
        with chart_columns[1]:
            st.markdown("**Monthly Unique Players**")
            show_chart(monthly_table, "Month", "Unique Players")

        st.markdown("**Entries Per Game**")
        show_chart(game_table, "Game", "Player Entries")

        if game_table.empty:
            st.info("No game attendance data available for the selected filters.")
        else:
            visible_game_table = game_table[get_visible_attendance_columns(game_table)]
            with st.expander("View Game Attendance Details", expanded=False):
                show_scrollable_table(visible_game_table, height=520)
            st.download_button(
                "Export game attendance as CSV",
                data=visible_game_table.to_csv(index=False).encode("utf-8"),
                file_name="bear_or_bull_game_attendance.csv",
                mime="text/csv",
                key="analytics_game_attendance_csv_export",
            )

    with analytics_players:
        show_player_performance_tables(analytics_leaderboard)
        if not analytics_leaderboard.empty:
            st.download_button(
                "Export player performance as CSV",
                data=analytics_leaderboard.to_csv(index=False).encode("utf-8"),
                file_name="bear_or_bull_player_performance.csv",
                mime="text/csv",
                key="analytics_player_performance_csv_export",
            )

            chart_columns = st.columns(2)
            with chart_columns[0]:
                st.markdown("**Top 10 Total Score**")
                top_score_chart = analytics_leaderboard.head(10)[
                    ["Player Name", "Total Score"]
                ]
                show_chart(top_score_chart, "Player Name", "Total Score")
            with chart_columns[1]:
                st.markdown("**Top 10 Top 5 Finishes**")
                prize_chart = analytics_leaderboard.sort_values(
                    ["Top 5 Finishes", "Total Score", "Player Name"],
                    ascending=[False, False, True],
                ).head(10)[["Player Name", "Top 5 Finishes"]]
                show_chart(prize_chart, "Player Name", "Top 5 Finishes")

    with analytics_monthly:
        show_section_heading("Monthly Analytics")
        if monthly_table.empty:
            st.info("No monthly analytics data available.")
        else:
            monthly_table = monthly_table.copy()
            monthly_table["Average Players Per Game"] = monthly_table[
                "Average Players Per Game"
            ].map(lambda value: f"{value:.1f}")
            visible_monthly_table = monthly_table[get_visible_monthly_columns(monthly_table)]
            show_full_height_table(visible_monthly_table)
            st.download_button(
                "Export monthly analytics as CSV",
                data=visible_monthly_table.to_csv(index=False).encode("utf-8"),
                file_name="bear_or_bull_monthly_analytics.csv",
                mime="text/csv",
                key="analytics_monthly_csv_export",
            )


def main():
    st.set_page_config(
        page_title="BEAR OR BULL Poker Leaderboard",
        layout="wide",
    )
    add_page_style()
    show_hero_header()

    games, warnings, scan_info = load_all_games()
    month_options, week_options = get_filter_options(games)
    monthly_options = month_options[1:] if len(month_options) > 1 else []

    with st.sidebar:
        show_sidebar_brand()
        st.header("Filters")
        selected_month = st.selectbox("Overall month", month_options)
        selected_week = st.selectbox("Week", week_options)
        search_name = st.text_input("Search player name", placeholder="Type a player name...")
        show_weeks = st.checkbox("Show weeks appeared in", value=False)

        st.divider()
        st.caption(f"Reading files from: {PLAYERS_FOLDER}")
        st.caption(f"Total .txt files found: {scan_info['files_found']}")
        st.caption(f"Folders scanned: {len(scan_info['folders_scanned'])}")
        with st.expander("Scanned folders", expanded=False):
            for folder_name in scan_info["folders_scanned"]:
                st.caption(folder_name)

        if st.button("Refresh data"):
            st.cache_data.clear()
            st.rerun()

    for warning in warnings:
        st.warning(warning)

    overall_games = filter_games(games, selected_month, selected_week)
    overall_leaderboard, overall_entries = build_leaderboard(overall_games)

    all_games_leaderboard, _ = build_leaderboard(games)

    overall_tab, monthly_tab, player_tab, analytics_tab = st.tabs(
        ["Overall Leaderboard", "Monthly View", "Player Search", "Analytics"]
    )

    with overall_tab:
        show_summary_cards(overall_leaderboard, len(overall_games), overall_entries)
        show_leaderboard_tables(
            overall_leaderboard,
            search_name,
            show_weeks,
            export_button_key="overall_leaderboard_csv_export",
        )

    with monthly_tab:
        if not monthly_options:
            st.info("No monthly files were found.")
        else:
            monthly_month = st.selectbox("Monthly leaderboard", monthly_options)
            monthly_games = filter_games(games, monthly_month, "ALL")
            monthly_leaderboard, monthly_entries = build_leaderboard(monthly_games)

            show_summary_cards(monthly_leaderboard, len(monthly_games), monthly_entries)
            show_leaderboard_tables(
                monthly_leaderboard,
                "",
                show_weeks,
                export_button_key="monthly_leaderboard_csv_export",
            )

    with player_tab:
        show_section_heading("Player Profile")
        show_player_profile(all_games_leaderboard, search_name)

        if search_name:
            show_section_heading("Matching Players")
            matching_players = filter_leaderboard_by_name(all_games_leaderboard, search_name)
            player_search_columns = [
                "Rank",
                "Player Name",
                "Games Played",
                "Appearance Points",
                "Placement Bonus Points",
                "Total Score",
                "Best Placement",
                "1st Place Finishes",
                "Top 5 Finishes",
                "Top 20 Finishes",
            ]
            if show_weeks:
                player_search_columns.append("Weeks Appeared In")

            show_scrollable_table(
                matching_players[player_search_columns],
                height=420,
            )

    with analytics_tab:
        show_analytics_tab(overall_games, games)


if __name__ == "__main__":
    main()
