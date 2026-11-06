
# ============================================================
# MEQETA - FOOD & BEVERAGES BILLING APPLICATION
# ============================================================
#
# Database: MySQL / MariaDB
# Database Name: meqeta_billing
# Tables: bills, bill_items
#
# Features:
# - KOT-based bill lookup
# - Visible character-by-character typing and opened dropdown selection
# - One-second dropdown selection
# - MySQL synchronization
# - Bill Save / Update / Delete / Find / Clone
# - Billing calculations
# - Keyboard navigation
# - PMS automation
#
# ============================================================


# ============================================================
# IMPORTS
# ============================================================

import tkinter as tk

from tkinter import (
    ttk,
    messagebox,
    simpledialog
)

from pathlib import Path
from datetime import datetime, date
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_HALF_UP
)

import calendar
import copy
import json
import re
import threading
import ctypes
import os
import queue
import sys
import time
from pynput import mouse

import mysql.connector
from mysql.connector import Error

import pyautogui
import pyperclip


# ============================================================
# APPLICATION PATHS
# ============================================================

BASE = Path(__file__).resolve().parent

INK_FILE = BASE / "ink.txt"

JSON_BACKUP_FILE = BASE / "food_bills.json"

EXPORT_DIRECTORY = BASE / "pms_exports"

EXPORT_DIRECTORY.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# MYSQL DATABASE CONFIGURATION
# ============================================================

DB_CONFIG = {
    "host": os.environ.get(
        "MEQETA_DB_HOST",
        "127.0.0.1"
    ),

    "port": int(
        os.environ.get(
            "MEQETA_DB_PORT",
            "3306"
        )
    ),

    "user": os.environ.get(
        "MEQETA_DB_USER",
        "root"
    ),

    "database": "meqeta_billing",

    "connection_timeout": 5
}


# ============================================================
# KOT AUTOMATION CONFIGURATION
# ============================================================

# Delay between filling each field.
KOT_FIELD_DELAY_MS = 1000

# Time to keep dropdown open before selection.
KOT_DROPDOWN_HOLD_MS = 1000

# Initial delay before starting automation.
KOT_START_DELAY_MS = 1000

# Internal PMS automation.
INTERNAL_FIELD_DELAY_MS = 1000

INTERNAL_DROPDOWN_HOLD_MS = 1000

# External PMS automation.
TARGET_DELAY_MS = 3000

EXTERNAL_FIELD_DELAY = 1

EXTERNAL_DROPDOWN_DELAY = 1

EXTERNAL_NEXT_KEY = "enter"


# ============================================================
# PMS FIELD CONFIGURATION
# ============================================================

PMS_FIELDS = (
    "bill",
    "date",
    "waiter",
    "service",
    "kot"
)


DROPDOWN_FIELDS = (
    "date",
    "waiter",
    "service"
)


# ============================================================
# SEQUENTIAL KOT FORM FILLING ORDER
# ============================================================

KOT_FILL_ORDER = (
    "bill",
    "date",
    "waiter",
    "service",
    "kot",
    "room",
    "reg",
    "guest",
    "company",
    "arrival",
    "arrival_time",
    "departure",
    "departure_time",
    "stay",
    "detail"
)


# ============================================================
# PYAUTOGUI SETTINGS
# ============================================================

pyautogui.FAILSAFE = True

pyautogui.PAUSE = 0.1


# ============================================================
# MYSQL DATABASE CONNECTION
# ============================================================

def get_db_connection():
    """
    Connect to the local MySQL/MariaDB database.

    Supports both:
    - XAMPP with an empty password
    - MySQL with a configured password

    Password can be supplied using:
        MEQETA_DB_PASSWORD
    """

    config = dict(DB_CONFIG)

    config["password"] = os.environ.get(
        "MEQETA_DB_PASSWORD",
        ""
    )

    try:
        connection = mysql.connector.connect(
            **config
        )

        return connection

    except Error as error:
        raise RuntimeError(
            "Unable to connect to MySQL.\n\n"
            f"Database: {config['database']}\n"
            f"Host: {config['host']}\n"
            f"User: {config['user']}\n\n"
            f"Error: {error}"
        ) from error


# ============================================================
# TEST DATABASE CONNECTION
# ============================================================

def test_database_connection():
    """
    Check MySQL connectivity and required tables.
    """

    connection = None
    cursor = None

    try:
        connection = get_db_connection()

        cursor = connection.cursor()

        cursor.execute("SHOW TABLES")

        tables = {
            row[0]
            for row in cursor.fetchall()
        }

        required_tables = {
            "bills",
            "bill_items"
        }

        missing_tables = (
            required_tables - tables
        )

        if missing_tables:
            raise RuntimeError(
                "Missing database tables: "
                + ", ".join(
                    sorted(missing_tables)
                )
            )

        print(
            "MySQL database connected successfully."
        )

        print(
            "Database: meqeta_billing"
        )

        return True

    except (Error, RuntimeError) as error:
        print(
            "Database connection failed:",
            error
        )

        return False

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


# ============================================================
# GET BILL NUMBER FROM KOT
# ============================================================

def find_bill_number_by_kot(kot_no):
    """
    Search MySQL using the entered KOT number.

    Returns a matching bill number.

    Raises an error when multiple bills share
    the same KOT number, avoiding selection
    of an unintended bill.
    """

    connection = None
    cursor = None

    try:
        connection = get_db_connection()

        cursor = connection.cursor(
            dictionary=True
        )

        cursor.execute(
            """
            SELECT id, bill_no, kot_no
            FROM bills
            WHERE kot_no = %s
            ORDER BY id DESC
            """,
            (str(kot_no).strip(),)
        )

        results = cursor.fetchall()

        if not results:
            return None

        if len(results) > 1:
            numbers = ", ".join(
                str(row["bill_no"])
                for row in results[:10]
            )

            raise ValueError(
                f"Multiple bills found for KOT {kot_no}.\n"
                f"Bill numbers: {numbers}\n\n"
                "Please identify the correct bill."
            )

        return str(
            results[0]["bill_no"]
        )

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


# ============================================================
# GET BILL DETAILS USING KOT
# ============================================================

def fetch_bill_by_kot(kot_no):
    """
    Read the latest committed MySQL bill data
    using the entered KOT number.

    Returns a dictionary containing the bill
    and its associated item rows.
    """

    bill_no = find_bill_number_by_kot(
        kot_no
    )

    if bill_no is None:
        return None

    connection = None
    cursor = None

    try:
        connection = get_db_connection()

        cursor = connection.cursor(
            dictionary=True
        )

        cursor.execute(
            """
            SELECT *
            FROM bills
            WHERE bill_no = %s
            LIMIT 1
            """,
            (bill_no,)
        )

        bill = cursor.fetchone()

        if bill is None:
            return None

        cursor.execute(
            """
            SELECT *
            FROM bill_items
            WHERE bill_id = %s
            ORDER BY id
            """,
            (bill["id"],)
        )

        items = cursor.fetchall()

        return {
            "bill": bill,
            "items": items
        }

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


# ============================================================
# DECIMAL NUMBER HELPER
# ============================================================

def num(value):
    """
    Safely convert a value into Decimal.
    """

    try:
        result = Decimal(
            str(value).strip() or "0"
        )

        if not result.is_finite():
            return Decimal("0")

        if result < 0:
            return Decimal("0")

        return result

    except (
        InvalidOperation,
        ValueError,
        TypeError
    ):
        return Decimal("0")


# ============================================================
# MONEY FORMATTING
# ============================================================

def money(value):
    return str(
        Decimal(value).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )
    )


# ============================================================
# DATE NORMALIZATION
# ============================================================

def normalize_date(value):
    """
    Convert dates into DD/MM/YYYY format.
    """

    if value is None:
        return ""

    if isinstance(value, (date, datetime)):
        return value.strftime(
            "%d/%m/%Y"
        )

    value = str(value).strip()

    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d"
    ):
        try:
            return datetime.strptime(
                value,
                fmt
            ).strftime(
                "%d/%m/%Y"
            )

        except ValueError:
            continue

    raise ValueError(
        f"Invalid date: {value}"
    )


# ============================================================
# SQL DATE CONVERSION
# ============================================================

def optional_sql_date(value):
    value = str(value).strip()

    if not value:
        return None

    normalized = normalize_date(
        value
    )

    return datetime.strptime(
        normalized,
        "%d/%m/%Y"
    ).date()


def sql_date_to_text(value):
    if value is None:
        return ""

    return normalize_date(value)


# ============================================================
# GENERATE DATE DROPDOWN OPTIONS
# ============================================================

