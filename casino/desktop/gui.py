import os
import sqlite3
from tkinter import StringVar, messagebox

import customtkinter as ctk
from PIL import Image, ImageOps

from casino.core.accounts import AccountStore, DATA_DIR, parse_bet_cents
from casino.desktop.api_client import RemoteAccounts
from casino.core.engine import Casino
from casino.core.roulette import Roulette
from casino.desktop.roulette_view import build_roulette
from casino.desktop.slots_view import build_game
from casino.desktop import ui_theme as theme


def run():
    ctk.set_appearance_mode("dark")

    app = ctk.CTk()
    app.title("Casino")
    app.geometry("1040x750")
    app.minsize(900, 700)
    app.configure(fg_color=theme.PAGE)

    try:
        api_url = os.environ.get("CASINO_API_URL")
        accounts = RemoteAccounts(api_url) if api_url else AccountStore()
    except (OSError, ValueError, sqlite3.Error) as error:
        messagebox.showerror("Account database", str(error), parent=app)
        app.destroy()
        return

    screen = None

    def show_screen(build_screen, *args):
        nonlocal screen
        if screen is not None:
            screen.destroy()
        screen = ctk.CTkFrame(app, fg_color=theme.PAGE, corner_radius=0)
        screen.pack(fill="both", expand=True)
        build_screen(screen, *args)

    def show_login():
        show_screen(build_login, accounts, start_session)

    def start_session(username):
        slots = Casino.Game()
        roulette = Roulette()
        slots_bet = StringVar(master=app, value="20")
        roulette_bet = StringVar(master=app, value="20")

        def show_lobby():
            show_screen(build_lobby, username, show_slots, show_roulette, show_account)

        def show_with_totals(builder, *args):
            try:
                totals = accounts.get_totals(username)
            except (sqlite3.Error, ValueError) as error:
                messagebox.showerror("Account database", str(error), parent=app)
                return
            show_screen(builder, totals, *args)

        def show_slots():
            show_with_totals(build_game, accounts, username, slots, slots_bet, show_lobby)

        def show_roulette():
            show_with_totals(build_roulette, accounts, username, roulette, roulette_bet, show_lobby)

        def sign_out():
            if isinstance(accounts, RemoteAccounts):
                try:
                    accounts.logout()
                except ValueError:
                    pass
            show_login()

        def show_account():
            show_with_totals(build_account, username, accounts, show_lobby, sign_out)

        show_lobby()

    show_login()
    app.mainloop()


def build_login(screen, accounts, on_login):
    card = theme.panel(screen, width=420, height=485)
    card.pack(pady=(78, 24))
    card.pack_propagate(False)

    ctk.CTkLabel(card, text="CASINO", font=theme.CAPTION, text_color=theme.GOLD).pack(pady=(30, 6))
    ctk.CTkLabel(card, text="Welcome", font=theme.TITLE, text_color=theme.INK).pack()
    account_mode = "shared" if isinstance(accounts, RemoteAccounts) else "local"
    ctk.CTkLabel(card, text=f"Sign in or create a {account_mode} account", font=theme.BODY,
                 text_color=theme.MUTED).pack(pady=(5, 22))

    username_entry = theme.entry(card, width=298, placeholder_text="Username")
    username_entry.pack(pady=(0, 10))
    password_entry = theme.entry(card, width=298, placeholder_text="Password", show="*")
    password_entry.pack()

    ctk.CTkLabel(
        card,
        text="New passwords need 8+ characters, including a letter and a digit. "
             "Choose a password different from your username.",
        font=theme.SMALL, text_color=theme.MUTED, wraplength=300, justify="center",
    ).pack(pady=(10, 4))

    status = ctk.CTkLabel(card, text="", font=theme.SMALL, wraplength=300)

    def sign_in():
        try:
            username = accounts.authenticate(username_entry.get(), password_entry.get())
        except ValueError as error:
            status.configure(text=str(error), text_color=theme.ERROR)
            return
        except sqlite3.Error:
            status.configure(text="Could not access the account database.", text_color=theme.ERROR)
            return

        if username is None:
            status.configure(text="Incorrect username or password.", text_color=theme.ERROR)
            return
        on_login(username)

    def register():
        try:
            accounts.register(username_entry.get(), password_entry.get())
        except ValueError as error:
            status.configure(text=str(error), text_color=theme.ERROR)
        except sqlite3.Error:
            status.configure(text="Could not access the account database.", text_color=theme.ERROR)
        else:
            password_entry.delete(0, "end")
            status.configure(text="Account created. Enter your password to sign in.",
                             text_color=theme.SUCCESS)

    theme.button(card, "Sign in", sign_in, primary=True, width=298).pack(pady=(12, 8))
    theme.button(card, "Register", register, width=298).pack()
    status.pack(pady=8)
    add_exit_button(screen)
    password_entry.bind("<Return>", lambda event: sign_in())
    username_entry.focus_set()


