import sqlite3
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

from casino.core.accounts import DATA_DIR, parse_bet_cents
from casino.desktop.api_client import RemoteAccounts
from casino.core.engine import Casino, SYMBOLS, SpecialEffect
from casino.desktop import ui_theme as theme


CELL_SIZE = 98
POP_SCALES = (1.0, 0.75, 0.5, 0.25, 0.0)
HEAT_STEPS = (
    (1.00, "#9333EA"),
    (1.06, "#A33ACE"),
    (1.12, "#BD398D"),
    (1.18, "#DB453F"),
    (1.24, "#EA580C"),
    (1.30, "#F59E0B"),
)
SYMBOL_COLORS = {
    1: "#78352B", 2: "#51405D", 3: "#673B4B", 4: "#785130",
    5: "#2C5156", 6: "#683986", 7: "#735729",
}


def fall_paths(board, previous=None, cleared=()):
    """Pair each landing cell with its starting row above or on the board."""
    rows, cols = board.shape
    for col in range(cols):
        survivors = []
        if previous is not None:
            survivors = [
                row for row in range(rows)
                if 1 <= previous[row, col] <= 5 and (row, col) not in cleared
            ]
        missing = rows - len(survivors)
        origins = [*range(-missing, 0), *survivors]
        for row, origin in enumerate(origins):
            yield col, origin, row, int(board[row, col])


def show_backdrop(screen):
    try:
        with Image.open(DATA_DIR / "images" / "slots_background.png") as source:
            artwork = source.convert("RGB")
    except OSError:
        return

    backdrop = tk.Label(screen, bd=0, highlightthickness=0)
    backdrop.place(x=0, y=0, relwidth=1, relheight=1)

    def resize(event):
        size = (event.width, event.height)
        if min(size) <= 1 or size == getattr(backdrop, "image_size", None):
            return
        cropped = ImageOps.fit(artwork, size, Image.Resampling.LANCZOS, centering=(1.0, 0.5))
        backdrop.image = ImageTk.PhotoImage(cropped, master=backdrop)
        backdrop.configure(image=backdrop.image)
        backdrop.image_size = size

    tk.Misc.bind(screen, "<Configure>", resize, add="+")


