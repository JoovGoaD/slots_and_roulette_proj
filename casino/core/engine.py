from dataclasses import dataclass

import numpy as np


SYMBOLS = {1: "A", 2: "K", 3: "Q", 4: "J", 5: "10", 6: "M", 7: "L"}
PROBABILITIES = {1: 0.15, 2: 0.20, 3: 0.20, 4: 0.20, 5: 0.20, 6: 0.01, 7: 0.04}
PAYOUTS = {1: 1, 2: 0.6, 3: 0.4, 4: 0.3, 5: 0.2}


@dataclass(frozen=True)
class SpinFrame:
    board: np.ndarray
    multiplier: int
    message: str
    clusters: tuple = ()


@dataclass(frozen=True)
class SpecialEffect:
    board: np.ndarray
    multiplier: int
    position: tuple[int, int]
    target: tuple[int, int] | None = None


class Casino:
    class Player:
        def __init__(self, balance, bet=1.0):
            if not np.isfinite(balance) or balance < 0:
                raise ValueError("Balance must be finite and non-negative")

            self.balance = float(balance)
            self.set_bet(bet)

        def set_bet(self, bet):
            if not np.isfinite(bet) or bet <= 0:
                raise ValueError("Bet must be finite and positive")
            self.bet = float(bet)

    class Game:
        def __init__(self, seed=None):
            self.map = np.zeros((5, 6), dtype=int)
            self.multiplier = 1
            self.rng = np.random.default_rng(seed)
            self.symbols = list(PROBABILITIES)
            self.probs = list(PROBABILITIES.values())
            self.payouts = PAYOUTS

            if not np.isclose(sum(self.probs), 1.0):
                raise ValueError("Symbol probabilities must sum to 1")

        def generate(self, size):
            return self.rng.choice(self.symbols, size=size, p=self.probs)

        def level_up(self, record_frame=None):
            positions = np.argwhere(self.map == 7)
            offsets = [
                (-1, -1), (-1, 0), (-1, 1),
                (0, -1),            (0, 1),
                (1, -1),  (1, 0),   (1, 1)
            ]
            rows, cols = self.map.shape

            for row, col in positions:
                for dr, dc in offsets:
                    nr, nc = row + dr, col + dc
                    if 0 <= nr < rows and 0 <= nc < cols:
                        if 2 <= self.map[nr, nc] <= 5:
                            self.map[nr, nc] -= 1
                            if record_frame is not None:
                                record_frame(SpecialEffect(
                                    self.map.copy(), self.multiplier, (row, col), (nr, nc)
                                ))

            self.map[self.map == 7] = 0

        def collect_multiplier(self, record_frame=None):
            mask = self.map == 6
            if record_frame is None:
                self.multiplier += int(np.count_nonzero(mask))
                self.map[mask] = 0
                return

            positions = np.argwhere(mask)
            for row, col in positions:
                self.multiplier += 1
                self.map[row, col] = 0
                record_frame(SpecialEffect(self.map.copy(), self.multiplier, (row, col)))

        def find_clusters(self, min_size=5):
            rows, cols = self.map.shape
            visited = set()
            clusters = []
            offsets = [(1, 0), (-1, 0), (0, 1), (0, -1)]

            for row in range(rows):
                for col in range(cols):
                    if (row, col) in visited:
                        continue
                    symbol = self.map[row, col]
                    if not 1 <= symbol <= 5:
                        continue

                    stack = [(row, col)]
                    visited.add((row, col))
                    cluster = []

                    while stack:
                        r, c = stack.pop()
                        cluster.append((r, c))
                        for dr, dc in offsets:
                            nr, nc = r + dr, c + dc
                            if not (0 <= nr < rows and 0 <= nc < cols):
                                continue
                            if (nr, nc) in visited:
                                continue
                            if self.map[nr, nc] == symbol:
                                visited.add((nr, nc))
                                stack.append((nr, nc))

                    if len(cluster) >= min_size:
                        clusters.append((symbol, cluster))

            return clusters

        def calculate_win(self, clusters):
            return sum(
                self.payouts[symbol] * (len(positions) - 4) * 0.7
                for symbol, positions in clusters
            )

        def cascade(self, clusters):
            for _, positions in clusters:
                for row, col in positions:
                    self.map[row, col] = 0
            self.refill()

        def refill(self):
            rows, cols = self.map.shape
            for col in range(cols):
                remaining = self.map[:, col]
                remaining = remaining[remaining != 0]
                missing = rows - len(remaining)
                new_symbols = self.generate(missing)
                self.map[:, col] = np.concatenate([new_symbols, remaining])

        def roll(self, player, max_steps=1000, verbose=False, *, record_frame=None):
            if player.balance < player.bet:
                raise ValueError("Insufficient balance for the bet")
            if not isinstance(max_steps, int) or max_steps <= 0:
                raise ValueError("max_steps must be a positive integer")

            self.map = self.generate(self.map.shape)
            self.multiplier = 1
            total_win = 0.0  # Сумма коэффициентов выплаты (до ставки и множителя)
            cascades = 0

            if record_frame is not None:
                record_frame(SpinFrame(self.map.copy(), self.multiplier, "Symbols landed"))

            if verbose:
                print("INITIAL BOARD")
                self.show_map()

            for _ in range(max_steps):
                has_special = bool(np.any((self.map == 6) | (self.map == 7)))
                if has_special:
                    self.level_up(record_frame)
                    self.collect_multiplier(record_frame)

                clusters = self.find_clusters()

                if clusters:
                    win = self.calculate_win(clusters)
                    total_win += win
                    cascades += 1

                    if verbose:
                        found = [(SYMBOLS[s], len(pos)) for s, pos in clusters]
                        print(f"\nCASCADE {cascades}: {found}")
                        print(f"Base win: {win:.2f} | Multiplier: x{self.multiplier}")

                    if record_frame is not None:
                        winning_clusters = tuple(tuple(positions) for _, positions in clusters)
                        record_frame(SpinFrame(
                            self.map.copy(),
                            self.multiplier,
                            f"Cascade {cascades}  |  {len(clusters)} winning cluster(s)",
                            winning_clusters,
                        ))
                    self.cascade(clusters)
                elif has_special:
                    # Спецсимволы удалены: новые символы тоже надо проверить.
                    self.refill()
                else:
                    break  # Поле стабильно: спецсимволов и кластеров нет.

                if record_frame is not None:
                    message = "Symbols dropped" if clusters else "Special symbols collected"
                    record_frame(SpinFrame(self.map.copy(), self.multiplier, message))

                if verbose:
                    self.show_map()
            else:
                # Ошибка не изменяет баланс игрока.
                raise RuntimeError("Spin exceeded max_steps")

            payout = round(player.bet * total_win * self.multiplier, 2)
            player.balance = round(player.balance - player.bet + payout, 2)

            if verbose:
                print(f"\nCascades: {cascades}")
                print(f"Base win: {total_win:.2f} | Multiplier: x{self.multiplier}")
                print(f"Bet: {player.bet:.2f} | Payout: {payout:.2f}")
                print(f"Net result: {payout - player.bet:+.2f}")
                print(f"Player balance: {player.balance:.2f}")

            return payout

        def show_map(self):
            for row in self.map:
                print(" ".join(f"{SYMBOLS.get(int(x), '.'):>2}" for x in row))
            print()


