"""Fantasy league reminders: Tuesday waivers and digest, inactive-player alerts."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
import logging
import pytz
import requests
from discord.ext import commands
import discord
from espn_api.football import League
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)
from discord.ext.commands import Bot
from data.channel_ids import REMINDER_CHANNEL_ID, ADMIN_CHANNEL_ID
from data.config import LEAGUE_TIMEZONE, FANTASY_FINAL_WEEK, LEAGUE_NAME
from data.discord_ids import load_discord_ids
from core.utils.espn_helpers import get_league
from core.utils.discord_helpers import get_text_channel, split_message
from core.utils.quiet_hours import clamp_to_quiet_hours
from data.messages import (
    WAIVER_REMINDER_MESSAGE,
    DIGEST_FAILURE_MESSAGE,
    RECAP_HEADER_TEMPLATE,
    NO_GAMES_RECAP_LABEL,
    AWARDS_HEADER_LABEL,
    NAILBITER_LABEL,
    TOP_SCORER_LABEL,
    LOWEST_SCORER_LABEL,
    BENCH_AWARD_LABEL,
    OVERACHIEVER_LABEL,
    UNDERACHIEVER_LABEL,
    SCORE_SUMMARY_TEMPLATE,
    BENCH_SUMMARY_TEMPLATE,
    OVERACHIEVER_SUMMARY_TEMPLATE,
    UNDERACHIEVER_SUMMARY_TEMPLATE,
    STANDINGS_LINE_TEMPLATE,
    WIN_STREAKS_HEADER_LABEL,
    LOSS_STREAKS_HEADER_LABEL,
    NO_STREAKS_LABEL,
    WIN_STREAK_LINE_TEMPLATE,
    LOSS_STREAK_LINE_TEMPLATE,
    FINAL_STANDINGS_BANNER_TEMPLATE,
    SEASON_END_MESSAGE,
    PREVIEW_HEADER_TEMPLATE,
    INACTIVE_ALERT_HEADER_TEMPLATE,
    INACTIVE_PLAYER_LINE_TEMPLATE,
    INACTIVE_PLAYER_LINE_WITH_TEAM_TEMPLATE,
    SOON_LABEL,
    INACTIVE_FALLBACK_HEADER,
)

logger = logging.getLogger(__name__)


# espn_api blocks while it waits for ESPN, so its network calls
# (get_league(), league.box_scores()) go through asyncio.to_thread().


# After a restart later than this on a Tuesday, assume the Tuesday messages
# already went out. What was sent is only kept in memory.
WAIVER_REMINDER_LATE_LIMIT = timedelta(hours=1)


class FantasyReminders(commands.Cog):
    """Scheduled fantasy league messages in REMINDER_CHANNEL_ID."""

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self.league_tz = pytz.timezone(LEAGUE_TIMEZONE)
        self.last_waiver_week: int | None = None
        self.inactive_notified: set[tuple[int, str | int | None, str | None]] = set()
        self.bot.loop.create_task(self.reminder_scheduler())
        self.bot.loop.create_task(self.inactive_alert_scheduler())

    async def _notify_admin(self, message: str) -> None:
        """Posts to the admin channel. A failed send is logged, never raised."""
        admin_channel = self.bot.get_channel(ADMIN_CHANNEL_ID)
        if not isinstance(admin_channel, discord.TextChannel):
            return
        try:
            await admin_channel.send(message)
        except Exception as exc:
            logger.warning("Failed to send admin warning in FantasyReminders: %s", exc)

    def _current_streak(self, team) -> tuple[str | None, int]:
        """("W" or "L", length) of a team's current streak, or (None, 0)."""
        length = getattr(team, "streak_length", 0)
        streak_type = getattr(team, "streak_type", "")
        if not length or not streak_type:
            return None, 0
        last = "W" if streak_type.upper().startswith("WIN") else "L"
        return last, length

    async def build_matchup_digest(
        self, channel: discord.TextChannel, league: League
    ) -> None:
        """Posts last week's results, awards, streaks and next week's matchups.

        The digest recapping FANTASY_FINAL_WEEK shows final standings instead
        of a preview; nothing is posted after that.
        """
        # The week about to be played. Not league.current_week: espn_api caps
        # it at the final week, so it can't tell when the season is over.
        upcoming_week = league.scoringPeriodId

        if upcoming_week <= 1:
            logger.info("Skipping matchup digest, no week played yet")
            return
        last_week = upcoming_week - 1
        next_week = upcoming_week
        if last_week > FANTASY_FINAL_WEEK:
            logger.info(
                "Skipping matchup digest, fantasy season ended week %s "
                "(now week %s)",
                FANTASY_FINAL_WEEK,
                upcoming_week,
            )
            return
        # The Tuesday after the championship, which it recaps.
        is_final_week = last_week == FANTASY_FINAL_WEEK

        msg = []

        # Recap
        msg.append(RECAP_HEADER_TEMPLATE.format(week=last_week))
        recap_boxes = await asyncio.to_thread(league.box_scores, week=last_week)

        recap_lines = []
        nailbiter: Optional[Tuple[float, str]] = None
        highest_scorer: Optional[Tuple[float, str]] = None
        lowest_scorer: Optional[Tuple[float, str]] = None
        bench_award: Optional[Tuple[float, str]] = None
        overachiever: Optional[Tuple[float, str]] = None
        underachiever: Optional[Tuple[float, str]] = None
        for box in recap_boxes:
            home, away = box.home_team, box.away_team
            hs, ascore = box.home_score, box.away_score
            if hs > ascore:
                line = (
                    f"- **{home.team_name} ({hs:.2f})** – "
                    f"{away.team_name} ({ascore:.2f})"
                )
            elif ascore > hs:
                line = (
                    f"- {home.team_name} ({hs:.2f}) – "
                    f"**{away.team_name} ({ascore:.2f})**"
                )
            else:
                line = (
                    f"- {home.team_name} ({hs:.2f}) – {away.team_name} ({ascore:.2f})"
                )
            recap_lines.append(line)
            margin = abs(hs - ascore)
            if nailbiter is None:
                nailbiter = (
                    margin,
                    f"{home.team_name} vs {away.team_name} (margin {margin:.2f})",
                )
            elif margin < nailbiter[0]:
                nailbiter = (
                    margin,
                    f"{home.team_name} vs {away.team_name} (margin {margin:.2f})",
                )
            for team, score in [(home, hs), (away, ascore)]:
                summary = SCORE_SUMMARY_TEMPLATE.format(
                    team=team.team_name, score=score
                )
                if highest_scorer is None:
                    highest_scorer = (score, summary)
                elif score > highest_scorer[0]:
                    highest_scorer = (score, summary)
                if lowest_scorer is None:
                    lowest_scorer = (score, summary)
                elif score < lowest_scorer[0]:
                    lowest_scorer = (score, summary)

            def bench_points(lineup):
                return sum(p.points for p in lineup if p.slot_position == "BE")

            home_bench = bench_points(box.home_lineup)
            away_bench = bench_points(box.away_lineup)
            for team, bench_sum in [(home, home_bench), (away, away_bench)]:
                bench_summary = BENCH_SUMMARY_TEMPLATE.format(
                    team=team.team_name, score=bench_sum
                )
                if bench_award is None:
                    bench_award = (bench_sum, bench_summary)
                elif bench_sum > bench_award[0]:
                    bench_award = (bench_sum, bench_summary)

            # Over/underachievers vs. projected points
            for team, actual, projected in [
                (home, hs, box.home_projected),
                (away, ascore, box.away_projected),
            ]:
                diff = actual - projected if projected not in (None, -1) else actual
                over_summary = OVERACHIEVER_SUMMARY_TEMPLATE.format(
                    team=team.team_name, diff=diff
                )
                under_summary = UNDERACHIEVER_SUMMARY_TEMPLATE.format(
                    team=team.team_name, diff=diff
                )
                if overachiever is None:
                    overachiever = (diff, over_summary)
                elif diff > overachiever[0]:
                    overachiever = (diff, over_summary)
                if underachiever is None:
                    underachiever = (diff, under_summary)
                elif diff < underachiever[0]:
                    underachiever = (diff, under_summary)

        if recap_lines:
            msg += recap_lines
        else:
            msg.append(NO_GAMES_RECAP_LABEL)

        # Awards
        msg.append("")
        msg.append(AWARDS_HEADER_LABEL)
        if nailbiter is not None:
            msg.append(f"- {NAILBITER_LABEL}: {nailbiter[1]}")
        if highest_scorer is not None:
            msg.append(f"- {TOP_SCORER_LABEL}: {highest_scorer[1]}")
        if lowest_scorer is not None:
            msg.append(f"- {LOWEST_SCORER_LABEL}: {lowest_scorer[1]}")
        if bench_award is not None:
            msg.append(f"- {BENCH_AWARD_LABEL}: {bench_award[1]}")
        if overachiever is not None:
            msg.append(f"- {OVERACHIEVER_LABEL}: {overachiever[1]}")
        if underachiever is not None:
            msg.append(f"- {UNDERACHIEVER_LABEL}: {underachiever[1]}")

        # Streaks of 3+
        msg.append("")
        msg.append(WIN_STREAKS_HEADER_LABEL)
        streaks = []
        for team in league.teams:
            last, run = self._current_streak(team)
            if run >= 3:
                streaks.append((team.team_name, last, run))
        hot = [s for s in streaks if s[1] == "W"]
        cold = [s for s in streaks if s[1] == "L"]
        if hot:
            for name, _, run in hot:
                msg.append(f"- {WIN_STREAK_LINE_TEMPLATE.format(name=name, run=run)}")
        else:
            msg.append(NO_STREAKS_LABEL)

        msg.append("")
        msg.append(LOSS_STREAKS_HEADER_LABEL)
        if cold:
            for name, _, run in cold:
                msg.append(f"- {LOSS_STREAK_LINE_TEMPLATE.format(name=name, run=run)}")
        else:
            msg.append(NO_STREAKS_LABEL)

        if is_final_week:
            msg.append("")
            msg.append("=" * 40)
            msg.append(
                FINAL_STANDINGS_BANNER_TEMPLATE.format(
                    league_name=LEAGUE_NAME, year=league.year
                )
            )
            msg.append("=" * 40)

            # Final playoff placement (regular-season rank until it's set),
            # so the champion gets the gold medal, not the best record.
            standings = league.standings()

            for i, team in enumerate(standings, start=1):
                medal = ""
                if i == 1:
                    medal = "🥇 "
                elif i == 2:
                    medal = "🥈 "
                elif i == 3:
                    medal = "🥉 "

                msg.append(
                    STANDINGS_LINE_TEMPLATE.format(
                        medal=medal,
                        rank=i,
                        team=team.team_name,
                        wins=team.wins,
                        losses=team.losses,
                        points=team.points_for,
                    )
                )

            msg.append("")
            msg.append(SEASON_END_MESSAGE)
        else:
            # Preview
            msg.append("")
            msg.append(PREVIEW_HEADER_TEMPLATE.format(week=next_week))
            preview_boxes = await asyncio.to_thread(league.box_scores, week=next_week)
            for box in preview_boxes:
                home, away = box.home_team, box.away_team
                msg.append(
                    f"- {away.team_name} ({away.wins}-{away.losses}) @ "
                    f"{home.team_name} ({home.wins}-{home.losses})"
                )

        if channel:
            for chunk in split_message("\n".join(msg)):
                await channel.send(chunk)

    @staticmethod
    def _too_late_for_tuesday_messages(now: datetime, reminder_time: datetime) -> bool:
        """Whether now is more than WAIVER_REMINDER_LATE_LIMIT past reminder_time."""
        return now > reminder_time + WAIVER_REMINDER_LATE_LIMIT

    async def _send_tuesday_messages(self, channel: discord.TextChannel) -> None:
        """Sends the waiver reminder while weeks are left to play, then the digest.

        The digest decides for itself whether to post, since it still recaps
        the championship after the last week.
        """
        try:
            league = await asyncio.to_thread(get_league)
        except (
            requests.exceptions.RequestException,
            ESPNAccessDenied,
            ESPNInvalidLeague,
            ESPNUnknownError,
            ValueError,
        ) as exc:
            # Admin only: without the league we can't tell if it's even the
            # season, so a public message could repeat all offseason.
            logger.error("Could not fetch ESPN league for Tuesday messages: %s", exc)
            await self._notify_admin(
                f"[fantasy_reminders] Could not fetch ESPN league, skipped "
                f"waiver reminder and digest: {exc}"
            )
            return

        if 1 <= league.scoringPeriodId <= FANTASY_FINAL_WEEK:
            await channel.send(WAIVER_REMINDER_MESSAGE)
        try:
            await self.build_matchup_digest(channel, league)
        except Exception as exc:
            # Broad on purpose: a failed digest must not stop the reminder loop.
            logger.exception("Error in matchup digest: %s", exc)
            await self._notify_admin(
                f"[fantasy_reminders] Error in matchup digest: {exc}"
            )
            await channel.send(DIGEST_FAILURE_MESSAGE)

    async def reminder_scheduler(self) -> None:
        """Sends the Tuesday 18:00 messages once a week. Runs forever.

        Errors are logged, and the loop retries after five minutes.
        """
        await self.bot.wait_until_ready()
        channel = get_text_channel(self.bot, REMINDER_CHANNEL_ID)
        while channel is None:
            logger.warning(
                "Could not find text channel with id %s, retrying in 30s",
                REMINDER_CHANNEL_ID,
            )
            await asyncio.sleep(30)
            channel = get_text_channel(self.bot, REMINDER_CHANNEL_ID)

        while True:
            now: datetime = datetime.now(self.league_tz)
            weekday: int = now.weekday()

            try:
                if weekday == 1:  # Tuesday
                    week_num: int = now.isocalendar()[1]
                    reminder_time: datetime = now.replace(
                        hour=18, minute=0, second=0, microsecond=0
                    )

                    if now < reminder_time:
                        sleep_seconds: float = (reminder_time - now).total_seconds()
                        await asyncio.sleep(sleep_seconds)
                    elif self._too_late_for_tuesday_messages(now, reminder_time):
                        self.last_waiver_week = week_num

                    if (
                        self.last_waiver_week is None
                        or self.last_waiver_week != week_num
                    ):
                        if channel:
                            await self._send_tuesday_messages(channel)
                            self.last_waiver_week = week_num
                            logger.info(
                                "Tuesday reminder sent for week %s (%s)",
                                week_num,
                                reminder_time,
                            )

                    tomorrow: datetime = reminder_time + timedelta(days=1)
                    sleep_seconds = (
                        tomorrow - datetime.now(self.league_tz)
                    ).total_seconds()
                    await asyncio.sleep(sleep_seconds)
                    continue

                next_tuesday: datetime = now + timedelta(days=(1 - weekday) % 7)
                next_tuesday = next_tuesday.replace(
                    hour=18, minute=0, second=0, microsecond=0
                )
                if next_tuesday <= now:
                    next_tuesday += timedelta(days=7)

                sleep_seconds = (next_tuesday - now).total_seconds()
                await asyncio.sleep(sleep_seconds)

            except Exception as e:
                # Broad on purpose: the scheduler loop must never die.
                logger.exception("Error in FantasyReminders: %s. Retrying in 5 min.", e)
                await self._notify_admin(
                    f"[fantasy_reminders] Error in FantasyReminders: {e}. Retrying in 5 min."
                )
                await asyncio.sleep(300)

    def _inactive_alert_send_time(self, kickoff: datetime) -> datetime:
        """One hour before kickoff, moved out of quiet hours if needed."""
        natural_time = kickoff - timedelta(hours=1)
        return clamp_to_quiet_hours(natural_time)

    @staticmethod
    def _ensure_aware_datetime(
        value: datetime, default_tz: timezone | pytz.tzinfo.BaseTzInfo = timezone.utc
    ) -> datetime:
        """Makes a naive datetime aware, assuming it's in default_tz."""
        if value.tzinfo is not None:
            return value.astimezone(default_tz)
        return value.replace(tzinfo=default_tz)

    def _player_kickoff(self, player) -> datetime | None:
        """A player's next kickoff, from a direct field if espn_api has one,
        else their schedule. None if unknown."""
        for attr in ("game_date", "gameDate", "game_start_time", "game_start"):
            kickoff = getattr(player, attr, None)
            if isinstance(kickoff, datetime):
                return self._ensure_aware_datetime(kickoff, timezone.utc)
            if isinstance(kickoff, (int, float)):
                return datetime.fromtimestamp(kickoff / 1000, tz=timezone.utc)

        schedule = getattr(player, "schedule", None) or {}
        upcoming: list[datetime] = []
        for _, entry in schedule.items():
            if isinstance(entry, list):
                # Some espn_api schedules have a list of games per week.
                games = entry
            else:
                games = [entry]
            for game in games:
                date_val = game.get("date")
                if isinstance(date_val, datetime):
                    upcoming.append(self._ensure_aware_datetime(date_val, timezone.utc))
                elif isinstance(date_val, (int, float)):
                    upcoming.append(
                        datetime.fromtimestamp(date_val / 1000, tz=timezone.utc)
                    )
        now_utc = datetime.now(timezone.utc)
        future_games = [d for d in upcoming if d >= now_utc]
        if not future_games and upcoming:
            # All in the past: the latest one is better than nothing.
            return sorted(upcoming)[-1]
        if future_games:
            return sorted(future_games)[0]
        return None

    async def inactive_alert_scheduler(self) -> None:
        """Every 10 minutes, warns managers about Out/Doubtful/etc. starters.

        The warning goes to the manager from discord_ids.json (or @everyone
        if unmapped) an hour before kickoff, once per player per game.
        """
        await self.bot.wait_until_ready()
        # Without a mapping, every team gets the general @everyone warning.
        id_map: dict[int, int] = {}
        try:
            id_map = load_discord_ids()
        except FileNotFoundError:
            logger.info("No discord_ids.json, injury warnings go to @everyone.")
        except ValueError as exc:
            logger.error("Could not read discord_ids.json: %s", exc)
            await self._notify_admin(
                f"[inactive-alert] Could not read discord_ids.json ({exc}). "
                "Injury warnings go to @everyone until it's fixed and the bot restarts."
            )

        channel = get_text_channel(self.bot, REMINDER_CHANNEL_ID)
        while channel is None:
            logger.warning(
                "Could not find text channel with id %s, retrying in 30s",
                REMINDER_CHANNEL_ID,
            )
            await asyncio.sleep(30)
            channel = get_text_channel(self.bot, REMINDER_CHANNEL_ID)
        admin_channel = get_text_channel(self.bot, ADMIN_CHANNEL_ID)
        # Tell the admin channel when checks start failing and when they
        # recover, not on every retry.
        failing = False

        while True:
            now = datetime.now(self.league_tz)
            try:
                league = await asyncio.to_thread(get_league)
                missing_id_flags: list[str] = []
                for team in league.teams:
                    discord_id = id_map.get(team.team_id)
                    team_display = getattr(team, "team_name", f"Team {team.team_id}")

                    flagged: list[tuple[str, str, datetime | None]] = []
                    for player in team.roster:
                        slot = getattr(player, "lineupSlot", "")
                        if slot in {"BE", "IR"}:
                            continue

                        status = getattr(player, "injuryStatus", "") or ""
                        if status.upper() not in {
                            "OUT",
                            "DOUBTFUL",
                            "INACTIVE",
                            "SUSPENSION",
                        }:
                            continue

                        kickoff = self._player_kickoff(player)
                        if kickoff:
                            kickoff = self._ensure_aware_datetime(kickoff, timezone.utc)
                            kickoff = kickoff.astimezone(self.league_tz)
                            send_time = self._inactive_alert_send_time(kickoff)
                            if now < send_time or now >= kickoff:
                                continue
                            key_time = kickoff.isoformat()
                        else:
                            key_time = None

                        unique_key = (
                            team.team_id,
                            getattr(player, "playerId", getattr(player, "name", None)),
                            key_time,
                        )
                        if unique_key in self.inactive_notified:
                            continue

                        flagged.append((player.name, status, kickoff))
                        self.inactive_notified.add(unique_key)

                    if flagged and discord_id is not None:
                        lines = [INACTIVE_ALERT_HEADER_TEMPLATE.format(user=discord_id)]
                        for name, status, kickoff in flagged:
                            when_txt = (
                                kickoff.strftime("%H:%M") if kickoff else SOON_LABEL
                            )
                            lines.append(
                                INACTIVE_PLAYER_LINE_TEMPLATE.format(
                                    name=name, status=status, time=when_txt
                                )
                            )
                        await channel.send("\n".join(lines))
                        if isinstance(admin_channel, discord.TextChannel):
                            msg = (
                                f"[inactive-alert] Notified <@{discord_id}> about "
                                f"{len(flagged)} player(s)."
                            )
                            await admin_channel.send(msg)
                    elif flagged and discord_id is None:
                        for name, status, kickoff in flagged:
                            when_txt = (
                                kickoff.strftime("%H:%M") if kickoff else SOON_LABEL
                            )
                            missing_id_flags.append(
                                INACTIVE_PLAYER_LINE_WITH_TEAM_TEMPLATE.format(
                                    team=team_display,
                                    name=name,
                                    status=status,
                                    time=when_txt,
                                )
                            )

                if missing_id_flags:
                    lines = [INACTIVE_FALLBACK_HEADER]
                    lines.extend(missing_id_flags)
                    await channel.send("\n".join(lines))
                    if isinstance(admin_channel, discord.TextChannel):
                        await admin_channel.send(
                            f"[inactive-alert] Sent @everyone fallback for "
                            f"{len(missing_id_flags)} player(s)."
                        )
                if failing:
                    failing = False
                    await self._notify_admin(
                        "[fantasy_reminders] Inactive-player checks are working again."
                    )
            except Exception as exc:
                # Broad on purpose: one failed check must not stop the loop.
                logger.exception(
                    "Error checking for inactive players: %s. Retrying in 10 min.",
                    exc,
                )
                if not failing:
                    failing = True
                    await self._notify_admin(
                        "[fantasy_reminders] Error checking for inactive players: "
                        f"{exc}. Retrying every 10 min, you'll get one message "
                        "here when it works again."
                    )
            await asyncio.sleep(600)


async def setup(bot: Bot) -> None:
    await bot.add_cog(FantasyReminders(bot))
