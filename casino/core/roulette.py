"""Rules and settlement for a European single-zero roulette wheel."""

from dataclasses import dataclass
from secrets import SystemRandom


RED = frozenset((1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36))
BLACK = frozenset(range(1, 37)) - RED


def pocket_color(number):
    if number == 0:
        return "Green"
    return "Red" if number in RED else "Black"


@dataclass(frozen=True)
class RouletteBet:
    label: str
    pockets: frozenset[int]
    odds: int


@dataclass(frozen=True)
class RouletteResult:
    number: int
    payout_cents: int

    @property
    def color(self):
        return pocket_color(self.number)


def _number_bets(groups, odds):
    return tuple(
        RouletteBet("–".join(map(str, numbers)), frozenset(numbers), odds)
        for numbers in groups
    )


def _table_bets():
    streets = [tuple(range(start, start + 3)) for start in range(1, 37, 3)]
    horizontal_splits = [(a, b) for street in streets for a, b in zip(street, street[1:])]
    vertical_splits = [(number, number + 3) for number in range(1, 34)]
    corners = [(a, b, a + 3, b + 3) for a, b in horizontal_splits if a <= 33]
    six_lines = [first + second for first, second in zip(streets, streets[1:])]

    return {
        "Straight": _number_bets(((number,) for number in range(37)), 35),
        "Split": _number_bets([(0, 1), (0, 2), (0, 3)] + sorted(
            horizontal_splits + vertical_splits
        ), 17),
        "Street": _number_bets(streets, 11),
        "Zero trio": _number_bets(((0, 1, 2), (0, 2, 3)), 11),
        "Corner": _number_bets(corners, 8),
        "First four": _number_bets(((0, 1, 2, 3),), 8),
        "Six line": _number_bets(six_lines, 5),
        "Dozen": tuple(
            RouletteBet(f"{start}–{start + 11}", frozenset(range(start, start + 12)), 2)
            for start in (1, 13, 25)
        ),
        "Column": tuple(
            RouletteBet(f"Column {start}", frozenset(range(start, 37, 3)), 2)
            for start in (1, 2, 3)
        ),
        "Even money": (
            RouletteBet("Red", RED, 1),
            RouletteBet("Black", BLACK, 1),
            RouletteBet("Odd", frozenset(range(1, 37, 2)), 1),
            RouletteBet("Even", frozenset(range(2, 37, 2)), 1),
            RouletteBet("1–18", frozenset(range(1, 19)), 1),
            RouletteBet("19–36", frozenset(range(19, 37)), 1),
        ),
    }


class Roulette:
    def __init__(self, rng=None):
        self._rng = SystemRandom() if rng is None else rng
        self._bets = _table_bets()
        self._valid_bets = frozenset(bet for bets in self._bets.values() for bet in bets)

    @property
    def bet_types(self):
        return tuple(self._bets)

    def bets_for(self, bet_type):
        return self._bets[bet_type]

    def draw(self):
        """Draw a pocket independently of any wager or account."""
        return self._rng.randrange(37)

    def result_for(self, number, bet, stake_cents):
        """Calculate a wager's gross payout for an already drawn pocket."""
        if type(number) is not int or not 0 <= number <= 36:
            raise ValueError("Invalid roulette pocket.")
        if not isinstance(bet, RouletteBet) or bet not in self._valid_bets:
            raise ValueError("Choose a valid roulette bet.")
        if type(stake_cents) is not int or stake_cents <= 0:
            raise ValueError("Enter a positive bet in whole cents.")

        payout = stake_cents * (bet.odds + 1) if number in bet.pockets else 0
        return RouletteResult(number, payout)

    def spin(self, bet, stake_cents):
        """Run a standalone roulette spin for local play."""
        return self.result_for(self.draw(), bet, stake_cents)