def page_header(screen, title, action, command):
    header = ctk.CTkFrame(screen, fg_color="transparent")
    header.pack(fill="x", padx=36, pady=(26, 24))
    heading = ctk.CTkFrame(header, fg_color="transparent")
    heading.pack(side="left")
    ctk.CTkLabel(heading, text="CASINO", font=theme.CAPTION,
                 text_color=theme.GOLD).pack(anchor="w")
    ctk.CTkLabel(heading, text=title, font=theme.TITLE, text_color=theme.INK).pack(anchor="w")
    theme.button(header, action, command, width=112).pack(side="right")


def lobby_icon(filename):
    try:
        with Image.open(DATA_DIR / "icons" / filename) as source:
            artwork = ImageOps.contain(source.convert("RGBA"), (220, 220), Image.Resampling.LANCZOS)
    except OSError:
        return None
    return ctk.CTkImage(artwork, size=(110, 110))


def build_lobby(screen, username, on_slots, on_roulette, on_account):
    page_header(screen, "Games", username, on_account)

    shelf = theme.panel(screen)
    shelf.pack(fill="x", padx=36)
    ctk.CTkLabel(shelf, text="Game library", font=theme.SUBTITLE,
                 text_color=theme.INK).pack(anchor="w", padx=22, pady=(17, 0))
    ctk.CTkLabel(shelf, text="Choose a game to play", font=theme.BODY,
                 text_color=theme.MUTED).pack(anchor="w", padx=22, pady=(4, 17))

    grid = ctk.CTkFrame(shelf, fg_color="transparent")
    grid.pack(fill="x", padx=14, pady=(0, 16))
    grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="game_cards")

    slots_icon = lobby_icon("slots_main_icon.png")
    roulette_icon = lobby_icon("roulette_main_icon.png")

    theme.button(
        grid, "Cascades Slots", on_slots, image=slots_icon, compound="top",
        fg_color=("#F3E3CD", "#332522"), hover_color=("#EAD1B2", "#49312C"),
        font=theme.SUBTITLE, height=174, corner_radius=12, border_color=theme.GOLD,
    ).grid(row=0, column=0, sticky="nsew", padx=8)

    theme.button(
        grid, "European Roulette", on_roulette, image=roulette_icon, compound="top",
        fg_color=("#DFEDE3", "#203B30"), hover_color=("#C9E0D1", "#294B3D"),
        font=theme.SUBTITLE, height=174, corner_radius=12, border_color=theme.GOLD,
    ).grid(row=0, column=1, sticky="nsew", padx=8)

    placeholder = ctk.CTkFrame(grid, fg_color=theme.FIELD, corner_radius=12,
                               border_width=1, border_color=theme.BORDER)
    placeholder.grid(row=0, column=2, sticky="nsew", padx=8)
    ctk.CTkLabel(placeholder, text="03", font=("Georgia", 28),
                 text_color=theme.GOLD).place(relx=0.5, rely=0.42, anchor="center")
    ctk.CTkLabel(placeholder, text="Coming soon", font=theme.SMALL,
                 text_color=theme.MUTED).place(relx=0.5, rely=0.62, anchor="center")

    add_exit_button(screen)