# def simulate_rtp(n_spins=100000, bet=100, seed=42):
#     game = Casino.Game()
#     # Большой стартовый баланс, чтобы симуляция не прерывалась из-за банкротства.
#     player = Casino.Player(balance=max(1_000_000, 10 * n_spins * bet), bet=bet)
#     start_balance = player.balance
#     payouts = np.empty(n_spins)
#
#     for i in range(n_spins):
#         payouts[i] = game.roll(player)
#
#     total_bets = n_spins * bet
#     total_payouts = payouts.sum()
#     rtp = total_payouts / total_bets * 100
#     print(f"Spins: {n_spins:,}")
#     print(f"RTP: {rtp:.2f}%")
#     print(f"House edge: {100 - rtp:.2f}%")
#     print(f"Hit frequency: {np.mean(payouts > 0):.2%}")
#     print(f"Average payout: {payouts.mean():.4f}")
#     print(f"Maximum payout: {payouts.max():.2f}")
#     print(f"Payout std: {payouts.std():.4f}")
#     print(f"Casino profit: {total_bets - total_payouts:.2f}")
#     print(f"Player balance change: {player.balance - start_balance:+.2f}")
#     return rtp
#
#
# if __name__ == "__main__":
#     simulate_rtp()
#     # Для одного спина с печатью поля:
#     # player = Casino.Player(balance=100, bet=5)
#     # Casino.Game(seed=42).roll(player, verbose=True)

