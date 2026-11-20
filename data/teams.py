"""NFL teams and their emoji.

A server's custom emoji named after a team's abbr (e.g. "ne") is used if
it exists; otherwise a standard Unicode emoji.
"""

import discord

# ESPN display name -> emoji name and short name.
teams = {
    "New England Patriots": {"abbr": "ne", "short": "Patriots"},
    "Buffalo Bills": {"abbr": "buf", "short": "Bills"},
    "New York Jets": {"abbr": "nyj", "short": "Jets"},
    "Miami Dolphins": {"abbr": "mia", "short": "Dolphins"},
    "Baltimore Ravens": {"abbr": "bal", "short": "Ravens"},
    "Cincinnati Bengals": {"abbr": "cin", "short": "Bengals"},
    "Cleveland Browns": {"abbr": "cle", "short": "Browns"},
    "Pittsburgh Steelers": {"abbr": "pit", "short": "Steelers"},
    "Houston Texans": {"abbr": "hou", "short": "Texans"},
    "Indianapolis Colts": {"abbr": "ind", "short": "Colts"},
    "Jacksonville Jaguars": {"abbr": "jax", "short": "Jaguars"},
    "Tennessee Titans": {"abbr": "ten", "short": "Titans"},
    "Denver Broncos": {"abbr": "den", "short": "Broncos"},
    "Kansas City Chiefs": {"abbr": "kc", "short": "Chiefs"},
    "Las Vegas Raiders": {"abbr": "lv", "short": "Raiders"},
    "Los Angeles Chargers": {"abbr": "lac", "short": "Chargers"},
    "Dallas Cowboys": {"abbr": "dal", "short": "Cowboys"},
    "New York Giants": {"abbr": "nyg", "short": "Giants"},
    "Philadelphia Eagles": {"abbr": "phi", "short": "Eagles"},
    "Washington Commanders": {"abbr": "was", "short": "Commanders"},
    "Chicago Bears": {"abbr": "chi", "short": "Bears"},
    "Detroit Lions": {"abbr": "det", "short": "Lions"},
    "Green Bay Packers": {"abbr": "gb", "short": "Packers"},
    "Minnesota Vikings": {"abbr": "min", "short": "Vikings"},
    "Atlanta Falcons": {"abbr": "atl", "short": "Falcons"},
    "Carolina Panthers": {"abbr": "car", "short": "Panthers"},
    "New Orleans Saints": {"abbr": "no", "short": "Saints"},
    "Tampa Bay Buccaneers": {"abbr": "tb", "short": "Buccaneers"},
    "Arizona Cardinals": {"abbr": "ari", "short": "Cardinals"},
    "Los Angeles Rams": {"abbr": "lar", "short": "Rams"},
    "San Francisco 49ers": {"abbr": "sf", "short": "49ers"},
    "Seattle Seahawks": {"abbr": "sea", "short": "Seahawks"},
}

DRAW_ABBR = "draw"

# Used when the server has no custom emoji with the team's abbr as its name.
DEFAULT_TEAM_EMOJIS = {
    "ne": "\U0001f514",  # 🔔
    "buf": "\U0001f9ac",  # 🦬
    "nyj": "✈️",  # ✈️
    "mia": "\U0001f42c",  # 🐬
    "bal": "\U0001f426‍⬛",  # 🐦‍⬛
    "cin": "\U0001f42f",  # 🐯
    "cle": "\U0001f415",  # 🐕
    "pit": "⚙️",  # ⚙️
    "hou": "⭐",  # ⭐
    "ind": "\U0001f434",  # 🐴
    "jax": "\U0001f406",  # 🐆
    "ten": "⚔️",  # ⚔️
    "den": "\U0001f3d4️",  # 🏔️
    "kc": "\U0001fab6",  # 🪶
    "lv": "\U0001f3f4‍☠️",  # 🏴‍☠️
    "lac": "⚡",  # ⚡
    "dal": "\U0001f920",  # 🤠
    "nyg": "\U0001f5fd",  # 🗽
    "phi": "\U0001f985",  # 🦅
    "was": "\U0001f396️",  # 🎖️
    "chi": "\U0001f43b",  # 🐻
    "det": "\U0001f981",  # 🦁
    "gb": "\U0001f9c0",  # 🧀
    "min": "\U0001f6e1️",  # 🛡️
    "atl": "\U0001f426",  # 🐦
    "car": "\U0001f408‍⬛",  # 🐈‍⬛
    "no": "\U0001f607",  # 😇
    "tb": "⚓",  # ⚓
    "ari": "\U0001f335",  # 🌵
    "lar": "\U0001f40f",  # 🐏
    "sf": "⛏️",  # ⛏️
    "sea": "\U0001f30a",  # 🌊
    DRAW_ABBR: "\U0001f91d",  # 🤝
}

team_location = {v["short"]: v["short"] for v in teams.values()}


def get_team_emoji(guild: discord.Guild | None, abbr: str) -> str:
    """The server's custom emoji named `abbr`, else the built-in default."""
    if guild is not None:
        custom = discord.utils.get(guild.emojis, name=abbr)
        if custom is not None:
            return str(custom)
    return DEFAULT_TEAM_EMOJIS.get(abbr, "")


def get_team_emoji_by_name(guild: discord.Guild | None, team_name: str) -> str:
    """The emoji for a team, by its ESPN display name ("" if unknown)."""
    info = teams.get(team_name)
    if info is None:
        return ""
    return get_team_emoji(guild, info["abbr"])


def get_draw_emoji(guild: discord.Guild | None) -> str:
    """The emoji for picking a tie."""
    return get_team_emoji(guild, DRAW_ABBR)


def get_emoji_to_team_short_map(guild: discord.Guild | None) -> dict[str, str]:
    """{emoji: team short name}, to turn a reaction back into a pick."""
    return {get_team_emoji(guild, v["abbr"]): v["short"] for v in teams.values()}
