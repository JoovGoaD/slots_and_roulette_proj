import math
import sqlite3
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from tkinter import messagebox

import customtkinter as ctk

from casino.desktop import ui_theme as theme
from casino.core.accounts import ROULETTE_HISTORY_LIMIT, parse_bet_cents
from casino.desktop.api_client import ApiError, RemoteAccounts
from casino.core.roulette import pocket_color


POCKET_COLORS = {"Green": "#246747", "Red": "#A52D36", "Black": "#252832"}
POCKET_HOVER = {"Green": "#347F5A", "Red": "#BA4149", "Black": "#3A3F4D"}
WHEEL_ORDER = (
    0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10,
    5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26,
)
SPIN_SECONDS = 18
BALL_BORDER = "#4CCEF2"


def build_roulette(screen, totals, accounts, username, game, stake_value, on_back):
    online = isinstance(accounts, RemoteAccounts)
    header = ctk.CTkFrame(screen, fg_color="transparent")
    header.pack(fill="x", padx=36, pady=(22, 14))
    ctk.CTkLabel(header, text="European Roulette", font=theme.TITLE,
                 text_color=theme.INK).pack(side="left")
    back = theme.button(header, "Back to games", on_back, width=125)
    back.pack(side="right")

    table = theme.panel(screen)
    table.pack(fill="x", padx=36)
    summary = ctk.CTkFrame(table, fg_color="transparent")
    summary.pack(fill="x", padx=20, pady=(12, 8))
    ctk.CTkLabel(summary, text="PICK ONE BET ON THE TABLE", font=theme.CAPTION,
                 text_color=theme.MUTED).pack(side="left")
    balance = ctk.CTkLabel(summary, text=f"Play balance  {theme.money(totals.balance_cents)}",
                           font=theme.BODY, text_color=theme.GOLD)
    balance.pack(side="right")

    board = ctk.CTkFrame(table, fg_color="transparent")
    board.pack(fill="x", padx=18, pady=(0, 8))
    board.grid_columnconfigure(tuple(range(13)), weight=1, uniform="pockets")
    buttons = {}
    pockets = {}

    def bet_button(bet, row, column, *, rowspan=1, columnspan=1, width=40, color=None):
        colors = {} if color is None else {
            "fg_color": POCKET_COLORS[color], "hover_color": POCKET_HOVER[color],
            "text_color": "white", "text_color_disabled": "#F2EDF0",
        }
        button = theme.button(
            board, bet.label, lambda: select_bet(bet), width=width, height=38,
            border_width=2, **colors,
        )
        button.grid(row=row, column=column, rowspan=rowspan, columnspan=columnspan,
                    sticky="nsew", padx=2, pady=2)
        buttons[bet] = button
        return button

    for number, bet in enumerate(game.bets_for("Straight")):
        row, column = (0, 0) if number == 0 else (2 - (number - 1) % 3, (number - 1) // 3 + 1)
        pockets[number] = bet_button(bet, row, column, rowspan=3 if number == 0 else 1,
                                     color=pocket_color(number))

    for row, bet in enumerate(reversed(game.bets_for("Column"))):
        bet_button(bet, row, 13, width=90)
    for index, bet in enumerate(game.bets_for("Dozen")):
        bet_button(bet, 3, index * 4 + 1, columnspan=4)

    outside = {bet.label: bet for bet in game.bets_for("Even money")}
    for index, label in enumerate(("1–18", "Even", "Red", "Black", "Odd", "19–36")):
        bet_button(outside[label], 4, index * 2 + 1, columnspan=2,
                   color=label if label in ("Red", "Black") else None)

    controls = ctk.CTkFrame(table, fg_color="transparent")
    controls.pack(fill="x", padx=20, pady=(0, 12))
    controls.grid_columnconfigure(2, weight=1)
    ctk.CTkLabel(controls, text="More bets", font=theme.SMALL, text_color=theme.MUTED).grid(
        row=0, column=0, sticky="w"
    )
    ctk.CTkLabel(controls, text="Stake ($)", font=theme.SMALL, text_color=theme.MUTED).grid(
        row=0, column=3, sticky="w"
    )
    quick_bets = {"Straight", "Dozen", "Column", "Even money"}
    kind = ctk.CTkComboBox(controls, values=[name for name in game.bet_types if name not in quick_bets],
                           state="readonly", command=lambda name: update_bets(name), height=36)
    kind.set("Choose a type")
    kind.grid(row=1, column=0, sticky="ew", padx=(0, 8))
    choice = ctk.CTkComboBox(controls, values=[], state="disabled", height=36,
                             command=lambda label: select_bet(advanced_bets[label]))
    choice.set("Choose numbers")
    choice.grid(row=1, column=1, sticky="ew", padx=(0, 8))
    stake = theme.entry(controls, textvariable=stake_value, width=105)
    stake.grid(row=1, column=3, padx=(0, 8))
    spin_button = theme.button(controls, "Spin", lambda: spin(), primary=True, width=112,
                               height=40, state="disabled")
    spin_button.grid(row=1, column=4)
    selection = ctk.CTkLabel(controls, text="Select a number or group to see its payout.",
                             font=theme.BODY, text_color=theme.GOLD)
    selection.grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))

    outcome = theme.panel(screen)
    outcome.pack(fill="x", padx=36, pady=(12, 0))
    badge = ctk.CTkLabel(outcome, text="—", width=72, height=60, corner_radius=10,
                         font=theme.NUMBERS, fg_color=theme.FIELD, text_color=theme.INK)
    badge.pack(side="left", padx=(16, 12), pady=12)
    status = ctk.CTkFrame(outcome, fg_color="transparent")
    status.pack(side="left", fill="x", expand=True, pady=10)
    result = ctk.CTkLabel(status, text="Ready to spin", font=theme.SUBTITLE, text_color=theme.INK)
    result.pack(anchor="w")
    detail = ctk.CTkLabel(status, text="Connecting to the shared wheel…" if online
                          else f"Each spin takes {SPIN_SECONDS} seconds.",
                          font=theme.BODY, text_color=theme.MUTED)
    detail.pack(anchor="w")

    history = theme.panel(screen)
    history.pack(fill="x", padx=36, pady=(12, 0))
    history_title = f"LAST {ROULETTE_HISTORY_LIMIT} ROUNDS" if online else f"LAST {ROULETTE_HISTORY_LIMIT} SPINS"
    ctk.CTkLabel(history, text=history_title, font=theme.CAPTION,
                 text_color=theme.INK).pack(side="left", padx=(16, 8), pady=10)
    ctk.CTkLabel(history, text="Newest first", font=theme.SMALL,
                 text_color=theme.MUTED).pack(side="left", padx=(0, 14))
    chips = ctk.CTkFrame(history, fg_color="transparent")
    chips.pack(side="left", pady=8)

    def show_history(numbers, empty_message="Your spins will appear here."):
        for widget in chips.winfo_children():
            widget.destroy()
        if not numbers:
            ctk.CTkLabel(chips, text=empty_message, font=theme.SMALL,
                         text_color=theme.MUTED).pack(side="left")
        for number in numbers:
            ctk.CTkLabel(
                chips, text=str(number), width=32, height=30, corner_radius=8,
                font=theme.BODY, fg_color=POCKET_COLORS[pocket_color(number)],
                text_color="white",
            ).pack(side="left", padx=(0, 5))

    recent_numbers = ()
    if online:
        show_history((), "Waiting for shared results…")
    else:
        try:
            recent_numbers = accounts.get_roulette_history(username)
        except sqlite3.Error:
            show_history((), "History unavailable.")
        else:
            show_history(recent_numbers)

    selected = None
    covered = frozenset()
    ball = None
    spinning = False
    timer = None
    advanced_bets = {}
    shared_state = None
    pending_round = None
    network = ThreadPoolExecutor(max_workers=1) if online else None
    polling = None
    last_wager = None

    def mark_pocket(number):
        if number is not None:
            color = BALL_BORDER if number == ball else theme.GOLD if number in covered else theme.BORDER
            pockets[number].configure(border_color=color)

    def move_ball(number):
        nonlocal ball
        if ball == number:
            return
        previous, ball = ball, number
        mark_pocket(previous)
        mark_pocket(ball)
        badge.configure(text=str(number), fg_color=POCKET_COLORS[pocket_color(number)], text_color="white")

    def select_bet(bet):
        nonlocal selected, covered
        if selected in buttons:
            buttons[selected].configure(border_color=theme.BORDER)
        previous, selected = covered, bet
        covered = bet.pockets
        for number in previous | covered:
            mark_pocket(number)
        if bet in buttons and len(covered) > 1:
            buttons[bet].configure(border_color=theme.GOLD)
        if bet in buttons:
            kind.set("Choose a type")
            choice.set("Choose numbers")
            choice.configure(state="disabled")
        selection.configure(text=f"Selected: {bet.label}  ·  Pays {bet.odds}:1")
        if online:
            update_shared_controls()
        else:
            spin_button.configure(state="normal")

    def update_bets(name):
        nonlocal advanced_bets
        advanced_bets = {bet.label: bet for bet in game.bets_for(name)}
        choice.configure(values=list(advanced_bets), state="readonly")
        label = next(iter(advanced_bets))
        choice.set(label)
        select_bet(advanced_bets[label])

    def set_spinning(active):
        nonlocal spinning
        spinning = active
        for widget in (*buttons.values(), stake, spin_button, back):
            widget.configure(state="disabled" if active else "normal")
        kind.configure(state="disabled" if active else "readonly")
        choice.configure(state="readonly" if not active and selected not in buttons else "disabled")

    def update_shared_controls():
        open_round = shared_state is not None and shared_state["server_time"] < shared_state["bets_close"]
        ready = open_round and selected is not None and pending_round is None
        spin_button.configure(state="normal" if ready else "disabled")

    def poll_shared():
        nonlocal polling
        requested = pending_round
        future = network.submit(accounts.wheel_state, requested)

        def collect():
            nonlocal polling, shared_state, pending_round, recent_numbers, last_wager
            if not future.done():
                polling = screen.after(80, collect)
                return
            try:
                shared_state = future.result()
            except ApiError as error:
                shared_state = None
                update_shared_controls()
                result.configure(text="Shared wheel unavailable")
                detail.configure(text=str(error))
                if error.status == 401:
                    return
                polling = screen.after(850, poll_shared)
                return

            state = shared_state
            wager = state["wager"]
            if wager is not None and wager["payout"] is None:
                pending_round = wager["round_id"]
                detail.configure(text=f"Bet accepted  ·  {wager['label']}  ·  {theme.money(wager['stake'])}")
            elif wager is not None and wager["payout"] is not None:
                pending_round = None
                last_wager = wager
            elif pending_round is not None and requested == pending_round:
                pending_round = None
                detail.configure(text="The server has no bet for that round.")

            balance.configure(text=f"Play balance  {theme.money(state['balance_cents'])}")
            history_numbers = tuple(row["number"] for row in state["history"])
            if history_numbers != recent_numbers:
                recent_numbers = history_numbers
                show_history(recent_numbers)

            now = state["server_time"]
            if now < state["bets_close"]:
                if recent_numbers:
                    move_ball(recent_numbers[0])
                result.configure(text=f"Shared wheel  ·  Bets close in {math.ceil(state['bets_close'] - now)}s")
            else:
                wheel_step = int((now - state["bets_close"]) * 10)
                move_ball(WHEEL_ORDER[wheel_step % len(WHEEL_ORDER)])
                result.configure(text=f"Wheel spinning… {math.ceil(max(0, state['reveals_at'] - now))}s")

            if pending_round is None:
                if last_wager is not None:
                    net = last_wager["payout"] - last_wager["stake"]
                    summary = f"Won {theme.money(net)}" if net > 0 else f"Lost {theme.money(-net)}"
                    number = last_wager["number"]
                    detail.configure(text=f"Your last bet  ·  {number} {pocket_color(number).lower()}  ·  {summary}")
                elif recent_numbers:
                    number = recent_numbers[0]
                    detail.configure(text=f"Last result  ·  {number} {pocket_color(number).lower()}")
                else:
                    detail.configure(text="Choose a bet before the shared round closes.")
            update_shared_controls()
            polling = screen.after(450, poll_shared)

        polling = screen.after(80, collect)

    def place_shared_bet():
        nonlocal pending_round
        if (shared_state is None or pending_round is not None
                or shared_state["server_time"] >= shared_state["bets_close"]):
            return
        try:
            stake_cents = parse_bet_cents(stake.get())
        except ValueError as error:
            messagebox.showerror("Roulette", str(error), parent=screen.winfo_toplevel())
            return
        kind_name = next(kind for kind in game.bet_types if selected in game.bets_for(kind))
        pending_round = shared_state["round_id"]
        update_shared_controls()
        try:
            accounts.place_wager(pending_round, kind_name, selected.label, stake_cents)
        except ApiError as error:
            if error.status is not None and 400 <= error.status < 500:
                pending_round = None
            messagebox.showerror("Roulette", str(error), parent=screen.winfo_toplevel())

    def spin():
        if online:
            if selected is not None:
                place_shared_bet()
            return
        if spinning or selected is None:
            return
        bet = selected

        try:
            stake_cents = parse_bet_cents(stake.get())
            saved_balance, landed = accounts.settle_roulette_spin(
                username, stake_cents, lambda _: game.spin(bet, stake_cents)
            )
        except (ValueError, sqlite3.Error) as error:
            messagebox.showerror("Roulette", str(error), parent=screen.winfo_toplevel())
            return

        set_spinning(True)
        start_index = WHEEL_ORDER.index(ball) if ball is not None else 0
        steps = 2 * len(WHEEL_ORDER) + (WHEEL_ORDER.index(landed.number) - start_index) % len(WHEEL_ORDER)
        start = monotonic()
        detail.configure(text=f"{bet.label}  ·  Stake {theme.money(stake_cents)}")

        def animate():
            nonlocal recent_numbers, timer
            timer = None
            elapsed = monotonic() - start
            if elapsed >= SPIN_SECONDS:
                move_ball(landed.number)
                balance.configure(text=f"Play balance  {theme.money(saved_balance)}")
                result.configure(text=f"{landed.number} landed  ·  {landed.color}")
                try:
                    recent_numbers = accounts.get_roulette_history(username)
                except sqlite3.Error:
                    recent_numbers = (landed.number, *recent_numbers)[:ROULETTE_HISTORY_LIMIT]
                show_history(recent_numbers)
                net = landed.payout_cents - stake_cents
                description = f"Won {theme.money(net)}" if net > 0 else f"Lost {theme.money(-net)}"
                detail.configure(text=f"{bet.label}  ·  Stake {theme.money(stake_cents)}  ·  {description}")
                set_spinning(False)
                return

            progress = elapsed / SPIN_SECONDS
            offset = int(steps * (1 - (1 - progress) ** 1.65))
            move_ball(WHEEL_ORDER[(start_index + offset) % len(WHEEL_ORDER)])
            result.configure(text=f"Spinning… {math.ceil(SPIN_SECONDS - elapsed)}s")
            timer = screen.after(65, animate)

        animate()

    def cancel_timer(event):
        if event.widget is not screen:
            return
        if timer is not None:
            screen.after_cancel(timer)
        if polling is not None:
            screen.after_cancel(polling)
        if network is not None:
            network.shutdown(wait=False, cancel_futures=True)

    tk.Misc.bind(screen, "<Destroy>", cancel_timer, add="+")
    stake.bind("<Return>", lambda _: spin())
    if online:
        spin_button.configure(text="Place bet")
        poll_shared()
