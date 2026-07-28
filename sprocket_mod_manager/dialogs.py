from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import customtkinter as ctk


def _dialog_layout(message: str) -> tuple[int, bool]:
    visual_lines = sum(max(1, (len(line) + 55) // 56) for line in message.splitlines() or [""])
    scrollable = visual_lines > 9 or len(message) > 520
    return (390 if scrollable else max(230, min(350, 184 + visual_lines * 22)), scrollable)


class ModalDialog(ctk.CTkToplevel):
    def __init__(
        self,
        parent: Any,
        *,
        title: str,
        eyebrow: str,
        message: str,
        confirm_text: str,
        palette: Mapping[str, str],
        cancel_text: str | None = None,
        tone: str = "accent",
    ) -> None:
        super().__init__(parent)
        self.withdraw()
        self.overrideredirect(True)
        self.transient(parent)
        self.result = False
        self._drag_offset = (0, 0)
        self._palette = palette
        self._tone_color = palette["danger"] if tone == "danger" else palette["accent"]
        self._button_color = palette["danger"] if tone == "danger" else palette.get("button_accent", palette["accent"])
        self._button_hover = palette["danger_hover"] if tone == "danger" else palette.get("button_accent_hover", palette["accent_hover"])

        height, scrollable = _dialog_layout(message)
        self._dialog_width = 520
        self._dialog_height = height
        self.configure(fg_color=palette["canvas"])
        self.geometry(f"{self._dialog_width}x{self._dialog_height}")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        outer = ctk.CTkFrame(
            self,
            corner_radius=4,
            fg_color=palette["surface"],
            border_width=1,
            border_color=palette["line"],
        )
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(outer, height=70, corner_radius=0, fg_color=palette["surface_high"])
        header.grid(row=0, column=0, sticky="ew", padx=1, pady=(1, 0))
        header.grid_propagate(False)
        header.grid_rowconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(header, width=3, corner_radius=0, fg_color=self._tone_color).grid(row=0, column=0, sticky="nsw")
        header_text = ctk.CTkFrame(header, corner_radius=0, fg_color="transparent")
        header_text.grid(row=0, column=1, sticky="nsew", padx=(17, 12), pady=(9, 8))
        kicker = ctk.CTkLabel(
            header_text,
            text=eyebrow,
            anchor="w",
            text_color=self._tone_color,
            height=18,
            font=ctk.CTkFont(family="Cascadia Mono", size=10, weight="bold"),
        )
        kicker.pack(fill="x")
        heading = ctk.CTkLabel(
            header_text,
            text=title,
            anchor="w",
            text_color=palette["text"],
            height=25,
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        heading.pack(fill="x")
        close_button = ctk.CTkButton(
            header,
            text="x",
            width=34,
            height=34,
            corner_radius=3,
            fg_color="transparent",
            hover_color=palette["surface_hover"],
            border_width=1,
            border_color=palette["line"],
            text_color=palette["text_soft"],
            font=ctk.CTkFont(size=15),
            command=self._cancel,
        )
        close_button.grid(row=0, column=2, padx=12)

        body = ctk.CTkFrame(outer, corner_radius=0, fg_color=palette["surface"])
        body.grid(row=1, column=0, sticky="nsew", padx=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        if scrollable:
            content = ctk.CTkTextbox(
                body,
                wrap="word",
                corner_radius=3,
                fg_color=palette["canvas"],
                border_width=1,
                border_color=palette["line_soft"],
                text_color=palette["text_soft"],
                scrollbar_button_color=palette["surface_hover"],
                scrollbar_button_hover_color=palette["line"],
                font=ctk.CTkFont(size=13),
            )
            content.grid(row=0, column=0, sticky="nsew", padx=20, pady=18)
            content.insert("1.0", message)
            content.configure(state="disabled")
        else:
            ctk.CTkLabel(
                body,
                text=message,
                anchor="nw",
                justify="left",
                wraplength=476,
                text_color=palette["text_soft"],
                font=ctk.CTkFont(size=13),
            ).grid(row=0, column=0, sticky="nsew", padx=22, pady=20)

        footer = ctk.CTkFrame(outer, height=60, corner_radius=0, fg_color=palette["sidebar"])
        footer.grid(row=2, column=0, sticky="ew", padx=1, pady=(0, 1))
        footer.grid_propagate(False)
        footer.grid_columnconfigure(0, weight=1)
        if cancel_text:
            ctk.CTkButton(
                footer,
                text=cancel_text,
                width=94,
                height=36,
                corner_radius=3,
                fg_color="transparent",
                hover_color=palette["surface_hover"],
                border_width=1,
                border_color=palette["line"],
                text_color=palette["text_soft"],
                font=ctk.CTkFont(size=12, weight="bold"),
                command=self._cancel,
            ).grid(row=0, column=1, padx=(12, 0), pady=12)
        self._confirm_button = ctk.CTkButton(
            footer,
            text=confirm_text,
            width=104,
            height=36,
            corner_radius=3,
            fg_color=self._button_color,
            hover_color=self._button_hover,
            text_color=palette["text"],
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._confirm,
        )
        self._confirm_button.grid(row=0, column=2, padx=12, pady=12)

        for widget in (header, header_text, kicker, heading):
            widget.bind("<ButtonPress-1>", self._begin_drag)
            widget.bind("<B1-Motion>", self._drag)
        self._bind_keyboard(self)

    def show(self) -> bool:
        self.update_idletasks()
        parent = self.master
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self._dialog_width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self._dialog_height) // 2)
        self.geometry(f"{self._dialog_width}x{self._dialog_height}+{x}+{y}")
        self.deiconify()
        self.lift()
        self.grab_set()
        self.focus_force()
        self._confirm_button.focus_set()
        self.wait_window()
        return self.result

    def _begin_drag(self, event: Any) -> None:
        self._drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _bind_keyboard(self, widget: Any) -> None:
        widget.bind("<Escape>", lambda _event: self._cancel(), add="+")
        widget.bind("<Return>", lambda _event: self._confirm(), add="+")
        for child in widget.winfo_children():
            self._bind_keyboard(child)

    def _drag(self, event: Any) -> None:
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        self.geometry(f"+{x}+{y}")

    def _confirm(self) -> None:
        self.result = True
        self._close()

    def _cancel(self) -> None:
        self.result = False
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def ask_confirmation(
    parent: Any,
    *,
    title: str,
    eyebrow: str,
    message: str,
    confirm_text: str,
    cancel_text: str,
    palette: Mapping[str, str],
    destructive: bool = False,
) -> bool:
    return ModalDialog(
        parent,
        title=title,
        eyebrow=eyebrow,
        message=message,
        confirm_text=confirm_text,
        cancel_text=cancel_text,
        palette=palette,
        tone="danger" if destructive else "accent",
    ).show()


def show_message(
    parent: Any,
    *,
    title: str,
    eyebrow: str,
    message: str,
    close_text: str,
    palette: Mapping[str, str],
    danger: bool = False,
) -> None:
    ModalDialog(
        parent,
        title=title,
        eyebrow=eyebrow,
        message=message,
        confirm_text=close_text,
        palette=palette,
        tone="danger" if danger else "accent",
    ).show()