def add_exit_button(screen):
    theme.button(screen, "Exit", screen.winfo_toplevel().destroy, width=170, height=50,
                 font=("Segoe UI", 17, "bold")).pack(side="bottom", pady=(8, 24))


def build_account(screen, totals, username, accounts, on_back, on_logout):
    page_header(screen, "Account", "Back to games", on_back)

    profile = theme.panel(screen)
    profile.pack(fill="x", padx=36)
    ctk.CTkLabel(profile, text=username, font=theme.SUBTITLE, text_color=theme.INK).pack(
        anchor="w", padx=22, pady=(18, 6)
    )
    figures = ctk.CTkFrame(profile, fg_color="transparent")
    figures.pack(fill="x", padx=22, pady=(0, 18))
    for column, (caption, cents) in enumerate((
        ("PLAY BALANCE", totals.balance_cents),
        ("TOTAL DEPOSITED", totals.deposit_cents),
        ("TOTAL WITHDRAWN", totals.withdrawal_cents),
    )):
        figures.grid_columnconfigure(column, weight=1)
        ctk.CTkLabel(figures, text=caption, font=theme.CAPTION,
                     text_color=theme.MUTED).grid(row=0, column=column, sticky="w")
        amount_label = ctk.CTkLabel(figures, text=theme.money(cents), font=theme.NUMBERS, text_color=theme.GOLD)
        amount_label.grid(row=1, column=column, sticky="w")
        if column == 0:
            balance_label = amount_label

    wallet_actions = ctk.CTkFrame(profile, fg_color="transparent")
    wallet_actions.pack(anchor="w", padx=22, pady=(0, 16))
    for caption in ("Deposit", "Withdraw"):
        theme.button(wallet_actions, caption, None, width=112, state="disabled",
                     text_color_disabled=theme.MUTED).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(wallet_actions, text="Coming soon", font=theme.SMALL,
                 text_color=theme.MUTED).pack(side="left")

    if isinstance(accounts, RemoteAccounts) and accounts.is_owner:
        def adjust(target, amount, reason):
            target, amount = target.strip(), amount.strip()
            cents = -parse_bet_cents(amount[1:]) if amount.startswith("-") else parse_bet_cents(amount)
            updated = accounts.adjust_balance(target, cents, reason.strip())
            if target.casefold() == username.casefold():
                balance_label.configure(text=theme.money(updated))
            return f"{target} balance is now {theme.money(updated)}."

        def open_adjustment():
            account_dialog(screen, "Adjust play balance", (
                ("Username", False), ("Adjustment in dollars (e.g. -25 or 100)", False), ("Reason", False),
            ), adjust)

        theme.button(wallet_actions, "Adjust balance", open_adjustment, width=145).pack(side="left", padx=(10, 0))

    security = theme.panel(screen)
    security.pack(fill="x", padx=36, pady=(16, 0))
    heading = ctk.CTkFrame(security, fg_color="transparent")
    heading.pack(side="left", padx=22, pady=12)
    ctk.CTkLabel(heading, text="Account details", font=theme.SUBTITLE,
                 text_color=theme.INK).pack(anchor="w")
    try:
        email = accounts.get_email(username)
        email_text = email or "No email added"
    except (ValueError, sqlite3.Error):
        email = None
        email_text = "Email unavailable"
    email_label = ctk.CTkLabel(heading, text=email_text, font=theme.SMALL,
                               text_color=theme.MUTED, wraplength=310, justify="left")
    email_label.pack(anchor="w")

    def save_email(address):
        saved = accounts.save_email(username, address)
        email_label.configure(text=saved)
        email_button.configure(text="Change email")
        return "Email saved."

    def open_email():
        try:
            current = accounts.get_email(username) or ""
        except (ValueError, sqlite3.Error) as error:
            messagebox.showerror("Email address", str(error), parent=screen.winfo_toplevel())
            return
        account_dialog(screen, "Email address", (("Email address", False),), save_email, initial=current)

    def save_password(current, new, confirmation):
        if new != confirmation:
            raise ValueError("The new passwords do not match.")
        accounts.change_password(username, current, new)
        return "Password changed."

    def open_password():
        account_dialog(screen, "Change password", (
            ("Current password", True), ("New password", True), ("Confirm new password", True),
        ), save_password)

    email_button = theme.button(security, "Change email" if email else "Add email", open_email, width=120)
    email_button.pack(side="right", padx=(8, 22))
    theme.button(security, "Change password", open_password, width=148).pack(side="right")

    settings = theme.panel(screen)
    settings.pack(fill="x", padx=36, pady=16)
    ctk.CTkLabel(settings, text="Theme", font=theme.SUBTITLE,
                 text_color=theme.INK).pack(side="left", padx=22, pady=16)
    selector = ctk.CTkSegmentedButton(
        settings, values=["Light", "Dark"], command=ctk.set_appearance_mode,
        fg_color=theme.FIELD, selected_color=("#E4CA9F", "#5C4335"),
        selected_hover_color=("#D9BB8B", "#6D5140"), unselected_color=theme.FIELD,
        unselected_hover_color=theme.HOVER, text_color=theme.INK,
        font=("Segoe UI", 13, "bold"), height=34,
    )
    selector.set(ctk.get_appearance_mode())
    selector.pack(side="right", padx=20, pady=14)

    theme.button(screen, "Sign out", on_logout, width=112).pack(anchor="w", padx=36)