def generate_date_options():
    today = datetime.now()

    dates = []

    for offset in (-1, 0, 1):
        month_index = (
            today.year * 12
            + today.month - 1
            + offset
        )

        year, zero_month = divmod(
            month_index,
            12
        )

        month = zero_month + 1

        days = calendar.monthrange(
            year,
            month
        )[1]

        for day_number in range(
            1,
            days + 1
        ):
            dates.append(
                f"{day_number:02d}/"
                f"{month:02d}/"
                f"{year}"
            )

    return dates


# ============================================================
# ACTIVE WINDOW DETECTION
# ============================================================

def active_window_handle():
    if sys.platform != "win32":
        return None

    try:
        return (
            ctypes.windll.user32
            .GetForegroundWindow()
        )

    except (AttributeError, OSError):
        return None


def root_window_handle(hwnd):
    if not hwnd:
        return None

    if sys.platform != "win32":
        return None

    try:
        return (
            ctypes.windll.user32
            .GetAncestor(hwnd, 2)
        )

    except (AttributeError, OSError):
        return None


# ============================================================
# READ EXISTING ink.txt
# ============================================================

def read_ink():
    """
    Read legacy PMS values from ink.txt.

    MySQL KOT lookup is the primary data source
    for the updated Fill to PMS workflow.
    """

    if not INK_FILE.exists():
        return {}

    content = INK_FILE.read_text(
        encoding="utf-8-sig"
    ).strip()

    if not content:
        return {}

    aliases = {
        "bill": "bill",
        "billno": "bill",
        "billnumber": "bill",
        "date": "date",
        "waiter": "waiter",
        "service": "service",
        "servicetype": "service",
        "kot": "kot",
        "kotno": "kot"
    }

    data = {}

    for line in content.splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith((
            "#",
            ";",
            "//"
        )):
            continue

        match = re.match(
            r"^([^=:]+?)\s*[:=]\s*(.*)$",
            line
        )

        if not match:
            continue

        key = re.sub(
            r"[^a-z0-9]",
            "",
            match.group(1).lower()
        )

        value = match.group(2).strip()

        mapped = aliases.get(key)

        if mapped:
            data[mapped] = value

    return data


# ============================================================
# END OF PART 1
# ============================================================

# PART 2 WILL START WITH:
#
# class BillingApp:
#
# Including:
# - Application initialization
# - Tkinter billing interface
# - KOT popup
# - Visible character-by-character typing and opened dropdown selection
# - Dropdown selection with one-second delay
#
# ============================================================

# ============================================================
# PART 2 - MAIN BILLING APPLICATION
# ============================================================

