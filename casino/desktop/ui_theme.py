"""Shared light and dark colors for the desktop screens."""

import customtkinter as ctk


PAGE = ("#F2ECE1", "#151517")
PANEL = ("#FFF9EE", "#211F22")
FIELD = ("#F4EBDD", "#2C282A")
HOVER = ("#EFE0CA", "#363034")
BORDER = ("#D9C6AA", "#494044")
INK = ("#2B211D", "#F7EBDA")
MUTED = ("#67574E", "#C5B4A8")
GOLD = ("#85521D", "#F4C57F")
PRIMARY = ("#8C3D32", "#9E4537")
PRIMARY_HOVER = ("#733028", "#80372D")
ERROR = ("#973B31", "#FFA99C")
SUCCESS = ("#35604B", "#A3D9BA")

BODY = ("Segoe UI", 14)
SMALL = ("Segoe UI", 12)
CAPTION = ("Segoe UI", 11, "bold")
NUMBERS = ("Segoe UI Semibold", 25)
TITLE = ("Georgia", 38, "bold")
SUBTITLE = ("Georgia", 23, "bold")


def button(parent, text, command, *, primary=False, **options):
    style = {
        "fg_color": PRIMARY if primary else FIELD,
        "hover_color": PRIMARY_HOVER if primary else HOVER,
        "text_color": "#FFFFFF" if primary else INK,
        "border_color": BORDER,
        "border_width": 0 if primary else 1,
        "corner_radius": 9,
        "height": 38,
        "font": ("Segoe UI", 14, "bold"),
    }
    style.update(options)
    return ctk.CTkButton(parent, text=text, command=command, **style)


def panel(parent, **options):
    return ctk.CTkFrame(parent, fg_color=PANEL, border_color=BORDER,
                        border_width=1, corner_radius=16, **options)


def entry(parent, **options):
    return ctk.CTkEntry(parent, fg_color=FIELD, text_color=INK, placeholder_text_color=MUTED,
                        border_color=BORDER, border_width=1, corner_radius=8,
                        height=40, font=BODY, **options)


def money(cents):
    dollars, remainder = divmod(cents, 100)
    return f"${dollars:,}.{remainder:02d}"
