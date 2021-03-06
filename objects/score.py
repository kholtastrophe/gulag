
from typing import Optional
from enum import IntEnum, unique
import time
import base64
from py3rijndael import RijndaelCbc, ZeroPadding

from pp.owoppai import Owoppai
from constants.mods import Mods
from constants.clientflags import ClientFlags
from constants.gamemodes import GameMode
from console import plog, Ansi

from objects.beatmap import Beatmap
from objects.player import Player
from objects import glob

__all__ = (
    'Rank',
    'SubmissionStatus',
    'Score'
)

@unique
class Rank(IntEnum):
    XH = 0
    SH = 1
    X  = 2
    S  = 3
    A  = 4
    B  = 5
    C  = 6
    D  = 7
    F  = 8
    N  = 9

    def __str__(self) -> str:
        return {
            self.XH: 'SS',
            self.SH: 'SS',
            self.X: 'S',
            self.S: 'S',
            self.A: 'A',
            self.B: 'B',
            self.C: 'C',
            self.D: 'D',
            self.F: 'F'
        }[self.value]

@unique
class SubmissionStatus(IntEnum):
    # TODO: make a system more like bancho's?
    FAILED = 0
    SUBMITTED = 1
    BEST = 2

    def __repr__(self) -> str:
        return {
            self.FAILED: 'Failed',
            self.SUBMITTED: 'Submitted',
            self.BEST: 'Best'
        }[self.value]