class BillingApp:

    # ========================================================
    # APPLICATION INITIALIZATION
    # ========================================================

    def __init__(self, root):
        self.root = root

        self.root.title(
            "MEQETA - Food & Beverages Billing Entry"
        )

        self.root.geometry("1180x780")
        self.root.minsize(1050, 680)

        self.bg = "#e8e8e8"
        self.blue = "#253e70"

        self.root.configure(bg=self.bg)

        # ----------------------------------------------------
        # BILLING FORM VARIABLES
        # ----------------------------------------------------

        field_names = [
            "bill",
            "date",
            "time",
            "waiter",
            "service",
            "room",
            "reg",
            "kot",
            "guest",
            "company",
            "arrival",
            "arrival_time",
            "departure",
            "departure_time",
            "stay",
            "detail"
        ]

        self.v = {
            key: tk.StringVar()
            for key in field_names
        }

        self.mode = tk.StringVar(value="Current")
        self.paid = tk.BooleanVar(value=False)

        # ----------------------------------------------------
        # BILL CHARGES
        # ----------------------------------------------------

        self.charges = {
            key: tk.StringVar(value="0")
            for key in (
                "discount",
                "service_charge",
                "cgst",
                "sgst",
                "cess"
            )
        }

        self.totals = {
            key: tk.StringVar(value="0.00")
            for key in (
                "qty",
                "total",
                "cgst_amount",
                "sgst_amount",
                "cess_amount",
                "round_off",
                "net"
            )
        }

        # ----------------------------------------------------
        # WIDGET REFERENCES
        # ----------------------------------------------------

        self.fields = {}
        self.rows = []
        self.cells = []
        self.row_frames = []
        self.action_buttons = []

        self.table_keys = (
            "code",
            "item",
            "qty",
            "rate"
        )

        self.status = tk.StringVar(value="Ready")

        # ----------------------------------------------------
        # AUTOMATION STATE
        # ----------------------------------------------------

        self.automation_running = False
        self.automation_job = None
        self.automation_token = 0
        self.automation_index = 0
        self.automation_data = {}

        self.external_cancel = threading.Event()
        self.external_thread = None
        self.external_results = queue.Queue()

        # ----------------------------------------------------
        # KOT FILLING STATE
        # ----------------------------------------------------

        self.kot_loaded_data = None
        self.kot_fill_order = list(KOT_FILL_ORDER)
        self.kot_fill_index = 0
        self.kot_fill_job = None

        self.closing = False
        self.extra_data = {}

        # ----------------------------------------------------
        # CREATE INTERFACE
        # ----------------------------------------------------

        self.build_ui()

        self.bind_navigation()

        for variable in self.charges.values():
            variable.trace_add(
                "write",
                lambda *_: self.calculate()
            )

        self.calculate()
        self.clock()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close_app
        )

        self.root.after(
            300,
            self.check_database_on_start
        )

    # ========================================================
    # DATABASE CONNECTION STATUS
    # ========================================================

    def check_database_on_start(self):
        if test_database_connection():
            self.status.set(
                "MySQL connected - meqeta_billing"
            )
        else:
            self.status.set(
                "MySQL connection unavailable"
            )

    # ========================================================
    # CREATE LABEL
    # ========================================================

    def label(
        self,
        parent,
        text,
        x,
        y,
        width=90
    ):
        widget = tk.Label(
            parent,
            text=text,
            bg=self.bg,
            fg=self.blue,
            anchor="e",
            font=("Arial", 9, "bold")
        )

        widget.place(
            x=x,
            y=y,
            width=width,
            height=24
        )

        return widget

    # ========================================================
    # CREATE ENTRY
    # ========================================================

    def entry(
        self,
        parent,
        key,
        x,
        y,
        width
    ):
        widget = tk.Entry(
            parent,
            textvariable=self.v[key],
            font=("Arial", 10)
        )

        widget.place(
            x=x,
            y=y,
            width=width,
            height=24
        )

        self.fields[key] = widget

        return widget

    # ========================================================
    # CREATE DROPDOWN
    # ========================================================

    def combo(
        self,
        parent,
        key,
        values,
        x,
        y,
        width,
        readonly=False
    ):
        widget = ttk.Combobox(
            parent,
            textvariable=self.v[key],
            values=values,
            state=(
                "readonly"
                if readonly
                else "normal"
            ),
            font=("Arial", 10)
        )

        widget.place(
            x=x,
            y=y,
            width=width,
            height=24
        )

        self.fields[key] = widget

        return widget

    # ========================================================
    # BUILD BILLING INTERFACE
    # ========================================================

    def build_ui(self):
        top = tk.Frame(
            self.root,
            bg=self.bg,
            height=215
        )

        top.pack(
            side="top",
            fill="x",
            padx=12,
            pady=(8, 0)
        )

        top.pack_propagate(False)

        # ----------------------------------------------------
        # FILL TO PMS BUTTON
        # ----------------------------------------------------

        self.pms_button = tk.Button(
            top,
            text="Fill to PMS",
            command=self.fill_to_pms,
            bg="#a9d2f0",
            fg="#17365d",
            font=("Arial", 10, "bold"),
            cursor="hand2"
        )

        self.pms_button.place(
            relx=1,
            x=-160,
            y=8,
            width=150,
            height=35
        )

        # ----------------------------------------------------
        # STATUS LABEL
        # ----------------------------------------------------

        tk.Label(
            top,
            textvariable=self.status,
            bg=self.bg,
            fg="#247044",
            anchor="e",
            font=("Arial", 9)
        ).place(
            relx=1,
            x=-390,
            y=48,
            width=380,
            height=22
        )

        # ----------------------------------------------------
        # BILL NUMBER
        # ----------------------------------------------------

        self.label(
            top, "Bill No :", 0, 10
        )

        self.entry(
            top, "bill", 95, 10, 110
        )

        tk.Button(
            top,
            text="Adding",
            command=self.summary,
            bg="#e9b5bb"
        ).place(
            x=215,
            y=8,
            width=85,
            height=29
        )

        tk.Radiobutton(
            top,
            text="Current",
            variable=self.mode,
            value="Current",
            bg=self.bg
        ).place(
            x=310,
            y=10
        )

        tk.Radiobutton(
            top,
            text="Previous",
            variable=self.mode,
            value="Previous",
            bg=self.bg
        ).place(
            x=390,
            y=10
        )

        # ----------------------------------------------------
        # DATE
        # ----------------------------------------------------

        self.label(
            top, "Date :", 0, 47
        )

        self.combo(
            top,
            "date",
            generate_date_options(),
            95,
            47,
            135,
            readonly=True
        )

        tk.Label(
            top,
            textvariable=self.v["time"],
            bg="white",
            relief="sunken"
        ).place(
            x=240,
            y=47,
            width=100,
            height=24
        )

        # ----------------------------------------------------
        # WAITER
        # ----------------------------------------------------

        self.label(
            top, "Waiter :", 0, 84
        )

        self.combo(
            top,
            "waiter",
            [
                "AKASH",
                "RAHUL",
                "AMIT",
                "JOHN"
            ],
            95,
            84,
            195,
            readonly=True
        )

        # ----------------------------------------------------
        # ROOM NUMBER
        # ----------------------------------------------------

        self.label(
            top, "Room No :", 300, 84, 90
        )

        self.combo(
            top,
            "room",
            [
                "101",
                "102",
                "201",
                "202"
            ],
            395,
            84,
            100
        )

        # ----------------------------------------------------
        # SERVICE TYPE
        # ----------------------------------------------------

        self.label(
            top, "Ser. Type :", 0, 121
        )

        self.combo(
            top,
            "service",
            [
                "FOOD & BEVERAGES",
                "ROOM SERVICE",
                "RESTAURANT",
                "TAKE AWAY"
            ],
            95,
            121,
            195,
            readonly=True
        )

        # ----------------------------------------------------
        # REGISTRATION NUMBER
        # ----------------------------------------------------

        self.label(
            top, "Reg No :", 300, 121, 90
        )

        self.entry(
            top, "reg", 395, 121, 100
        )

        # ----------------------------------------------------
        # KOT NUMBER
        # ----------------------------------------------------

        self.label(
            top, "KOT No :", 0, 158
        )

        self.entry(
            top, "kot", 95, 158, 125
        )

        # ----------------------------------------------------
        # GUEST DETAILS
        # ----------------------------------------------------

        ttk.Separator(
            top,
            orient="vertical"
        ).place(
            x=510,
            y=5,
            height=195
        )

        self.label(
            top, "Guest :", 520, 10
        )

        self.entry(
            top, "guest", 615, 10, 350
        )

        self.label(
            top, "Company :", 520, 47
        )

        self.entry(
            top, "company", 615, 47, 350
        )

        # ----------------------------------------------------
        # ARRIVAL DATE / TIME
        # ----------------------------------------------------

        self.label(
            top, "Arr. Date :", 520, 84
        )

        self.entry(
            top, "arrival", 615, 84, 125
        )

        self.entry(
            top,
            "arrival_time",
            748,
            84,
            85
        )

        # ----------------------------------------------------
        # DEPARTURE DATE / TIME
        # ----------------------------------------------------

        self.label(
            top, "Dep. Date :", 520, 121
        )

        self.entry(
            top,
            "departure",
            615,
            121,
            125
        )

        self.entry(
            top,
            "departure_time",
            748,
            121,
            85
        )

        # ----------------------------------------------------
        # STAY PLAN
        # ----------------------------------------------------

        self.label(
            top, "Stay Plan :", 520, 158
        )

        self.combo(
            top,
            "stay",
            [
                "MAP",
                "AP",
                "CP",
                "EP"
            ],
            615,
            158,
            150
        )

        # ----------------------------------------------------
        # BOTTOM SECTION
        # ----------------------------------------------------

        bottom = tk.Frame(
            self.root,
            bg=self.bg,
            height=215
        )

        bottom.pack(
            side="bottom",
            fill="x",
            padx=12,
            pady=6
        )

        bottom.pack_propagate(False)

        self.create_bottom(bottom)
        self.create_table()

    # ========================================================
    # FILL TO PMS - ASK FOR KOT NUMBER
    # ========================================================

    def fill_to_pms(self):
        """Arm one-click filling using a saved bill found by KOT."""
        if self.automation_running:
            self.stop_click_filling()
            return
        kot = simpledialog.askstring(
            "MEQETA - Fill to PMS", "Enter KOT Number:", parent=self.root
        )
        if kot is None:
            return
        kot = kot.strip()
        if not kot:
            messagebox.showwarning("KOT Required", "Enter a KOT number.")
            return
        try:
            result = fetch_bill_by_kot(kot)
            if result is None:
                messagebox.showwarning("Not Found", f"No bill found for KOT {kot}.")
                return
            data = self.convert_mysql_bill(result)
        except (Error, RuntimeError, ValueError, KeyError) as exc:
            messagebox.showerror("Database Error", str(exc))
            return

        self.click_fill_data = {
            key: str(data.get(key) or "")
            for key in ("bill", "date", "waiter", "service")
        }
        self.click_fill_data["kot"] = kot
        missing = [key for key, val in self.click_fill_data.items() if not val.strip()]
        if missing:
            messagebox.showwarning("Missing Data", ", ".join(missing))
            return

        self.click_fill_events = queue.Queue()
        self.click_fill_stop = threading.Event()
        self.click_fill_busy = False
        self.click_fill_listener = None
        self.automation_running = True
        self.set_controls(False)
        self.pms_button.configure(
            state="normal", text="Stop Filling",
            command=self.stop_click_filling, bg="#f1b4b4"
        )
        # Bind once. Any one of these fields triggers the full assigned fill.
        if not getattr(self, "click_fill_bound", False):
            for key in ("bill", "date", "waiter", "service", "kot"):
                widget = self.fields.get(key)
                if widget is not None:
                    widget.bind(
                        "<Button-1>",
                        lambda event: self.fill_all_meqeta_fields(),
                        add="+"
                    )
            self.click_fill_bound = True
        try:
            self.click_fill_listener = mouse.Listener(on_click=self._global_click)
            self.click_fill_listener.start()
        except Exception as exc:
            self.stop_click_filling()
            messagebox.showerror("Mouse Listener", str(exc))
            return
        self.status.set(
            "Ready: click once in MEQETA, Notepad, or a labeled PMS field."
        )
        self.root.after(100, self._poll_click_events)

    def fill_all_meqeta_fields(self):
        """One click visibly types all five values in their assigned MEQETA fields.

        Ordinary entries are updated character by character using Tk's event loop.
        Read-only comboboxes cannot accept keystrokes, so their matching option is
        selected at the corresponding step instead of being pasted.
        """
        if not self.automation_running or self.click_fill_busy:
            return
        self.click_fill_busy = True
        self._type_meqeta_next_field(0)

    def _type_meqeta_next_field(self, index):
        if not self.automation_running or self.click_fill_stop.is_set():
            return
        keys = ("bill", "date", "waiter", "service", "kot")
        if index >= len(keys):
            self.stop_click_filling(completed=True)
            return
        key = keys[index]
        value = self.click_fill_data[key]
        widget = self.fields.get(key)
        if widget is None:
            self._type_meqeta_next_field(index + 1)
            return
        self.status.set(f"Typing {key.upper()}...")
        if isinstance(widget, ttk.Combobox):
            # Show the REAL ttk dropdown before selecting the saved option.
            # This is not an instant StringVar assignment: the user can see
            # the choices open, the matching choice selected, and the list close.
            choices = list(widget.cget("values"))
            match = next(
                (i for i, option in enumerate(choices)
                 if str(option).strip().casefold() == value.strip().casefold()),
                None
            )
            if match is None:
                choices.append(value)
                widget.configure(values=choices)
                match = len(choices) - 1

            widget.focus_set()
            try:
                widget.tk.call("ttk::combobox::Post", str(widget))
            except tk.TclError:
                # Fallback for platforms/Tk themes without the posting command.
                widget.event_generate("<Alt-Down>")

            self.status.set(f"Showing {key.upper()} dropdown...")
            self.root.after(
                650,
                lambda w=widget, i=match, k=key, n=index:
                    self._select_meqeta_dropdown(w, i, k, n)
            )
            return
        # StringVar changes simulate visible character-by-character typing in
        # this application's own Tkinter entry; they do not paste whole values.
        self.v[key].set("")
        self._type_meqeta_character(key, value, 0, index)

    def _select_meqeta_dropdown(self, widget, option_index, key, field_index):
        """Select the database value while the dropdown is visibly displayed."""
        if not self.automation_running or self.click_fill_stop.is_set():
            try:
                widget.tk.call("ttk::combobox::Unpost", str(widget))
            except tk.TclError:
                pass
            return
        try:
            widget.current(option_index)
            widget.event_generate("<<ComboboxSelected>>")
        finally:
            try:
                widget.tk.call("ttk::combobox::Unpost", str(widget))
            except tk.TclError:
                widget.event_generate("<Escape>")
        self.status.set(f"Selected {key.upper()}: {self.click_fill_data[key]}")
        self.root.after(250, lambda: self._type_meqeta_next_field(field_index + 1))

    def _type_meqeta_character(self, key, value, position, field_index):
        if not self.automation_running or self.click_fill_stop.is_set():
            return
        if position >= len(value):
            self.root.after(150, lambda: self._type_meqeta_next_field(field_index + 1))
            return
        self.v[key].set(value[:position + 1])
        self.root.after(75, lambda: self._type_meqeta_character(
            key, value, position + 1, field_index))

    def _global_click(self, x, y, button, pressed):
        """Mouse listener thread: enqueue only; do not access Tkinter."""
        if (not pressed or button != mouse.Button.left
                or self.click_fill_stop.is_set()):
            return
        self.click_fill_events.put(("click", int(x), int(y)))

    def _clicked_window_title(self, x, y):
        """Identify the clicked Windows top-level window."""
        if sys.platform != "win32":
            return ""
        from ctypes import wintypes
        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.GetAncestor(
            user32.WindowFromPoint(POINT(x, y)), 2
        )
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def _identify_external_control(self, x, y):
        """Inspect a Windows UI Automation control, never guess by click order.

        Returns (field_key, control_type), or (None, type) when ambiguous.
        Requires pywinauto for external labeled controls.
        """
        try:
            from pywinauto import Desktop
            control = Desktop(backend="uia").from_point(x, y)
            info = control.element_info
            kind = str(info.control_type or "").casefold()
            texts = [str(info.name or ""), str(info.automation_id or "")]
            # A control can be unlabeled while its immediate parent has a name.
            # Avoid using whole-window names as field identifiers.
            try:
                parent = control.parent()
                if parent is not None:
                    texts.append(str(parent.element_info.name or ""))
            except Exception:
                pass
            label = " ".join(texts).casefold()
            matches = []
            patterns = {
                "bill": r"\bbill\s*(?:no|number|#)\b",
                "date": r"\b(?:bill\s*)?date\b",
                "waiter": r"\bwaiter\b",
                "service": r"\b(?:service\s*type|ser\.?\s*type)\b",
                "kot": r"\bkot\s*(?:no|number|#)?\b",
            }
            for key, pattern in patterns.items():
                if re.search(pattern, label):
                    matches.append(key)
            return (matches[0] if len(matches) == 1 else None), kind
        except Exception:
            return None, ""

    def _formatted_bill_text(self):
        d = self.click_fill_data
        return (
            f"Bill No: {d['bill']}\n"
            f"Date: {d['date']}\n"
            f"Waiter: {d['waiter']}\n"
            f"Service Type: {d['service']}\n"
            f"KOT No: {d['kot']}"
        )

    def _poll_click_events(self):
        if self.closing or not self.automation_running:
            return
        try:
            while True:
                event = self.click_fill_events.get_nowait()
                if event[0] == "done":
                    self.click_fill_busy = False
                    self.stop_click_filling(completed=True)
                    return
                if event[0] == "error":
                    self.click_fill_busy = False
                    self.status.set(f"Typing failed: {event[1]}")
                    continue
                if self.click_fill_busy or event[0] != "click":
                    continue
                _, x, y = event
                title = self._clicked_window_title(x, y).casefold()
                if not title or "meqeta - food" in title:
                    continue

                key, kind = self._identify_external_control(x, y)
                if "notepad" in title:
                    # Only type into an editor/document, not menus or title bar.
                    if kind not in ("edit", "document"):
                        self.status.set("Click inside Notepad's text editing area.")
                        continue
                    value = self._formatted_bill_text()
                    replace = False
                elif key is not None and kind in ("edit", "combobox"):
                    # Exact external field label recognized; fill only that field.
                    value = self.click_fill_data[key]
                    replace = kind == "edit"
                else:
                    self.status.set(
                        "External field not identified. Click a labeled text field "
                        "or use Notepad; no data was typed."
                    )
                    continue
                self.click_fill_busy = True
                self._start_click_typing(value, replace, dropdown=(kind == "combobox"))
                break
        except queue.Empty:
            pass
        except Exception as exc:
            self.status.set(f"Click detection error: {exc}")
        if self.automation_running:
            self.root.after(100, self._poll_click_events)

    def _start_click_typing(self, value, replace, dropdown=False):
        """Type into the clicked external editor using actual keyboard events.

        Never use clipboard paste. This works for ordinary editable controls;
        custom or read-only PMS dropdowns may require app-specific automation.
        """
        def worker():
            try:
                time.sleep(0.35)  # Let the user's click focus the target.
                if self.click_fill_stop.is_set():
                    return
                if dropdown:
                    # Open the external PMS dropdown visibly. Standard Windows
                    # comboboxes support Alt+Down, text search, and Enter.
                    # Custom dropdowns may require a PMS-specific adapter.
                    pyautogui.hotkey("alt", "down")
                    time.sleep(0.65)
                    if self.click_fill_stop.is_set():
                        return
                    pyautogui.write(str(value), interval=0.075)
                    time.sleep(0.3)
                    pyautogui.press("enter")
                    self.click_fill_events.put(("done",))
                    return
                if replace:
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.1)
                for line_number, line in enumerate(str(value).split("\n")):
                    if self.click_fill_stop.is_set():
                        return
                    if line_number:
                        pyautogui.press("enter")
                        time.sleep(0.08)
                    for character in line:
                        if self.click_fill_stop.is_set():
                            return
                        pyautogui.write(character)
                        time.sleep(0.075)
                self.click_fill_events.put(("done",))
            except Exception as exc:
                self.click_fill_events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def stop_click_filling(self, completed=False):
        if hasattr(self, "click_fill_stop"):
            self.click_fill_stop.set()
        listener = getattr(self, "click_fill_listener", None)
        if listener is not None:
            listener.stop()
            self.click_fill_listener = None
        self.automation_running = False
        self.click_fill_busy = False
        if not self.closing:
            self.set_controls(True)
            self.pms_button.configure(
                text="Fill to PMS", state="normal",
                command=self.fill_to_pms, bg="#a9d2f0"
            )
            self.status.set(
                "PMS filling completed." if completed else "PMS filling stopped."
            )

    # ========================================================
    # CONVERT MYSQL BILL TO FORM DATA
    # ========================================================

    def convert_mysql_bill(self, result):
        bill = result["bill"]
        items = result["items"]

        def value(key, default=""):
            raw = bill.get(key)

            if raw is None:
                return default

            return str(raw)

        data = {
            "bill": value("bill_no"),
            "date": sql_date_to_text(
                bill.get("bill_date")
            ),
            "waiter": value("waiter"),
            "service": value("service_type"),
            "kot": value("kot_no"),
            "room": value("room_no"),
            "reg": value("reg_no"),
            "guest": value("guest_name"),
            "company": value("company"),
            "arrival": sql_date_to_text(
                bill.get("arrival_date")
            ),
            "arrival_time": value("arrival_time"),
            "departure": sql_date_to_text(
                bill.get("departure_date")
            ),
            "departure_time": value(
                "departure_time"
            ),
            "stay": value("stay_plan"),
            "detail": value("payment_method"),
            "paid": bool(bill.get("paid", False)),
            "mode": "Current",

            "charges": {
                "discount": value("discount", "0"),
                "service_charge": value(
                    "service_charge", "0"
                ),
                "cgst": value("cgst_rate", "0"),
                "sgst": value("sgst_rate", "0"),
                "cess": value("cess_rate", "0")
            },

            "items": []
        }

        for item in items:
            data["items"].append({
                "code": str(
                    item.get("item_code") or ""
                ),
                "item": str(
                    item.get("item_name") or ""
                ),
                "qty": str(
                    item.get("quantity") or ""
                ),
                "rate": str(
                    item.get("rate") or ""
                ),
                "amount": str(
                    item.get("amount") or ""
                )
            })

        return data

    # ========================================================
    # FILL NEXT FIELD
    # ========================================================

    def kot_fill_next(self, token):
        if token != self.automation_token:
            return

        if not self.automation_running:
            return

        self.kot_fill_job = None

        if self.kot_fill_index >= len(
            self.kot_fill_order
        ):
            self.kot_fill_complete(token)
            return

        key = self.kot_fill_order[
            self.kot_fill_index
        ]

        value = str(
            self.kot_loaded_data.get(key) or ""
        )

        widget = self.fields[key]
        widget.focus_set()

        # ----------------------------------------------------
        # DROPDOWN FIELD
        # ----------------------------------------------------

        if isinstance(widget, ttk.Combobox):
            self.kot_select_dropdown(
                key,
                value,
                token
            )
            return

        # ----------------------------------------------------
        # NORMAL TEXT FIELD
        # ----------------------------------------------------

        self.v[key].set(value)

        try:
            widget.icursor("end")
        except tk.TclError:
            pass

        self.status.set(
            f"Filling {key.upper()}: {value}"
        )

        self.kot_fill_index += 1

        self.kot_fill_job = self.root.after(
            KOT_FIELD_DELAY_MS,
            lambda: self.kot_fill_next(token)
        )

    # ========================================================
    # VISIBLE DROPDOWN SELECTION
    # ========================================================

    def kot_select_dropdown(
        self,
        key,
        value,
        token
    ):
        widget = self.fields[key]

        options = list(
            widget["values"]
        )

        match_index = next(
            (
                index
                for index, option in enumerate(options)
                if str(option).strip().casefold()
                == value.strip().casefold()
            ),
            None
        )

        # Add database value when not in dropdown.
        if value and match_index is None:
            options.append(value)
            widget["values"] = options
            match_index = len(options) - 1

        self.status.set(
            f"Selecting {key.upper()}: {value}"
        )

        # Open dropdown visibly.
        if match_index is not None:
            try:
                widget.event_generate("<Alt-Down>")
            except tk.TclError:
                pass

        # ----------------------------------------------------
        # SELECT AFTER ONE SECOND
        # ----------------------------------------------------

        def select_value():
            if token != self.automation_token:
                return

            if not self.automation_running:
                return

            self.kot_fill_job = None

            if match_index is not None:
                widget.current(match_index)
            else:
                self.v[key].set("")

            try:
                widget.event_generate("<Escape>")
            except tk.TclError:
                pass

            self.kot_fill_index += 1

            self.kot_fill_job = self.root.after(
                KOT_FIELD_DELAY_MS,
                lambda: self.kot_fill_next(token)
            )

        self.kot_fill_job = self.root.after(
            KOT_DROPDOWN_HOLD_MS,
            select_value
        )

    # ========================================================
    # COMPLETE KOT FIELD FILLING
    # ========================================================

    def kot_fill_complete(self, token):
        if token != self.automation_token:
            return

        if not self.automation_running:
            return

        data = self.kot_loaded_data

        # ----------------------------------------------------
        # RESTORE BILL MODE AND PAYMENT STATUS
        # ----------------------------------------------------

        self.mode.set(
            data.get("mode", "Current")
        )

        self.paid.set(
            bool(data.get("paid", False))
        )

        # ----------------------------------------------------
        # RESTORE CHARGES
        # ----------------------------------------------------

        for key, value in data.get(
            "charges", {}
        ).items():
            if key in self.charges:
                self.charges[key].set(
                    str(value)
                )

        # ----------------------------------------------------
        # RESTORE BILL ITEMS
        # ----------------------------------------------------

        items = data.get("items", [])

        while len(self.rows) < len(items):
            self.add_row()

        for index, item in enumerate(items):
            for key in self.table_keys:
                self.rows[index][key].set(
                    str(
                        item.get(key) or ""
                    )
                )

        # ----------------------------------------------------
        # RECALCULATE BILL
        # ----------------------------------------------------

        self.calculate()

        self.automation_running = False
        self.set_controls(True)

        self.status.set(
            f"KOT {data['kot']} loaded successfully. "
            f"Bill {data['bill']} ready."
        )

        self.kot_loaded_data = None

        if "bill" in self.fields:
            self.fields["bill"].focus_set()

    # ========================================================
    # END OF PART 2
    # ========================================================

    # ========================================================
    # CREATE BILLING TABLE
    # ========================================================

    def create_table(self):
        holder = tk.Frame(
            self.root,
            relief="sunken",
            bd=1
        )

        holder.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=4
        )

        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            holder,
            bg="white",
            highlightthickness=0
        )

        self.canvas.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        scrollbar_y = ttk.Scrollbar(
            holder,
            orient="vertical",
            command=self.canvas.yview
        )

        scrollbar_y.grid(
            row=0,
            column=1,
            sticky="ns"
        )

        scrollbar_x = ttk.Scrollbar(
            holder,
            orient="horizontal",
            command=self.canvas.xview
        )

        scrollbar_x.grid(
            row=1,
            column=0,
            sticky="ew"
        )

        self.canvas.configure(
            yscrollcommand=scrollbar_y.set,
            xscrollcommand=scrollbar_x.set
        )

        self.inner = tk.Frame(
            self.canvas,
            bg="white"
        )

        self.canvas.create_window(
            (0, 0),
            window=self.inner,
            anchor="nw"
        )

        self.inner.bind(
            "<Configure>",
            lambda event: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            )
        )

        self.columns = [
            ("Sr", 45),
            ("Code", 120),
            ("Item", 430),
            ("Qty", 95),
            ("Rate", 130),
            ("Amount", 150)
        ]

        for column, (name, width) in enumerate(
            self.columns
        ):
            frame = tk.Frame(
                self.inner,
                width=width,
                height=30,
                bg="#d9e1e8",
                relief="solid",
                bd=1
            )

            frame.grid(
                row=0,
                column=column
            )

            frame.grid_propagate(False)

            tk.Label(
                frame,
                text=name,
                bg="#d9e1e8",
                font=("Arial", 10, "bold")
            ).pack(
                fill="both",
                expand=True
            )

        # Create 20 editable rows initially.
        for _ in range(20):
            self.add_row()

        self.canvas.bind(
            "<MouseWheel>",
            lambda event: self.canvas.yview_scroll(
                -1 if event.delta > 0 else 1,
                "units"
            )
        )

    # ========================================================
    # ADD BILLING TABLE ROW
    # ========================================================

    def add_row(self):
        index = len(self.rows)

        row = {
            key: tk.StringVar()
            for key in (
                "code",
                "item",
                "qty",
                "rate",
                "amount"
            )
        }

        self.rows.append(row)

        widgets = {}
        frames = []

        for column, (name, width) in enumerate(
            self.columns
        ):
            frame = tk.Frame(
                self.inner,
                width=width,
                height=27,
                bg="white",
                relief="solid",
                bd=1
            )

            frame.grid(
                row=index + 1,
                column=column
            )

            frame.grid_propagate(False)
            frames.append(frame)

            if column == 0:
                tk.Label(
                    frame,
                    text=str(index + 1),
                    bg="white"
                ).pack(
                    fill="both",
                    expand=True
                )
                continue

            key = name.lower()

            widget = tk.Entry(
                frame,
                textvariable=row[key],
                bd=0,
                justify=(
                    "right"
                    if key in (
                        "qty",
                        "rate",
                        "amount"
                    )
                    else "left"
                ),
                state=(
                    "readonly"
                    if key == "amount"
                    else "normal"
                )
            )

            widget.pack(
                fill="both",
                expand=True
            )

            widgets[key] = widget

            if key == "amount":
                continue

            widget.bind(
                "<Return>",
                lambda event, r=index, k=key:
                    self.next_cell(r, k)
            )

            widget.bind(
                "<Tab>",
                lambda event, r=index, k=key:
                    self.next_cell(r, k)
            )

            widget.bind(
                "<Shift-Tab>",
                lambda event, r=index, k=key:
                    self.previous_cell(r, k)
            )

            for event_name, dr, dc in (
                ("<Up>", -1, 0),
                ("<Down>", 1, 0),
                ("<Left>", 0, -1),
                ("<Right>", 0, 1)
            ):
                widget.bind(
                    event_name,
                    lambda event, r=index, k=key,
                    a=dr, b=dc:
                        self.move_cell(r, k, a, b)
                )

        self.cells.append(widgets)
        self.row_frames.append(frames)

        for key in ("qty", "rate"):
            row[key].trace_add(
                "write",
                lambda *_: self.calculate()
            )

    # ========================================================
    # FOCUS BILLING TABLE CELL
    # ========================================================

    def focus_cell(self, row, column):
        if row < 0:
            return

        while row >= len(self.rows):
            self.add_row()

        column = max(0, min(3, column))

        self.root.update_idletasks()

        frame = self.row_frames[row][column + 1]

        y = frame.winfo_y()
        x = frame.winfo_x()

        height = self.inner.winfo_reqheight()
        width = self.inner.winfo_reqwidth()

        view_height = self.canvas.winfo_height()
        view_width = self.canvas.winfo_width()

        if height > view_height:
            if y < self.canvas.canvasy(0):
                self.canvas.yview_moveto(
                    y / height
                )

            elif y + 27 > (
                self.canvas.canvasy(0)
                + view_height
            ):
                self.canvas.yview_moveto(
                    (y + 27 - view_height) / height
                )

        if width > view_width:
            if x < self.canvas.canvasx(0):
                self.canvas.xview_moveto(
                    x / width
                )

            elif (
                x + frame.winfo_width()
                > self.canvas.canvasx(0)
                + view_width
            ):
                self.canvas.xview_moveto(
                    (
                        x + frame.winfo_width()
                        - view_width
                    ) / width
                )

        widget = self.cells[row][
            self.table_keys[column]
        ]

        widget.focus_set()
        widget.select_range(0, "end")

    # ========================================================
    # NEXT CELL - ENTER / TAB
    # ========================================================

    def next_cell(self, row, key):
        column = self.table_keys.index(key)

        if column == 3:
            self.focus_cell(row + 1, 0)
        else:
            self.focus_cell(row, column + 1)

        return "break"

    # ========================================================
    # PREVIOUS CELL - SHIFT + TAB
    # ========================================================

    def previous_cell(self, row, key):
        column = self.table_keys.index(key)

        if column == 0 and row > 0:
            self.focus_cell(row - 1, 3)

        elif column > 0:
            self.focus_cell(row, column - 1)

        return "break"

    # ========================================================
    # ARROW KEY NAVIGATION
    # ========================================================

    def move_cell(self, row, key, dr, dc):
        column = self.table_keys.index(key)

        new_row = row + dr
        new_column = column + dc

        if new_column > 3:
            new_row += 1
            new_column = 0

        elif new_column < 0:
            new_row -= 1
            new_column = 3

        if new_row >= 0:
            self.focus_cell(
                new_row,
                new_column
            )

        return "break"

    # ========================================================
    # CREATE BOTTOM CONTROLS
    # ========================================================

    def create_bottom(self, parent):
        self.label(
            parent,
            "Detail :",
            0,
            8,
            75
        )

        self.combo(
            parent,
            "detail",
            [
                "Cash",
                "Card",
                "UPI",
                "Room Charge",
                "Credit",
                "Other"
            ],
            80,
            8,
            330
        )

        tk.Checkbutton(
            parent,
            text="Bill Paid",
            variable=self.paid,
            bg=self.bg
        ).place(
            x=80,
            y=45
        )

        buttons = [
            ("Save", self.save_bill),
            ("Delete", self.delete_bill),
            ("Clear", self.clear_form),
            ("Find", self.find_bill),
            ("Print", self.print_bill),
            ("Exit", self.close_app)
        ]

        for index, (name, command) in enumerate(
            buttons
        ):
            button = tk.Button(
                parent,
                text=name,
                command=command,
                bg="#dce3e9",
                font=("Arial", 9, "bold")
            )

            button.place(
                x=3 + index * 80,
                y=105,
                width=75,
                height=42
            )

            if name != "Exit":
                self.action_buttons.append(button)

        clone = tk.Button(
            parent,
            text="Clone Bill",
            command=self.clone_bill,
            bg="#d6e9d2",
            font=("Arial", 9, "bold")
        )

        clone.place(
            x=3,
            y=158,
            width=115,
            height=35
        )

        self.action_buttons.append(clone)

        # ----------------------------------------------------
        # CALCULATION PANEL
        # ----------------------------------------------------

        calculation = tk.LabelFrame(
            parent,
            text="Bill Calculation",
            bg=self.bg,
            fg=self.blue
        )

        calculation.place(
            relx=1,
            x=-460,
            y=0,
            width=460,
            height=205
        )

        fields = [
            ("QTY", "qty", "output"),
            ("Total", "total", "output"),
            ("Discount", "discount", "input"),
            (
                "Service Charge",
                "service_charge",
                "input"
            ),
            ("+ CGST (%)", "cgst", "tax"),
            ("+ SGST (%)", "sgst", "tax"),
            ("+ CESS (%)", "cess", "tax"),
            (
                "ROUND OFF",
                "round_off",
                "output"
            ),
            ("Net Amount", "net", "output")
        ]

        for index, (title, key, kind) in enumerate(
            fields
        ):
            y = 6 + index * 20

            tk.Label(
                calculation,
                text=title,
                bg=self.bg,
                fg=self.blue,
                anchor="w"
            ).place(
                x=10,
                y=y,
                width=140,
                height=19
            )

            if kind == "tax":
                tk.Entry(
                    calculation,
                    textvariable=self.charges[key],
                    justify="right"
                ).place(
                    x=155,
                    y=y,
                    width=90,
                    height=19
                )

                tk.Label(
                    calculation,
                    textvariable=self.totals[
                        key + "_amount"
                    ],
                    bg="white",
                    relief="sunken",
                    anchor="e"
                ).place(
                    x=260,
                    y=y,
                    width=175,
                    height=19
                )

            elif kind == "input":
                tk.Entry(
                    calculation,
                    textvariable=self.charges[key],
                    justify="right"
                ).place(
                    x=260,
                    y=y,
                    width=175,
                    height=19
                )

            else:
                tk.Label(
                    calculation,
                    textvariable=self.totals[key],
                    bg="white",
                    relief="sunken",
                    anchor="e"
                ).place(
                    x=260,
                    y=y,
                    width=175,
                    height=19
                )

    # ========================================================
    # CALCULATE BILL AMOUNTS
    # ========================================================

    def calculate(self):
        qty_total = Decimal("0")
        subtotal = Decimal("0")

        for row in self.rows:
            qty = num(
                row["qty"].get()
            )

            rate = num(
                row["rate"].get()
            )

            if row["qty"].get().strip():
                qty_total += qty

            if (
                row["qty"].get().strip()
                and row["rate"].get().strip()
            ):
                amount = qty * rate

                row["amount"].set(
                    money(amount)
                )

                subtotal += amount

            else:
                row["amount"].set("")

        discount = num(
            self.charges["discount"].get()
        )

        service = num(
            self.charges["service_charge"].get()
        )

        taxable = max(
            Decimal("0"),
            subtotal - discount + service
        )

        taxes = {}

        for key in (
            "cgst",
            "sgst",
            "cess"
        ):
            taxes[key] = (
                taxable
                * num(
                    self.charges[key].get()
                )
                / Decimal("100")
            ).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP
            )

            self.totals[
                key + "_amount"
            ].set(
                money(taxes[key])
            )

        gross = taxable + sum(
            taxes.values(),
            Decimal("0")
        )

        net = gross.quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )

        self.totals["qty"].set(
            str(qty_total)
        )

        self.totals["total"].set(
            money(subtotal)
        )

        self.totals["round_off"].set(
            money(net - gross)
        )

        self.totals["net"].set(
            money(net)
        )

    # ========================================================
    # ENABLE / DISABLE CONTROLS
    # ========================================================

    def set_controls(self, enabled):
        state = (
            "normal"
            if enabled
            else "disabled"
        )

        self.pms_button.config(
            state=state,
            text=(
                "Fill to PMS"
                if enabled
                else "Filling..."
            )
        )

        for button in self.action_buttons:
            button.config(
                state=state
            )

    # ========================================================
    # COLLECT CURRENT BILL DATA
    # ========================================================

    def collect_data(self):
        data = {
            key: variable.get()
            for key, variable in self.v.items()
        }

        data["mode"] = self.mode.get()
        data["paid"] = self.paid.get()

        data["charges"] = {
            key: variable.get()
            for key, variable in self.charges.items()
        }

        data["totals"] = {
            key: variable.get()
            for key, variable in self.totals.items()
        }

        data["items"] = []

        for row in self.rows:
            item = {
                key: variable.get()
                for key, variable in row.items()
            }

            if any(
                item[key].strip()
                for key in self.table_keys
            ):
                data["items"].append(
                    item
                )

        return data

    # ========================================================
    # FORM KEYBOARD NAVIGATION
    # ========================================================

    def bind_navigation(self):
        order = [
            "bill",
            "date",
            "waiter",
            "service",
            "kot",
            "room",
            "reg",
            "guest",
            "company",
            "arrival",
            "arrival_time",
            "departure",
            "departure_time",
            "stay",
            "detail"
        ]

        self.form_order = [
            self.fields[key]
            for key in order
        ]

        for widget in self.form_order:
            for event_name in (
                "<Return>",
                "<Tab>"
            ):
                widget.bind(
                    event_name,
                    lambda event:
                        self.move_form(event, 1)
                )

            widget.bind(
                "<Shift-Tab>",
                lambda event:
                    self.move_form(event, -1)
            )

            if not isinstance(
                widget,
                ttk.Combobox
            ):
                widget.bind(
                    "<Down>",
                    lambda event:
                        self.move_form(event, 1)
                )

                widget.bind(
                    "<Up>",
                    lambda event:
                        self.move_form(event, -1)
                )

    # ========================================================
    # MOVE BETWEEN FORM FIELDS
    # ========================================================

    def move_form(
        self,
        event,
        direction
    ):
        if self.automation_running:
            return "break"

        try:
            index = (
                self.form_order.index(
                    event.widget
                )
                + direction
            )

        except ValueError:
            return "break"

        if index >= len(self.form_order):
            self.focus_cell(0, 0)

        elif index >= 0:
            widget = self.form_order[index]

            widget.focus_set()

            try:
                widget.selection_range(
                    0,
                    "end"
                )

            except tk.TclError:
                pass

        return "break"

    # ========================================================
    # END OF PART 3
    # ========================================================

    # ========================================================
    # GET BILL FROM MYSQL USING BILL NUMBER
    # ========================================================

    def get_bill_from_database(self, bill_no):
        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT *
                FROM bills
                WHERE bill_no = %s
                LIMIT 1
                """,
                (str(bill_no).strip(),)
            )

            bill = cursor.fetchone()

            if bill is None:
                return None

            cursor.execute(
                """
                SELECT *
                FROM bill_items
                WHERE bill_id = %s
                ORDER BY id
                """,
                (bill["id"],)
            )

            items = cursor.fetchall()

            return self.convert_mysql_bill({
                "bill": bill,
                "items": items
            })

        finally:
            if cursor is not None:
                cursor.close()

            if connection is not None:
                connection.close()

    # ========================================================
    # CHECK WHETHER BILL ALREADY EXISTS
    # ========================================================

    def bill_exists(self, bill_no):
        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor()

            cursor.execute(
                """
                SELECT id
                FROM bills
                WHERE bill_no = %s
                LIMIT 1
                """,
                (str(bill_no).strip(),)
            )

            return cursor.fetchone() is not None

        finally:
            if cursor is not None:
                cursor.close()

            if connection is not None:
                connection.close()

    # ========================================================
    # SAVE / UPDATE BILL IN MYSQL
    # ========================================================

    def save_bill(self):
        if self.automation_running:
            return

        data = self.collect_data()

        bill_no = data["bill"].strip()

        if not bill_no:
            messagebox.showwarning(
                "Bill Number Required",
                "Please enter a Bill Number."
            )
            return

        if not data["kot"].strip():
            messagebox.showwarning(
                "KOT Required",
                "Please enter a KOT Number."
            )
            return

        if not data["date"].strip():
            messagebox.showwarning(
                "Date Required",
                "Please select a Bill Date."
            )
            return

        connection = None
        cursor = None

        try:
            bill_date = optional_sql_date(
                data["date"]
            )

            arrival_date = optional_sql_date(
                data["arrival"]
            )

            departure_date = optional_sql_date(
                data["departure"]
            )

            connection = get_db_connection()
            cursor = connection.cursor()

            # -----------------------------------------------
            # CHECK EXISTING BILL
            # -----------------------------------------------

            cursor.execute(
                """
                SELECT id
                FROM bills
                WHERE bill_no = %s
                LIMIT 1
                """,
                (bill_no,)
            )

            existing = cursor.fetchone()

            # -----------------------------------------------
            # PREPARE BILL VALUES
            # -----------------------------------------------

            columns = [
                "bill_no",
                "bill_date",
                "waiter",
                "service_type",
                "kot_no",
                "room_no",
                "reg_no",
                "guest_name",
                "company",
                "arrival_date",
                "arrival_time",
                "departure_date",
                "departure_time",
                "stay_plan",
                "payment_method",
                "paid",
                "discount",
                "service_charge",
                "cgst_rate",
                "sgst_rate",
                "cess_rate"
            ]

            values = [
                bill_no,
                bill_date,
                data["waiter"],
                data["service"],
                data["kot"],
                data["room"],
                data["reg"],
                data["guest"],
                data["company"],
                arrival_date,
                data["arrival_time"],
                departure_date,
                data["departure_time"],
                data["stay"],
                data["detail"],
                int(data["paid"]),
                str(num(
                    data["charges"]["discount"]
                )),
                str(num(
                    data["charges"]["service_charge"]
                )),
                str(num(
                    data["charges"]["cgst"]
                )),
                str(num(
                    data["charges"]["sgst"]
                )),
                str(num(
                    data["charges"]["cess"]
                ))
            ]

            # -----------------------------------------------
            # UPDATE EXISTING BILL
            # -----------------------------------------------

            if existing:
                bill_id = existing[0]

                if not messagebox.askyesno(
                    "Update Existing Bill",
                    f"Bill {bill_no} already exists.\n\n"
                    "Do you want to update it?"
                ):
                    return

                assignments = ", ".join(
                    f"`{column}` = %s"
                    for column in columns[1:]
                )

                sql = (
                    "UPDATE bills SET "
                    + assignments
                    + " WHERE id = %s"
                )

                cursor.execute(
                    sql,
                    tuple(values[1:] + [bill_id])
                )

                # Replace item rows for this bill.
                cursor.execute(
                    """
                    DELETE FROM bill_items
                    WHERE bill_id = %s
                    """,
                    (bill_id,)
                )

                action = "updated"

            # -----------------------------------------------
            # INSERT NEW BILL
            # -----------------------------------------------

            else:
                column_sql = ", ".join(
                    f"`{column}`"
                    for column in columns
                )

                placeholders = ", ".join(
                    ["%s"] * len(columns)
                )

                sql = (
                    f"INSERT INTO bills ({column_sql}) "
                    f"VALUES ({placeholders})"
                )

                cursor.execute(
                    sql,
                    tuple(values)
                )

                bill_id = cursor.lastrowid
                action = "saved"

            # -----------------------------------------------
            # SAVE BILL ITEMS
            # -----------------------------------------------

            item_sql = """
                INSERT INTO bill_items (
                    bill_id,
                    item_code,
                    item_name,
                    quantity,
                    rate,
                    amount
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """

            for item in data["items"]:
                quantity = num(
                    item["qty"]
                )

                rate = num(
                    item["rate"]
                )

                amount = quantity * rate

                cursor.execute(
                    item_sql,
                    (
                        bill_id,
                        item["code"],
                        item["item"],
                        str(quantity),
                        str(rate),
                        money(amount)
                    )
                )

            connection.commit()

            self.status.set(
                f"Bill {bill_no} {action} successfully."
            )

            messagebox.showinfo(
                "MEQETA Billing",
                f"Bill {bill_no} {action} successfully."
            )

        except (
            Error,
            RuntimeError,
            ValueError
        ) as error:
            if connection is not None:
                connection.rollback()

            messagebox.showerror(
                "Save Failed",
                str(error)
            )

        finally:
            if cursor is not None:
                cursor.close()

            if connection is not None:
                connection.close()

    # ========================================================
    # LOAD SAVED BILL DATA INTO FORM
    # ========================================================

    def load_data(self, data):
        if self.automation_running:
            return

        self.clear_form()

        for key in self.v:
            if key in data:
                self.v[key].set(
                    str(data[key] or "")
                )

                widget = self.fields.get(key)

                if isinstance(
                    widget,
                    ttk.Combobox
                ):
                    value = self.v[key].get()

                    options = list(
                        widget["values"]
                    )

                    if (
                        value
                        and value not in options
                    ):
                        options.append(value)
                        widget["values"] = options

        self.mode.set(
            data.get("mode", "Current")
        )

        self.paid.set(
            bool(data.get("paid", False))
        )

        for key, value in data.get(
            "charges", {}
        ).items():
            if key in self.charges:
                self.charges[key].set(
                    str(value)
                )

        items = data.get("items", [])

        while len(self.rows) < len(items):
            self.add_row()

        for index, item in enumerate(items):
            for key in self.rows[index]:
                self.rows[index][key].set(
                    str(item.get(key) or "")
                )

        self.calculate()

        self.status.set(
            f"Bill {data.get('bill', '')} loaded."
        )

    # ========================================================
    # FIND BILL BY BILL NUMBER
    # ========================================================

    def find_bill(self):
        if self.automation_running:
            return

        bill_no = simpledialog.askstring(
            "Find Bill",
            "Enter Bill Number:",
            initialvalue=self.v["bill"].get(),
            parent=self.root
        )

        if bill_no is None:
            return

        bill_no = bill_no.strip()

        if not bill_no:
            return

        try:
            data = self.get_bill_from_database(
                bill_no
            )

            if data is None:
                messagebox.showwarning(
                    "Bill Not Found",
                    f"Bill {bill_no} was not found."
                )
                return

            self.load_data(data)

        except (
            Error,
            RuntimeError,
            ValueError
        ) as error:
            messagebox.showerror(
                "Find Failed",
                str(error)
            )

    # ========================================================
    # CLONE CURRENT BILL
    # ========================================================

    def clone_bill(self):
        if self.automation_running:
            return

        source = self.collect_data()

        if not source["bill"].strip():
            messagebox.showwarning(
                "Clone Bill",
                "Load or enter a bill first."
            )
            return

        new_bill_no = simpledialog.askstring(
            "Clone Bill",
            "Enter New Bill Number:",
            parent=self.root
        )

        if new_bill_no is None:
            return

        new_bill_no = new_bill_no.strip()

        if not new_bill_no:
            return

        if new_bill_no == source["bill"].strip():
            messagebox.showwarning(
                "Clone Bill",
                "The new Bill Number must be different."
            )
            return

        try:
            if self.bill_exists(new_bill_no):
                messagebox.showwarning(
                    "Clone Bill",
                    "This Bill Number already exists."
                )
                return

        except (Error, RuntimeError) as error:
            messagebox.showerror(
                "Database Error",
                str(error)
            )
            return

        new_kot = simpledialog.askstring(
            "Clone Bill",
            "Enter New KOT Number:",
            parent=self.root
        )

        if new_kot is None:
            return

        new_kot = new_kot.strip()

        if not new_kot:
            messagebox.showwarning(
                "Clone Bill",
                "A KOT Number is required."
            )
            return

        cloned = copy.deepcopy(source)

        cloned["bill"] = new_bill_no
        cloned["kot"] = new_kot

        self.load_data(cloned)

        self.status.set(
            f"Bill cloned as {new_bill_no}. "
            "Click Save to store it in MySQL."
        )

        messagebox.showinfo(
            "Clone Bill",
            f"Bill {new_bill_no} is ready.\n\n"
            "Click Save to store the cloned bill."
        )

    # ========================================================
    # DELETE BILL FROM MYSQL
    # ========================================================

    def delete_bill(self):
        if self.automation_running:
            return

        bill_no = self.v["bill"].get().strip()

        if not bill_no:
            bill_no = simpledialog.askstring(
                "Delete Bill",
                "Enter Bill Number:",
                parent=self.root
            )

            if bill_no is None:
                return

            bill_no = bill_no.strip()

        if not bill_no:
            return

        if not messagebox.askyesno(
            "Confirm Delete",
            f"Permanently delete Bill {bill_no} "
            "and all its items?\n\n"
            "This action cannot be undone."
        ):
            return

        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor()

            cursor.execute(
                """
                SELECT id
                FROM bills
                WHERE bill_no = %s
                LIMIT 1
                """,
                (bill_no,)
            )

            result = cursor.fetchone()

            if result is None:
                messagebox.showwarning(
                    "Bill Not Found",
                    f"Bill {bill_no} does not exist."
                )
                return

            bill_id = result[0]

            cursor.execute(
                """
                DELETE FROM bill_items
                WHERE bill_id = %s
                """,
                (bill_id,)
            )

            cursor.execute(
                """
                DELETE FROM bills
                WHERE id = %s
                """,
                (bill_id,)
            )

            connection.commit()

            self.clear_form()

            self.status.set(
                f"Bill {bill_no} deleted."
            )

            messagebox.showinfo(
                "Delete Successful",
                f"Bill {bill_no} deleted successfully."
            )

        except (Error, RuntimeError) as error:
            if connection is not None:
                connection.rollback()

            messagebox.showerror(
                "Delete Failed",
                str(error)
            )

        finally:
            if cursor is not None:
                cursor.close()

            if connection is not None:
                connection.close()

    # ========================================================
    # CLEAR BILLING FORM
    # ========================================================

    def clear_form(self):
        if self.automation_running:
            return

        for variable in self.v.values():
            variable.set("")

        self.v["date"].set(
            datetime.now().strftime("%d/%m/%Y")
        )

        self.mode.set("Current")
        self.paid.set(False)

        for variable in self.charges.values():
            variable.set("0")

        for row in self.rows:
            for variable in row.values():
                variable.set("")

        self.calculate()

        self.status.set("Form cleared.")

        if "bill" in self.fields:
            self.fields["bill"].focus_set()

    # ========================================================
    # BILL SUMMARY
    # ========================================================

    def summary(self):
        data = self.collect_data()

        item_count = len(
            data["items"]
        )

        text = (
            "MEQETA - BILL SUMMARY\n\n"
            f"Bill No: {data['bill']}\n"
            f"KOT No: {data['kot']}\n"
            f"Date: {data['date']}\n"
            f"Waiter: {data['waiter']}\n"
            f"Service: {data['service']}\n"
            f"Guest: {data['guest']}\n"
            f"Items: {item_count}\n"
            f"Quantity: {data['totals']['qty']}\n"
            f"Subtotal: {data['totals']['total']}\n"
            f"Net Amount: {data['totals']['net']}\n"
            f"Paid: {'Yes' if data['paid'] else 'No'}"
        )

        messagebox.showinfo(
            "Bill Summary",
            text
        )

    # ========================================================
    # PRINT BILL
    # ========================================================

    def print_bill(self):
        if self.automation_running:
            return

        data = self.collect_data()

        if not data["bill"].strip():
            messagebox.showwarning(
                "Print Bill",
                "Please enter or load a bill first."
            )
            return

        lines = [
            "==========================================",
            "                 MEQETA",
            "         FOOD & BEVERAGES BILL",
            "==========================================",
            f"Bill No : {data['bill']}",
            f"KOT No  : {data['kot']}",
            f"Date    : {data['date']}",
            f"Waiter  : {data['waiter']}",
            f"Service : {data['service']}",
            f"Guest   : {data['guest']}",
            "------------------------------------------",
            f"{'ITEM':22} {'QTY':>5} {'AMOUNT':>10}",
            "------------------------------------------"
        ]

        for item in data["items"]:
            item_name = item["item"][:22]

            quantity = item["qty"]

            amount = item["amount"]

            lines.append(
                f"{item_name:22} "
                f"{quantity:>5} "
                f"{amount:>10}"
            )

        lines.extend([
            "------------------------------------------",
            f"Subtotal   : {data['totals']['total']}",
            f"CGST       : {data['totals']['cgst_amount']}",
            f"SGST       : {data['totals']['sgst_amount']}",
            f"CESS       : {data['totals']['cess_amount']}",
            f"Round Off  : {data['totals']['round_off']}",
            f"NET AMOUNT : {data['totals']['net']}",
            "==========================================",
            "               THANK YOU",
            "=========================================="
        ])

        content = "\n".join(lines)

        safe_bill = re.sub(
            r"[^A-Za-z0-9_-]",
            "_",
            data["bill"]
        )

        output_file = (
            EXPORT_DIRECTORY
            / f"bill_{safe_bill}.txt"
        )

        try:
            output_file.write_text(
                content,
                encoding="utf-8"
            )

            if sys.platform == "win32":
                os.startfile(
                    str(output_file),
                    "print"
                )

                messagebox.showinfo(
                    "Print Bill",
                    "Bill sent to the Windows "
                    "default print handler."
                )

            else:
                messagebox.showinfo(
                    "Print Bill",
                    f"Bill exported to:\n{output_file}"
                )

        except (OSError, ValueError) as error:
            messagebox.showerror(
                "Print Failed",
                str(error)
            )

    # ========================================================
    # UPDATE CLOCK
    # ========================================================

    def clock(self):
        if self.closing:
            return

        self.v["time"].set(
            datetime.now().strftime(
                "%I:%M:%S %p"
            )
        )

        self.root.after(
            1000,
            self.clock
        )

    # ========================================================
    # CLOSE APPLICATION
    # ========================================================

    def close_app(self):
        if self.closing:
            return

        if not messagebox.askyesno(
            "Exit MEQETA",
            "Are you sure you want to exit?"
        ):
            return

        self.closing = True

        self.automation_running = False
        self.automation_token += 1

        self.external_cancel.set()
        if getattr(self, "click_fill_listener", None) is not None:
            self.click_fill_listener.stop()
        if hasattr(self, "click_fill_stop"):
            self.click_fill_stop.set()

        if self.automation_job is not None:
            try:
                self.root.after_cancel(
                    self.automation_job
                )
            except tk.TclError:
                pass

        if self.kot_fill_job is not None:
            try:
                self.root.after_cancel(
                    self.kot_fill_job
                )
            except tk.TclError:
                pass

        self.root.destroy()


# ============================================================
# APPLICATION STARTUP
# ============================================================

def main():
    root = tk.Tk()

    app = BillingApp(root)

    root.mainloop()


if __name__ == "__main__":
    main()


# ============================================================
# END OF MEQETA MAIN2.PY
# ============================================================