def build_game(screen, totals, accounts, username, game, bet_value, on_back):
    show_backdrop(screen)

    back_button = theme.button(screen, "Back to games", on_back, width=125)
    back_button.place(x=24, y=22)

    art_caption = theme.panel(screen)
    art_caption.place(relx=0, rely=1, x=24, y=-24, anchor="sw")
    ctk.CTkLabel(art_caption, text="CASINO  /  01", font=theme.CAPTION,
                 text_color=theme.GOLD).pack(anchor="w", padx=16, pady=(10, 0))
    ctk.CTkLabel(art_caption, text="Dragon's Hoard", font=theme.SUBTITLE,
                 text_color=theme.INK).pack(anchor="w", padx=16, pady=(0, 12))

    console = theme.panel(screen)
    console.pack(side="right", padx=24, pady=6)

    header = ctk.CTkFrame(console, fg_color="transparent")
    header.pack(fill="x", padx=18, pady=(8, 6))
    ctk.CTkLabel(header, text="Cascades Slots", font=("Georgia", 27, "bold"),
                 text_color=theme.INK).pack(side="left")
    balance_panel = ctk.CTkFrame(header, fg_color="transparent")
    balance_panel.pack(side="right")
    ctk.CTkLabel(balance_panel, text="PLAY BALANCE", font=theme.CAPTION,
                 text_color=theme.MUTED, height=15).pack(anchor="e")
    balance = ctk.CTkLabel(balance_panel, text=theme.money(totals.balance_cents), font=theme.NUMBERS,
                           text_color=theme.GOLD)
    balance.pack(anchor="e")

    status = ctk.CTkFrame(console, fg_color=theme.FIELD, corner_radius=9)
    status.pack(fill="x", padx=18, pady=(0, 8))
    result = ctk.CTkLabel(status, text="Ready to spin", font=("Segoe UI", 15, "bold"),
                          text_color=theme.INK)
    result.pack(side="left", padx=10, pady=3)
    counter_font = ctk.CTkFont(family="Segoe UI", size=14)
    detail = ctk.CTkLabel(status, text=f"Multiplier  x{game.multiplier}", font=counter_font,
                          text_color=theme.GOLD, height=28)
    detail.pack(side="right", padx=10)

    rows, cols = game.map.shape
    light = ctk.get_appearance_mode() == "Light"
    canvas = tk.Canvas(console, width=cols * CELL_SIZE, height=rows * CELL_SIZE,
                       background="#D1B58E" if light else "#181619", highlightthickness=0)
    canvas.pack(padx=18)
    icons = {}
    for symbol, name in SYMBOLS.items():
        try:
            with Image.open(DATA_DIR / "icons" / f"{name}.png") as source:
                icon = source.convert("RGBA")
                icon_size = round(CELL_SIZE * 2 / 3)
                icon.thumbnail((icon_size, icon_size), Image.Resampling.LANCZOS)
                scales = POP_SCALES
                if symbol == 6:
                    scales += tuple(scale for scale, _ in HEAT_STEPS[1:])
                icons[symbol] = {
                    scale: ImageTk.PhotoImage(
                        icon.resize((max(1, round(icon.width * scale)),
                                     max(1, round(icon.height * scale))), Image.Resampling.LANCZOS),
                        master=canvas,
                    )
                    for scale in scales if scale
                }
        except OSError:
            continue

    controls = ctk.CTkFrame(console, fg_color="transparent")
    controls.pack(pady=(7, 3))
    ctk.CTkLabel(controls, text="BET", font=theme.CAPTION,
                 text_color=theme.MUTED).pack(side="left", padx=(0, 8))
    bet = theme.entry(controls, width=94, textvariable=bet_value)
    bet.pack(side="left", padx=(0, 12))
    spin_button = theme.button(controls, "Spin", lambda: spin(), primary=True, width=134, height=40)
    spin_button.pack(side="left")

    tips = (
        "Five or more matching symbols win when connected up, down, left, or right. "
        "M adds to the spin multiplier. L upgrades adjacent symbols."
    )
    ctk.CTkLabel(console, text=tips, font=theme.SMALL, text_color=theme.MUTED,
                 wraplength=cols * CELL_SIZE - 20, justify="center").pack(pady=(4, 6))

    empty_color = "#E6D4B8" if light else "#2F282B"
    for row in range(rows):
        for col in range(cols):
            x, y = col * CELL_SIZE, row * CELL_SIZE
            canvas.create_rectangle(x + 3, y + 3, x + CELL_SIZE - 3, y + CELL_SIZE - 3,
                                    fill=empty_color, outline="")

    def draw_tile(symbol, row, col, scale=1.0, heat_color=None):
        if not symbol or not scale or row <= -1:
            return
        x, y = (col + 0.5) * CELL_SIZE, (row + 0.5) * CELL_SIZE
        half = (CELL_SIZE / 2 - 3) * scale
        canvas.create_rectangle(x - half, y - half, x + half, y + half,
                                fill=heat_color or SYMBOL_COLORS[symbol],
                                outline="#FDE68A" if heat_color else "",
                                width=2, tags="tile")
        if symbol in icons:
            canvas.create_image(x, y, image=icons[symbol][scale], tags="tile")
        else:
            canvas.create_text(x, y, text=SYMBOLS[symbol], fill="white",
                               font=("Arial", round(CELL_SIZE * 0.31 * scale), "bold"), tags="tile")

    def draw_board(board, clusters=(), cleared=(), pop_step=0):
        canvas.delete("tile")
        for row in range(rows):
            for col in range(cols):
                step = pop_step if (row, col) in cleared else 0
                draw_tile(int(board[row, col]), row, col, POP_SCALES[step])

        for cluster in clusters:
            positions = set(cluster)
            for row, col in cluster:
                x, y = (col + 0.5) * CELL_SIZE, (row + 0.5) * CELL_SIZE
                for dr, dc in ((0, 1), (1, 0)):
                    if (row + dr, col + dc) in positions:
                        end_x, end_y = x + dc * CELL_SIZE, y + dr * CELL_SIZE
                        canvas.create_line(x, y, end_x, end_y, fill="#422006", width=8, tags="tile")
                        canvas.create_line(x, y, end_x, end_y, fill="#FDE047", width=4, tags="tile")
                canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#FDE047", outline="#422006", tags="tile")

    def set_controls(state):
        for widget in (back_button, spin_button, bet):
            widget.configure(state=state)

    def spin():
        frames = []
        try:
            bet_cents = parse_bet_cents(bet.get())

            def play(balance_cents):
                player = Casino.Player(balance=balance_cents / 100, bet=bet_cents / 100)
                payout = game.roll(player, record_frame=frames.append)
                return round(payout * 100)

            if isinstance(accounts, RemoteAccounts):
                saved_balance, payout, frames = accounts.spin_slots(bet_cents)
                game.map = frames[-1].board
                game.multiplier = frames[-1].multiplier
            else:
                saved_balance, payout = accounts.settle_spin(username, bet_cents, play)
        except (ValueError, RuntimeError, sqlite3.Error) as error:
            try:
                latest = accounts.get_totals(username)
                balance.configure(text=theme.money(latest.balance_cents))
            except (ValueError, sqlite3.Error):
                pass
            messagebox.showerror("Spin", str(error), parent=screen.winfo_toplevel())
            return

        def finish():
            draw_board(game.map)
            try:
                current_balance = accounts.get_totals(username).balance_cents
            except (ValueError, sqlite3.Error):
                current_balance = saved_balance
            balance.configure(text=theme.money(current_balance))
            result.configure(text=f"Won {theme.money(payout)}" if payout else "No win this spin")
            detail.configure(text=f"Multiplier  x{game.multiplier}   |   Bet  {theme.money(bet_cents)}")
            set_controls("normal")

        set_controls("disabled")

        def drop(board, on_landed, previous=None, cleared=()):
            paths = list(fall_paths(board, previous, cleared))
            duration = 14
            stagger = 3 if previous is None else 0

            def tick(step):
                canvas.delete("tile")
                for col, origin, row, symbol in paths:
                    delay = (rows - 1 - row) * stagger
                    progress = min(1, max(0, step - delay) / duration)
                    draw_tile(symbol, origin + (row - origin) * progress ** 2, col)

                if step < duration + (rows - 1) * stagger:
                    screen.after(16, tick, step + 1)
                else:
                    if previous is None:
                        result.configure(text=frames[0].message)
                    screen.after(600 if previous is None else 340, on_landed)

            tick(0)

        def drop_refill(index, previous, cleared=()):
            frame = frames[index]
            result.configure(text=frame.message)
            detail.configure(text=f"Multiplier  x{frame.multiplier}")
            drop(frame.board, lambda: play_frame(index + 1), previous, cleared)

        def play_special(effect, on_done):
            row, col = effect.position
            x, y = (col + 0.5) * CELL_SIZE, (row + 0.5) * CELL_SIZE
            if effect.target is not None:
                draw_board(effect.board)
                detail.configure(text=f"Multiplier  x{effect.multiplier}")
                result.configure(text="Level up  |  Lightning strike")
                target_row, target_col = effect.target
                dx, dy = (target_col - col) * CELL_SIZE, (target_row - row) * CELL_SIZE
                tx, ty = x + dx, y + dy
                points = (
                    x, y,
                    x + dx * 0.27 - dy * 0.12, y + dy * 0.27 + dx * 0.12,
                    x + dx * 0.52 + dy * 0.12, y + dy * 0.52 - dx * 0.12,
                    x + dx * 0.74 - dy * 0.10, y + dy * 0.74 + dx * 0.10,
                    tx, ty,
                )
                canvas.create_line(*points, fill="#2563EB", width=9, tags="tile")
                canvas.create_line(*points, fill="#E0F2FE", width=4, tags="tile")
                radius = CELL_SIZE * 0.41
                canvas.create_oval(tx - radius, ty - radius, tx + radius, ty + radius,
                                   outline="#7DD3FC", width=3, tags="tile")

                def settle_strike():
                    draw_board(effect.board)
                    screen.after(35, on_done)

                screen.after(110, settle_strike)
                return

            result.configure(text="Multiplier heating up")
            detail.configure(text=f"Multiplier  x{effect.multiplier - 1}")

            def start_burst():
                draw_board(effect.board)
                result.configure(text="Multiplier burst  |  +1")
                detail.configure(text=f"Multiplier  x{effect.multiplier}")
                counter_font.configure(size=20, weight="bold")
                burst(0)

            def heat(step):
                draw_board(effect.board)
                scale, color = HEAT_STEPS[step]
                draw_tile(6, row, col, scale, heat_color=color)
                if step + 1 < len(HEAT_STEPS):
                    screen.after(85, heat, step + 1)
                else:
                    screen.after(100, start_burst)

            def burst(step):
                canvas.delete("burst")
                radius = 10 + step * 7
                canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                                   outline="#FDE047", width=max(1, 5 - step), tags="burst")
                for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                    canvas.create_line(x + dx * radius, y + dy * radius,
                                       x + dx * (radius + 8), y + dy * (radius + 8),
                                       fill="#FB923C", width=3, tags="burst")
                canvas.create_text(x, max(15, y - step * 4), text="+1",
                                   fill="#FDE047", font=("Arial", 20, "bold"), tags="burst")
                if step < 5:
                    screen.after(40, burst, step + 1)
                else:
                    screen.after(80, end_burst)

            def end_burst():
                canvas.delete("burst")
                counter_font.configure(size=14, weight="normal")
                on_done()

            heat(0)

        def play_frame(index):
            if index == len(frames):
                finish()
                return

            frame = frames[index]
            if isinstance(frame, SpecialEffect):
                play_special(frame, lambda: play_frame(index + 1))
                return
            if not frame.clusters:
                drop_refill(index, frames[index - 1].board)
                return

            result.configure(text=frame.message)
            detail.configure(text=f"Multiplier  x{frame.multiplier}")
            draw_board(frame.board, frame.clusters)
            cleared = {position for cluster in frame.clusters for position in cluster}

            def pop(step):
                draw_board(frame.board, cleared=cleared, pop_step=step)
                if step + 1 < len(POP_SCALES):
                    screen.after(38, pop, step + 1)
                else:
                    screen.after(70, drop_refill, index + 1, frame.board, cleared)

            screen.after(480, pop, 1)

        result.configure(text="Spinning...")
        detail.configure(text="Multiplier  x1")
        drop(frames[0].board, lambda: play_frame(1))

    draw_board(game.map)