class Score:
    """A class to represent an osu! score.

    Attributes
    -----------
    id: `int`
        The score's unique ID.

    bmap: Optional[`Beatmap`]
        A beatmap obj representing the osu map.

    player: Optional[`Player`]
        A player obj of the player who submitted the score.

    pp: `float`
        The score's performance points.

    score: `int`
        The score's osu! score value.

    max_combo: `int`
        The maximum combo reached in the score.

    mods: `Mods`
        A bitwise value of the osu! mods used in the score.

    acc: `float`
        The accuracy of the score.

    n300: `int`
        The number of 300s in the score.

    n100: `int`
        The number of 100s in the score (150s if taiko).

    n50: `int`
        The number of 50s in the score.

    nmiss: `int`
        The number of misses in the score.

    ngeki: `int`
        The number of gekis in the score.

    nkatu: `int`
        The number of katus in the score.

    grade: `str`
        The letter grade in the score.

    rank: `int`
        The leaderboard placement of the score.

    passed: `bool`
        Whether the score completed the map.

    perfect: `bool`
        Whether the score is a full-combo.

    status: `SubmissionStatus`
        The submission status of the score.

    mode: `GameMode`
        The game mode of the score.

    play_time: `int`
        A UNIX timestamp of the time of score submission.

    time_elapsed: `int`
        The total elapsed time of the play (in milliseconds).

    client_flags: `int`
        osu!'s old anticheat flags.

    prev_best: Optional[`Score`]
        The previous best score before this play was submitted.
        NOTE: just because a score has a `prev_best` attribute does
        mean the score is our best score on the map! the `status`
        value will always be accurate for any score.
    """
    __slots__ = (
        'id', 'bmap', 'player',
        'pp', 'score', 'max_combo', 'mods',
        'acc', 'n300', 'n100', 'n50', 'nmiss', 'ngeki', 'nkatu', 'grade',
        'rank', 'passed', 'perfect', 'status',
        'mode', 'play_time', 'time_elapsed',
        'client_flags', 'prev_best'
    )

    def __init__(self):
        self.id = 0

        self.bmap: Optional[Beatmap] = None
        self.player: Optional[Player] = None

        self.pp = 0.0
        self.score = 0
        self.max_combo = 0
        self.mods = Mods.NOMOD

        self.acc = 0.0
        # TODO: perhaps abstract these differently
        # since they're mode dependant? feels weird..
        self.n300 = 0
        self.n100 = 0 # n150 for taiko
        self.n50 = 0
        self.nmiss = 0
        self.ngeki = 0
        self.nkatu = 0
        self.grade = Rank.F

        self.rank = 0
        self.passed = False
        self.perfect = False
        self.status = SubmissionStatus.FAILED

        self.mode = GameMode.vn_std
        self.play_time = 0
        self.time_elapsed = 0

        # osu!'s client 'anticheat'.
        self.client_flags = ClientFlags.Clean

        self.prev_best = None

    @classmethod
    async def from_sql(cls, scoreid: int, sql_table: str):
        """Create a score object from sql using it's scoreid."""
        # XXX: perhaps in the future this should take a gamemode rather
        # than just the sql table? just faster on the current setup :P
        res = await glob.db.fetch(
            'SELECT id, map_md5, userid, pp, score, '
            'max_combo, mods, acc, n300, n100, n50, '
            'nmiss, ngeki, nkatu, grade, perfect, '
            'status, mode, play_time, '
            'time_elapsed, client_flags '
            f'FROM {sql_table} WHERE id = %s',
            [scoreid], _dict = False
        )

        if not res:
            return

        s = cls()

        s.id = res[0]
        s.bmap = await Beatmap.from_md5(res[1])
        s.player = await glob.players.get_by_id(res[2], sql=True)

        (s.pp, s.score, s.max_combo, s.mods, s.acc, s.n300,
         s.n100, s.n50, s.nmiss, s.ngeki, s.nkatu, s.grade,
         s.perfect, s.status, mode_vn, s.play_time,
         s.time_elapsed, s.client_flags) = res[3:]

        # fix some types
        s.passed = s.status != 0
        s.status = SubmissionStatus(s.status)
        s.mods = Mods(s.mods)
        s.mode = GameMode.from_params(mode_vn, s.mods)
        s.client_flags = ClientFlags(s.client_flags)

        if s.bmap:
            s.rank = await s.calc_lb_placement()

        return s

    @classmethod
    async def from_submission(cls, data_enc: str, iv: str,
                              osu_ver: str, phash: str) -> None:
        """Create a score object from an osu! submission string."""
        cbc = RijndaelCbc(
            f'osu!-scoreburgr---------{osu_ver}',
            iv = base64.b64decode(iv).decode('latin_1'),
            padding = ZeroPadding(32), block_size =  32
        )

        data = cbc.decrypt(
            base64.b64decode(data_enc).decode('latin_1')
        ).decode().split(':')

        if len(data) != 18:
            plog('Received an invalid score submission.', Ansi.LRED)
            return

        s = cls()

        if len(map_md5 := data[0]) != 32:
            return

        pname = data[1].rstrip() # why does osu! make me rstrip lol

        # Get the map & player for the score.
        s.bmap = await Beatmap.from_md5(map_md5)
        s.player = await glob.players.get_login(pname, phash)

        if not s.player:
            # Return the obj with an empty player to
            # determine whether the score faield to
            # be parsed vs. the user could not be found
            # logged in (we want to not send a reply to
            # the osu! client if they're simply not logged
            # in, so that it will retry once they login).
            return s

        # XXX: unused idx 2: online score checksum
        # Perhaps will use to improve security at some point?

        # Ensure all ints are safe to cast.
        if not all(i.isdecimal() for i in data[3:11] + [data[13], data[15]]):
            plog('Invalid parameter passed into submit-modular.', Ansi.LRED)
            return

        (s.n300, s.n100, s.n50, s.ngeki, s.nkatu, s.nmiss,
         s.score, s.max_combo) = (int(i) for i in data[3:11])

        s.perfect = data[11] == '1'
        _grade = data[12] # letter grade
        s.mods = Mods(int(data[13]))
        s.passed = data[14] == 'True'
        s.mode = GameMode.from_params(int(data[15]), s.mods)
        s.play_time = int(time.time()) # (yyMMddHHmmss)
        s.client_flags = data[17].count(' ') # TODO: use osu!ver? (osuver\s+)

        s.grade = _grade if s.passed else 'F'

        # All data read from submission.
        # Now we can calculate things based on our data.
        s.calc_accuracy()

        if s.bmap:
            # Ignore SR for now.
            s.pp = (await s.calc_diff())[0]

            await s.calc_status()
            s.rank = await s.calc_lb_placement()
        else:
            s.pp = 0.0
            s.status = SubmissionStatus.SUBMITTED if s.passed \
                  else SubmissionStatus.FAILED

        return s

    async def calc_lb_placement(self) -> int:
        table = self.mode.sql_table

        if self.mode <= GameMode.rx_std:
            scoring = 'pp'
            score = self.pp
        else:
            scoring = 'score'
            score = self.score

        res = await glob.db.fetch(
            'SELECT COUNT(*) AS c FROM {t} '
            'WHERE map_md5 = %s AND mode = %s '
            'AND status = 2 AND {s} > %s'.format(t=table, s=scoring),
            [self.bmap.md5, self.mode.as_vanilla, score]
        )

        return res['c'] + 1 if res else 1

    # Could be staticmethod?
    # We'll see after some usage of gulag
    # whether it's beneficial or not.
    async def calc_diff(self) -> tuple[float, float]:
        """Calculate PP and star rating for our score."""
        mode_vn = self.mode.as_vanilla

        if mode_vn not in (0, 1):
            # Currently only std and taiko are supported,
            # since we are simply using oppai-ng alone.
            return (0.0, 0.0)

        pp_params = {
            'mods': self.mods,
            'combo': self.max_combo,
            'nmiss': self.nmiss,
            'mode': mode_vn,
            'acc': self.acc
        }

        async with Owoppai(self.bmap.id, **pp_params) as owo:
            ret = (owo.pp, owo.stars)

        return ret

    async def calc_status(self) -> None:
        """Calculate the submission status of a score."""
        if not self.passed:
            self.status = SubmissionStatus.FAILED
            return

        table = self.mode.sql_table

        # find any other `status = 2` scores we have
        # on the map. If there are any, store
        res = await glob.db.fetch(
            f'SELECT id, pp FROM {table} '
            'WHERE userid = %s AND map_md5 = %s '
            'AND mode = %s AND status = 2',
            [self.player.id, self.bmap.md5, self.mode.as_vanilla]
        )

        if res:
            # we have a score on the map.
            # save it as our previous best score.
            self.prev_best = await Score.from_sql(res['id'], table)

            # if our new score is better, update
            # both of our score's submission statuses.
            # NOTE: this will be updated in sql later on in submission
            if self.pp > res['pp']:
                self.status = SubmissionStatus.BEST
                self.prev_best.status = SubmissionStatus.SUBMITTED
            else:
                self.status = SubmissionStatus.SUBMITTED
        else:
            # this is our first score on the map.
            self.status = SubmissionStatus.BEST

    def calc_accuracy(self) -> None:
        """Calculate the accuracy of our score."""
        mode_vn = self.mode.as_vanilla

        if mode_vn == 0: # osu!
            if not (total := sum((self.n300, self.n100,
                                  self.n50, self.nmiss))):
                self.acc = 0.0
                return

            self.acc = 100.0 * sum((
                self.n50 * 50.0,
                self.n100 * 100.0,
                self.n300 * 300.0
            )) / (total * 300.0)

        elif mode_vn == 1: # osu!taiko
            if not (total := sum((self.n300, self.n100,
                                  self.nmiss))):
                self.acc = 0.0
                return

            self.acc = 100.0 * sum((
                self.n100 * 150.0,
                self.n300 * 300.0
            )) / (total * 300.0)

        elif mode_vn == 2:
            # osu!catch
            NotImplemented

        elif mode_vn == 3:
            # osu!mania
            NotImplemented