def account_dialog(screen, title, fields, save, initial=""):
    dialog = ctk.CTkToplevel(screen)
    dialog.title(title)
    dialog.transient(screen.winfo_toplevel())
    dialog.configure(fg_color=theme.PAGE)
    dialog.resizable(False, False)

    card = theme.panel(dialog)
    card.pack(padx=16, pady=16)
    ctk.CTkLabel(card, text=title, font=theme.SUBTITLE,
                 text_color=theme.INK).pack(anchor="w", padx=20, pady=(16, 8))
    entries = []
    for label, masked in fields:
        ctk.CTkLabel(card, text=label, font=theme.SMALL,
                     text_color=theme.MUTED).pack(anchor="w", padx=20)
        entry = theme.entry(card, width=320, show="*" if masked else "")
        entry.pack(padx=20, pady=(0, 8))
        entries.append(entry)
    if initial:
        entries[0].insert(0, initial)

    status = ctk.CTkLabel(card, text="", font=theme.SMALL, wraplength=320)
    status.pack(padx=20)

    def submit():
        try:
            message = save(*(entry.get() for entry in entries))
        except ValueError as error:
            status.configure(text=str(error), text_color=theme.ERROR)
        except sqlite3.Error:
            status.configure(text="Could not update the account database.", text_color=theme.ERROR)
        else:
            dialog.destroy()
            messagebox.showinfo(title, message, parent=screen.winfo_toplevel())

    actions = ctk.CTkFrame(card, fg_color="transparent")
    actions.pack(anchor="e", padx=20, pady=(6, 16))
    theme.button(actions, "Cancel", dialog.destroy, width=90).pack(side="left", padx=(0, 8))
    theme.button(actions, "Save", submit, primary=True, width=90).pack(side="left")
    dialog.bind("<Escape>", lambda event: dialog.destroy())
    entries[-1].bind("<Return>", lambda event: submit())
    dialog.update_idletasks()
    dialog.grab_set()
    entries[0].focus_set()
