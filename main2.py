
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path
from datetime import datetime, date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import calendar
import copy
import json
import re
import threading
import ctypes
import os
import queue
import sys

import mysql.connector
from mysql.connector import Error

import pyautogui
import pyperclip


# ============================================================
# CONFIGURATION
# ============================================================

BASE = Path(__file__).resolve().parent
INK_FILE = BASE / "ink.txt"

# Existing JSON file is no longer used for database storage.
# Keep food_bills.json as a backup of your old bills.

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "billing_app",
    "database": "meqeta_billing",
    "connection_timeout": 5
}

# Internal automation timings
INTERNAL_FIELD_DELAY_MS = 2000
INTERNAL_DROPDOWN_HOLD_MS = 350

# External automation timings
TARGET_DELAY_MS = 3000
EXTERNAL_FIELD_DELAY = 2
EXTERNAL_DROPDOWN_DELAY = 0.4

EXTERNAL_NEXT_KEY = "enter"

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

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db_connection():
    """
    Connect to the existing MariaDB database.

    Password comes from the environment variable:
        MEQETA_DB_PASSWORD
    """

    password = os.environ.get("MEQETA_DB_PASSWORD")

    if not password:
        raise RuntimeError(
            "Database password is not configured.\n\n"
            "In PowerShell, run:\n"
            '$env:MEQETA_DB_PASSWORD = "YOUR_PASSWORD"\n\n'
            "Then start this application from that terminal."
        )

    config = dict(DB_CONFIG)
    config["password"] = password

    return mysql.connector.connect(**config)


def test_database_connection():
    """
    Test connectivity and verify required tables.
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

        required = {"bills", "bill_items"}

        missing = required - tables

        if missing:
            raise RuntimeError(
                "Missing database tables: "
                + ", ".join(sorted(missing))
            )

        print("MariaDB connected successfully!")
        print("Database: meqeta_billing")
        print("Tables: bills, bill_items")

        return True

    except (Error, RuntimeError) as error:
        print("Database connection failed:", error)
        return False

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


# ============================================================
# WINDOWS ACTIVE WINDOW DETECTION
# ============================================================

def active_window_handle():
    try:
        return ctypes.windll.user32.GetForegroundWindow()

    except (AttributeError, OSError):
        return None


def root_window_handle(hwnd):
    if not hwnd or sys.platform != "win32":
        return None

    try:
        return ctypes.windll.user32.GetAncestor(hwnd, 2)

    except (AttributeError, OSError):
        return None


# ============================================================
# GENERAL HELPERS
# ============================================================

def num(value):
    try:
        result = Decimal(str(value).strip() or "0")

        if not result.is_finite() or result < 0:
            return Decimal("0")

        return result

    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def money(value):
    return str(
        Decimal(value).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )
    )


def normalize_date(value):
    value = str(value).strip()

    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d"
    ):
        try:
            return datetime.strptime(
                value, fmt
            ).strftime("%d/%m/%Y")

        except ValueError:
            continue

    raise ValueError(
        f"Invalid date: {value}. Use DD/MM/YYYY."
    )


def optional_sql_date(value):
    """
    Convert a date string to a Python date,
    or return None for an empty date.
    """

    value = str(value).strip()

    if not value:
        return None

    normalized = normalize_date(value)

    return datetime.strptime(
        normalized, "%d/%m/%Y"
    ).date()


def sql_date_to_text(value):
    if value is None:
        return ""

    if isinstance(value, (date, datetime)):
        return value.strftime("%d/%m/%Y")

    return normalize_date(value)


def generate_date_options():
    today = datetime.now()
    dates = []

    for offset in (-1, 0, 1):
        month_index = (
            today.year * 12
            + today.month - 1
            + offset
        )

        year, zero_month = divmod(month_index, 12)
        month = zero_month + 1

        days = calendar.monthrange(year, month)[1]

        for day_number in range(1, days + 1):
            dates.append(
                f"{day_number:02d}/{month:02d}/{year}"
            )

    return dates


# ============================================================
# READ ink.txt
# ============================================================

def read_ink():
    """
    Supported ink.txt:

    bill=1001
    date=05/11/2026
    waiter=AKASH
    service=ROOM SERVICE
    kot=501

    Legacy helper. Fill to PMS now uses MariaDB.
    """

    if not INK_FILE.exists():
        raise FileNotFoundError(
            f"ink.txt not found:\n{INK_FILE}"
        )

    content = INK_FILE.read_text(
        encoding="utf-8-sig"
    ).strip()

    if not content:
        raise ValueError("ink.txt is empty.")

    aliases = {
        "bill": "bill",
        "billno": "bill",
        "billnumber": "bill",
        "billid": "bill",

        "date": "date",
        "billdate": "date",

        "waiter": "waiter",
        "waitername": "waiter",

        "service": "service",
        "servicetype": "service",
        "sertype": "service",

        "kot": "kot",
        "kotno": "kot",
        "kotnumber": "kot"
    }

    data = {}

    def add_value(raw_key, value):
        key = re.sub(
            r"[^a-z0-9]",
            "",
            str(raw_key).lower()
        )

        mapped = aliases.get(key)

        if mapped and value is not None:
            value = str(value).strip()

            if value:
                data[mapped] = value

    if content.startswith("{"):
        try:
            parsed = json.loads(content)

            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    add_value(key, value)

        except json.JSONDecodeError:
            pass

    if not data:
        for line in content.splitlines():
            line = line.strip()

            if not line or line.startswith(
                ("#", ";", "//")
            ):
                continue

            match = re.match(
                r"^([^=:]+?)\s*[:=]\s*(.*)$",
                line
            )

            if match:
                add_value(
                    match.group(1),
                    match.group(2)
                )

    missing = [
        key for key in PMS_FIELDS
        if not data.get(key)
    ]

    if missing:
        raise ValueError(
            "Missing ink.txt fields: "
            + ", ".join(missing)
        )

    data["date"] = normalize_date(data["date"])

    return data


# ============================================================
# MAIN APPLICATION
# ============================================================

class BillingApp:

    def __init__(self, root):
        self.root = root

        self.root.title(
            "Food & Beverages Billing Entry"
        )

        self.root.geometry("1180x780")
        self.root.minsize(1050, 680)

        self.bg = "#e8e8e8"
        self.blue = "#253e70"

        self.root.configure(bg=self.bg)

        names = [
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
            for key in names
        }

        self.mode = tk.StringVar(value="Current")
        self.paid = tk.BooleanVar(value=False)

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

        # Automation state
        self.automation_running = False
        self.automation_job = None
        self.automation_token = 0
        self.automation_index = 0
        self.automation_data = {}

        self.external_cancel = threading.Event()
        self.external_thread = None
        self.external_results = queue.Queue()

        self.closing = False

        # Extra fields retained in memory because the
        # existing SQL schema does not contain them.
        self.extra_data = {}

        # Build the original interface
        self.build_ui()
        self.bind_navigation()

        for variable in self.charges.values():
            variable.trace_add(
                "write",
                lambda *_: self.calculate()
            )

        self.calculate()
        self.clock()
        self.poll_external_results()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close_app
        )

        self.root.after(
            300,
            self.check_database_on_start
        )

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path
from datetime import datetime, date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import calendar
import copy
import json
import re
import threading
import ctypes
import os
import queue
import sys

import mysql.connector
from mysql.connector import Error

import pyautogui
import pyperclip


# ============================================================
# CONFIGURATION
# ============================================================

BASE = Path(__file__).resolve().parent
INK_FILE = BASE / "ink.txt"

# Existing JSON file is no longer used for database storage.
# Keep food_bills.json as a backup of your old bills.

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "billing_app",
    "database": "meqeta_billing",
    "connection_timeout": 5
}

# Internal automation timings
INTERNAL_FIELD_DELAY_MS = 2000
INTERNAL_DROPDOWN_HOLD_MS = 350

# External automation timings
TARGET_DELAY_MS = 3000
EXTERNAL_FIELD_DELAY = 2
EXTERNAL_DROPDOWN_DELAY = 0.4

EXTERNAL_NEXT_KEY = "enter"

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

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db_connection():
    """
    Connect to the existing MariaDB database.

    Password comes from the environment variable:
        MEQETA_DB_PASSWORD
    """

    password = os.environ.get("MEQETA_DB_PASSWORD")

    if not password:
        raise RuntimeError(
            "Database password is not configured.\n\n"
            "In PowerShell, run:\n"
            '$env:MEQETA_DB_PASSWORD = "YOUR_PASSWORD"\n\n'
            "Then start this application from that terminal."
        )

    config = dict(DB_CONFIG)
    config["password"] = password

    return mysql.connector.connect(**config)


def test_database_connection():
    """
    Test connectivity and verify required tables.
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

        required = {"bills", "bill_items"}

        missing = required - tables

        if missing:
            raise RuntimeError(
                "Missing database tables: "
                + ", ".join(sorted(missing))
            )

        print("MariaDB connected successfully!")
        print("Database: meqeta_billing")
        print("Tables: bills, bill_items")

        return True

    except (Error, RuntimeError) as error:
        print("Database connection failed:", error)
        return False

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


# ============================================================
# WINDOWS ACTIVE WINDOW DETECTION
# ============================================================

def active_window_handle():
    try:
        return ctypes.windll.user32.GetForegroundWindow()

    except (AttributeError, OSError):
        return None


def root_window_handle(hwnd):
    if not hwnd or sys.platform != "win32":
        return None

    try:
        return ctypes.windll.user32.GetAncestor(hwnd, 2)

    except (AttributeError, OSError):
        return None


# ============================================================
# GENERAL HELPERS
# ============================================================

def num(value):
    try:
        result = Decimal(str(value).strip() or "0")

        if not result.is_finite() or result < 0:
            return Decimal("0")

        return result

    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def money(value):
    return str(
        Decimal(value).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )
    )


def normalize_date(value):
    value = str(value).strip()

    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d"
    ):
        try:
            return datetime.strptime(
                value, fmt
            ).strftime("%d/%m/%Y")

        except ValueError:
            continue

    raise ValueError(
        f"Invalid date: {value}. Use DD/MM/YYYY."
    )


def optional_sql_date(value):
    """
    Convert a date string to a Python date,
    or return None for an empty date.
    """

    value = str(value).strip()

    if not value:
        return None

    normalized = normalize_date(value)

    return datetime.strptime(
        normalized, "%d/%m/%Y"
    ).date()


def sql_date_to_text(value):
    if value is None:
        return ""

    if isinstance(value, (date, datetime)):
        return value.strftime("%d/%m/%Y")

    return normalize_date(value)


def generate_date_options():
    today = datetime.now()
    dates = []

    for offset in (-1, 0, 1):
        month_index = (
            today.year * 12
            + today.month - 1
            + offset
        )

        year, zero_month = divmod(month_index, 12)
        month = zero_month + 1

        days = calendar.monthrange(year, month)[1]

        for day_number in range(1, days + 1):
            dates.append(
                f"{day_number:02d}/{month:02d}/{year}"
            )

    return dates


# ============================================================
# READ ink.txt
# ============================================================

def read_ink():
    """
    Supported ink.txt:

    bill=1001
    date=05/11/2026
    waiter=AKASH
    service=ROOM SERVICE
    kot=501

    Legacy helper. Fill to PMS now uses MariaDB.
    """

    if not INK_FILE.exists():
        raise FileNotFoundError(
            f"ink.txt not found:\n{INK_FILE}"
        )

    content = INK_FILE.read_text(
        encoding="utf-8-sig"
    ).strip()

    if not content:
        raise ValueError("ink.txt is empty.")

    aliases = {
        "bill": "bill",
        "billno": "bill",
        "billnumber": "bill",
        "billid": "bill",

        "date": "date",
        "billdate": "date",

        "waiter": "waiter",
        "waitername": "waiter",

        "service": "service",
        "servicetype": "service",
        "sertype": "service",

        "kot": "kot",
        "kotno": "kot",
        "kotnumber": "kot"
    }

    data = {}

    def add_value(raw_key, value):
        key = re.sub(
            r"[^a-z0-9]",
            "",
            str(raw_key).lower()
        )

        mapped = aliases.get(key)

        if mapped and value is not None:
            value = str(value).strip()

            if value:
                data[mapped] = value

    if content.startswith("{"):
        try:
            parsed = json.loads(content)

            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    add_value(key, value)

        except json.JSONDecodeError:
            pass

    if not data:
        for line in content.splitlines():
            line = line.strip()

            if not line or line.startswith(
                ("#", ";", "//")
            ):
                continue

            match = re.match(
                r"^([^=:]+?)\s*[:=]\s*(.*)$",
                line
            )

            if match:
                add_value(
                    match.group(1),
                    match.group(2)
                )

    missing = [
        key for key in PMS_FIELDS
        if not data.get(key)
    ]

    if missing:
        raise ValueError(
            "Missing ink.txt fields: "
            + ", ".join(missing)
        )

    data["date"] = normalize_date(data["date"])

    return data


# ============================================================
# MAIN APPLICATION
# ============================================================

class BillingApp:

    def __init__(self, root):
        self.root = root

        self.root.title(
            "Food & Beverages Billing Entry"
        )

        self.root.geometry("1180x780")
        self.root.minsize(1050, 680)

        self.bg = "#e8e8e8"
        self.blue = "#253e70"

        self.root.configure(bg=self.bg)

        names = [
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
            for key in names
        }

        self.mode = tk.StringVar(value="Current")
        self.paid = tk.BooleanVar(value=False)

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

        # Automation state
        self.automation_running = False
        self.automation_job = None
        self.automation_token = 0
        self.automation_index = 0
        self.automation_data = {}

        self.external_cancel = threading.Event()
        self.external_thread = None
        self.external_results = queue.Queue()

        self.closing = False

        # Extra fields retained in memory because the
        # existing SQL schema does not contain them.
        self.extra_data = {}

        # Build the original interface
        self.build_ui()
        self.bind_navigation()

        for variable in self.charges.values():
            variable.trace_add(
                "write",
                lambda *_: self.calculate()
            )

        self.calculate()
        self.clock()
        self.poll_external_results()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close_app
        )

        self.root.after(
            300,
            self.check_database_on_start
        )

    # ========================================================
    # EXTERNAL PMS AUTOMATION
    # ========================================================

    def start_external_automation(self, token):
        self.status.set(
            "External PMS automation started..."
        )

        data = dict(self.automation_data)

        self.external_thread = threading.Thread(
            target=self.external_worker,
            args=(data, token),
            daemon=True
        )

        self.external_thread.start()

    def external_worker(self, data, token):
        """
        External PMS sequence:

        Bill -> Enter -> Date -> Enter ->
        Waiter -> Enter -> Service -> Enter -> KOT

        IMPORTANT:
        The external PMS Bill No field must have
        keyboard focus when automation starts.
        """

        try:
            sequence = [
                ("bill", data["bill"]),
                ("date", data["date"]),
                ("waiter", data["waiter"]),
                ("service", data["service"]),
                ("kot", data["kot"])
            ]

            for index, (key, value) in enumerate(sequence):
                if self.external_cancel.is_set():
                    return

                if key in DROPDOWN_FIELDS:
                    self.fill_external_dropdown(value)
                else:
                    self.fill_external_text(value)

                if index < len(sequence) - 1:
                    if self.external_cancel.wait(
                        EXTERNAL_FIELD_DELAY
                    ):
                        return

                    pyautogui.press(EXTERNAL_NEXT_KEY)

            if not self.external_cancel.is_set():
                self.external_results.put(
                    (
                        token,
                        True,
                        "External PMS automation completed."
                    )
                )

        except Exception as error:
            if not self.external_cancel.is_set():
                self.external_results.put(
                    (token, False, str(error))
                )

    def fill_external_text(self, value):
        pyperclip.copy(str(value))
        pyautogui.hotkey("ctrl", "a")
        pyautogui.hotkey("ctrl", "v")

    def fill_external_dropdown(self, value):
        pyautogui.hotkey("alt", "down")

        if self.external_cancel.wait(
            EXTERNAL_DROPDOWN_DELAY
        ):
            return

        pyautogui.write(
            str(value),
            interval=0.04
        )

    def poll_external_results(self):
        if self.closing:
            return

        try:
            while True:
                token, success, message = (
                    self.external_results.get_nowait()
                )

                if token == self.automation_token:
                    self.finish_automation(
                        message, success
                    )

        except queue.Empty:
            pass

        self.root.after(
            100,
            self.poll_external_results
        )

    # ========================================================
    # AUTOMATION CONTROL MANAGEMENT
    # ========================================================

    def set_controls(self, enabled):
        state = (
            "normal" if enabled else "disabled"
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
            button.config(state=state)

    def finish_automation(
        self, message, success=True
    ):
        if self.closing:
            return

        self.automation_running = False
        self.automation_job = None

        self.set_controls(True)
        self.status.set(message)

        if not success:
            messagebox.showerror(
                "Automation Error",
                message
            )

    def cancel_automation(self):
        self.automation_token += 1
        self.external_cancel.set()

        if self.automation_job is not None:
            try:
                self.root.after_cancel(
                    self.automation_job
                )
            except tk.TclError:
                pass

        self.automation_job = None
        self.automation_running = False

    # ========================================================
    # BILLING TABLE
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
            lambda e: self.canvas.configure(
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

        for _ in range(20):
            self.add_row()

        self.canvas.bind(
            "<MouseWheel>",
            lambda e: self.canvas.yview_scroll(
                -1 if e.delta > 0 else 1,
                "units"
            )
        )

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
                lambda e, r=index, k=key:
                    self.next_cell(r, k)
            )

            widget.bind(
                "<Tab>",
                lambda e, r=index, k=key:
                    self.next_cell(r, k)
            )

            widget.bind(
                "<Shift-Tab>",
                lambda e, r=index, k=key:
                    self.previous_cell(r, k)
            )

            for event, dr, dc in (
                ("<Up>", -1, 0),
                ("<Down>", 1, 0),
                ("<Left>", 0, -1),
                ("<Right>", 0, 1)
            ):
                widget.bind(
                    event,
                    lambda e, r=index, k=key,
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
                self.canvas.yview_moveto(y / height)

            elif y + 27 > (
                self.canvas.canvasy(0) + view_height
            ):
                self.canvas.yview_moveto(
                    (y + 27 - view_height) / height
                )

        if width > view_width:
            if x < self.canvas.canvasx(0):
                self.canvas.xview_moveto(x / width)

            elif (
                x + frame.winfo_width()
                > self.canvas.canvasx(0) + view_width
            ):
                self.canvas.xview_moveto(
                    (
                        x + frame.winfo_width() - view_width
                    ) / width
                )

        widget = self.cells[row][
            self.table_keys[column]
        ]

        widget.focus_set()
        widget.select_range(0, "end")

    def next_cell(self, row, key):
        column = self.table_keys.index(key)

        if column == 3:
            self.focus_cell(row + 1, 0)
        else:
            self.focus_cell(row, column + 1)

        return "break"

    def previous_cell(self, row, key):
        column = self.table_keys.index(key)

        if column == 0 and row > 0:
            self.focus_cell(row - 1, 3)
        elif column > 0:
            self.focus_cell(row, column - 1)

        return "break"

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
    # BOTTOM CONTROLS
    # ========================================================

    def create_bottom(self, parent):
        self.label(
            parent, "Detail :", 0, 8, 75
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
            80, 8, 330
        )

        tk.Checkbutton(
            parent,
            text="Bill Paid",
            variable=self.paid,
            bg=self.bg
        ).place(x=80, y=45)

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
            ("Service Charge", "service_charge", "input"),
            ("+ CGST (%)", "cgst", "tax"),
            ("+ SGST (%)", "sgst", "tax"),
            ("+ CESS (%)", "cess", "tax"),
            ("ROUND OFF", "round_off", "output"),
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
    # CALCULATIONS
    # ========================================================

    def calculate(self):
        qty_total = Decimal("0")
        subtotal = Decimal("0")

        for row in self.rows:
            qty = num(row["qty"].get())
            rate = num(row["rate"].get())

            if row["qty"].get().strip():
                qty_total += qty

            if (
                row["qty"].get().strip()
                and row["rate"].get().strip()
            ):
                amount = qty * rate
                row["amount"].set(money(amount))
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

        for key in ("cgst", "sgst", "cess"):
            taxes[key] = (
                taxable
                * num(self.charges[key].get())
                / Decimal("100")
            ).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP
            )

            self.totals[
                key + "_amount"
            ].set(money(taxes[key]))

        gross = taxable + sum(
            taxes.values(),
            Decimal("0")
        )

        net = gross.quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )

        self.totals["qty"].set(str(qty_total))
        self.totals["total"].set(money(subtotal))

        self.totals["round_off"].set(
            money(net - gross)
        )

        self.totals["net"].set(money(net))

    # ========================================================
    # COLLECT FORM DATA
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
                data["items"].append(item)

        return data

    # ========================================================
    # DATABASE READ HELPERS - UPDATED
    # ========================================================

    def get_bill_from_database(self, bill_no):
        """
        Fetch a bill and all associated items.

        If bill_no is None:
            Fetch the latest inserted bill.

        Otherwise:
            Fetch the specified bill number.

        Returns a dictionary compatible with load_data().
        """

        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)

            # ==========================================
            # LATEST BILL OR SPECIFIC BILL
            # ==========================================

            if bill_no is None:
                # Highest ID = most recently inserted bill
                cursor.execute(
                    "SELECT * FROM bills "
                    "ORDER BY id DESC LIMIT 1"
                )
            else:
                cursor.execute(
                    "SELECT * FROM bills WHERE bill_no = %s",
                    (bill_no,)
                )

            bill = cursor.fetchone()

            if bill is None:
                return None

            # ==========================================
            # FETCH BILL ITEMS
            # ==========================================

            cursor.execute(
                """
                SELECT
                    item_code,
                    item_name,
                    quantity,
                    rate,
                    amount
                FROM bill_items
                WHERE bill_id = %s
                ORDER BY id
                """,
                (bill["id"],)
            )

            items = cursor.fetchall()

            # ==========================================
            # CONVERT DATABASE RECORD TO FORM DATA
            # ==========================================

            data = {
                "bill": bill["bill_no"],
                "date": sql_date_to_text(
                    bill["bill_date"]
                ),
                "waiter": bill["waiter"] or "",
                "service": bill["service_type"] or "",
                "kot": bill["kot_no"] or "",
                "room": bill["room_no"] or "",
                "reg": bill["reg_no"] or "",
                "guest": bill["guest_name"] or "",
                "company": bill["company"] or "",
                "detail": bill["payment_method"] or "",
                "paid": bool(bill["paid"]),
                "mode": "Current",

                "charges": {
                    "discount": str(
                        bill["discount"] or 0
                    ),
                    "service_charge": str(
                        bill["service_charge"] or 0
                    ),
                    "cgst": str(
                        bill["cgst_rate"] or 0
                    ),
                    "sgst": str(
                        bill["sgst_rate"] or 0
                    ),
                    "cess": str(
                        bill["cess_rate"] or 0
                    )
                },

                "items": []
            }

            # ==========================================
            # CONVERT DATABASE ITEMS TO TABLE ROWS
            # ==========================================

            for item in items:
                data["items"].append({
                    "code": item["item_code"] or "",
                    "item": item["item_name"] or "",
                    "qty": str(
                        item["quantity"] or 0
                    ),
                    "rate": str(
                        item["rate"] or 0
                    ),
                    "amount": str(
                        item["amount"] or 0
                    )
                })

            return data

        finally:
            if cursor is not None:
                cursor.close()

            if connection is not None:
                connection.close()

    # ========================================================
    # GET LATEST BILL - NEW FUNCTION
    # ========================================================

    def get_latest_bill_from_database(self):
        """
        Retrieve the latest inserted bill from MariaDB.

        This is used automatically by Fill to PMS.

        It does not depend on the bill number currently
        displayed in the application.
        """

        return self.get_bill_from_database(None)

    # ========================================================
    # CHECK WHETHER BILL EXISTS
    # ========================================================

    def bill_exists(self, bill_no):
        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor()

            cursor.execute(
                "SELECT id FROM bills WHERE bill_no = %s",
                (bill_no,)
            )

            return cursor.fetchone() is not None

        finally:
            if cursor is not None:
                cursor.close()

            if connection is not None:
                connection.close()

    # ========================================================
    # SAVE / UPDATE BILL
    # ========================================================

    def save_bill(self):
        if self.automation_running:
            return

        data = self.collect_data()
        bill_no = data["bill"].strip()

        if not bill_no:
            messagebox.showwarning(
                "Save",
                "Bill No is required."
            )
            return

        connection = None
        cursor = None

        try:
            bill_date = optional_sql_date(
                data["date"]
            )

            # Validate item values before writing.
            for index, item in enumerate(
                data["items"], start=1
            ):
                for key in ("qty", "rate"):
                    raw = item[key].strip()

                    if raw:
                        try:
                            value = Decimal(raw)

                            if (
                                not value.is_finite()
                                or value < 0
                            ):
                                raise ValueError

                        except (
                            InvalidOperation,
                            ValueError
                        ):
                            raise ValueError(
                                f"Invalid {key} in "
                                f"item row {index}."
                            )

            connection = get_db_connection()
            connection.start_transaction()

            cursor = connection.cursor()

            # Lock an existing bill during update.
            cursor.execute(
                """
                SELECT id
                FROM bills
                WHERE bill_no = %s
                FOR UPDATE
                """,
                (bill_no,)
            )

            existing = cursor.fetchone()

            values = (
                bill_date,
                data["waiter"],
                data["service"],
                data["kot"],
                data["room"],
                data["reg"],
                data["guest"],
                data["company"],
                data["detail"],
                int(data["paid"]),
                num(data["totals"]["total"]),
                num(data["charges"]["discount"]),
                num(data["charges"]["service_charge"]),
                num(data["charges"]["cgst"]),
                num(data["charges"]["sgst"]),
                num(data["charges"]["cess"]),
                num(data["totals"]["net"])
            )

            if existing:
                bill_id = existing[0]

                if not messagebox.askyesno(
                    "Overwrite",
                    f"Bill {bill_no} already exists.\n"
                    "Overwrite it?"
                ):
                    connection.rollback()
                    return

                cursor.execute(
                    """
                    UPDATE bills
                    SET
                        bill_date = %s,
                        waiter = %s,
                        service_type = %s,
                        kot_no = %s,
                        room_no = %s,
                        reg_no = %s,
                        guest_name = %s,
                        company = %s,
                        payment_method = %s,
                        paid = %s,
                        subtotal = %s,
                        discount = %s,
                        service_charge = %s,
                        cgst_rate = %s,
                        sgst_rate = %s,
                        cess_rate = %s,
                        net_amount = %s
                    WHERE id = %s
                    """,
                    values + (bill_id,)
                )

                cursor.execute(
                    """
                    DELETE FROM bill_items
                    WHERE bill_id = %s
                    """,
                    (bill_id,)
                )

            else:
                cursor.execute(
                    """
                    INSERT INTO bills (
                        bill_no,
                        bill_date,
                        waiter,
                        service_type,
                        kot_no,
                        room_no,
                        reg_no,
                        guest_name,
                        company,
                        payment_method,
                        paid,
                        subtotal,
                        discount,
                        service_charge,
                        cgst_rate,
                        sgst_rate,
                        cess_rate,
                        net_amount
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (bill_no,) + values
                )

                bill_id = cursor.lastrowid

            for item in data["items"]:
                qty = num(item["qty"])
                rate = num(item["rate"])

                amount = (
                    qty * rate
                ).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP
                )

                cursor.execute(
                    """
                    INSERT INTO bill_items (
                        bill_id,
                        item_code,
                        item_name,
                        quantity,
                        rate,
                        amount
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        bill_id,
                        item["code"],
                        item["item"],
                        qty,
                        rate,
                        amount
                    )
                )

            connection.commit()

            self.status.set(
                f"Bill {bill_no} saved to MariaDB."
            )

            messagebox.showinfo(
                "Saved",
                f"Bill {bill_no} saved successfully."
            )

        except (Error, RuntimeError, ValueError) as error:
            if connection is not None:
                connection.rollback()

            messagebox.showerror(
                "Database Error",
                str(error)
            )

        finally:
            if cursor is not None:
                cursor.close()

            if connection is not None:
                connection.close()

    # ========================================================
    # LOAD BILL
    # ========================================================

    def load_data(self, data):
        self.clear_form()

        for key in self.v:
            if key in data and key != "time":
                self.v[key].set(data[key])

        for key in DROPDOWN_FIELDS:
            value = self.v[key].get()

            if value:
                widget = self.fields[key]
                options = list(widget["values"])

                if value not in options:
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
                self.charges[key].set(str(value))

        items = data.get("items", [])

        while len(self.rows) < len(items):
            self.add_row()

        for index, item in enumerate(items):
            for key in self.table_keys:
                self.rows[index][key].set(
                    str(item.get(key, ""))
                )

        self.calculate()

    # ========================================================
    # FIND BILL
    # ========================================================

    def find_bill(self):
        if self.automation_running:
            return

        bill_no = simpledialog.askstring(
            "Find Bill",
            "Enter Bill No:",
            parent=self.root
        )

        if not bill_no:
            return

        bill_no = bill_no.strip()

        try:
            data = self.get_bill_from_database(
                bill_no
            )

            if data is None:
                messagebox.showinfo(
                    "Find",
                    "Bill not found in MariaDB."
                )
                return

            self.load_data(data)

            self.status.set(
                f"Loaded bill {bill_no} from MariaDB."
            )

        except (Error, RuntimeError, ValueError) as error:
            messagebox.showerror(
                "Database Error",
                str(error)
            )

    # ========================================================
    # CLONE BILL
    # ========================================================

    def clone_bill(self):
        if self.automation_running:
            return

        old_bill = self.v["bill"].get().strip()

        if not old_bill:
            messagebox.showwarning(
                "Clone",
                "Enter the original Bill No first."
            )
            return

        try:
            original = self.get_bill_from_database(
                old_bill
            )

            if original is None:
                messagebox.showwarning(
                    "Clone",
                    "Original bill not found in MariaDB."
                )
                return

            new_bill = simpledialog.askstring(
                "Clone Bill",
                "Enter new Bill No:",
                parent=self.root
            )

            if not new_bill:
                return

            new_bill = new_bill.strip()

            if not new_bill:
                messagebox.showwarning(
                    "Clone",
                    "New Bill No cannot be empty."
                )
                return

            if self.bill_exists(new_bill):
                messagebox.showwarning(
                    "Clone",
                    "New Bill No already exists."
                )
                return

            cloned = copy.deepcopy(original)
            cloned["bill"] = new_bill

            self.load_data(cloned)

            self.status.set(
                f"Cloned {old_bill} to {new_bill}. "
                "Click Save."
            )

            messagebox.showinfo(
                "Clone",
                "Bill cloned.\nClick Save to store it."
            )

        except (Error, RuntimeError, ValueError) as error:
            messagebox.showerror(
                "Database Error",
                str(error)
            )

    # ========================================================
    # SAVE / UPDATE BILL
    # ========================================================

    def save_bill(self):
        if self.automation_running:
            return

        data = self.collect_data()
        bill_no = data["bill"].strip()

        if not bill_no:
            messagebox.showwarning(
                "Save",
                "Bill No is required."
            )
            return

        connection = None
        cursor = None

        try:
            bill_date = optional_sql_date(
                data["date"]
            )

            # Validate item values before writing.
            for index, item in enumerate(
                data["items"], start=1
            ):
                for key in ("qty", "rate"):
                    raw = item[key].strip()

                    if raw:
                        try:
                            value = Decimal(raw)

                            if (
                                not value.is_finite()
                                or value < 0
                            ):
                                raise ValueError

                        except (
                            InvalidOperation,
                            ValueError
                        ):
                            raise ValueError(
                                f"Invalid {key} in "
                                f"item row {index}."
                            )

            connection = get_db_connection()
            connection.start_transaction()

            cursor = connection.cursor()

            # Lock an existing bill during update.
            cursor.execute(
                """
                SELECT id
                FROM bills
                WHERE bill_no = %s
                FOR UPDATE
                """,
                (bill_no,)
            )

            existing = cursor.fetchone()

            values = (
                bill_date,
                data["waiter"],
                data["service"],
                data["kot"],
                data["room"],
                data["reg"],
                data["guest"],
                data["company"],
                data["detail"],
                int(data["paid"]),
                num(data["totals"]["total"]),
                num(data["charges"]["discount"]),
                num(data["charges"]["service_charge"]),
                num(data["charges"]["cgst"]),
                num(data["charges"]["sgst"]),
                num(data["charges"]["cess"]),
                num(data["totals"]["net"])
            )

            if existing:
                bill_id = existing[0]

                if not messagebox.askyesno(
                    "Overwrite",
                    f"Bill {bill_no} already exists.\n"
                    "Overwrite it?"
                ):
                    connection.rollback()
                    return

                cursor.execute(
                    """
                    UPDATE bills
                    SET
                        bill_date = %s,
                        waiter = %s,
                        service_type = %s,
                        kot_no = %s,
                        room_no = %s,
                        reg_no = %s,
                        guest_name = %s,
                        company = %s,
                        payment_method = %s,
                        paid = %s,
                        subtotal = %s,
                        discount = %s,
                        service_charge = %s,
                        cgst_rate = %s,
                        sgst_rate = %s,
                        cess_rate = %s,
                        net_amount = %s
                    WHERE id = %s
                    """,
                    values + (bill_id,)
                )

                cursor.execute(
                    """
                    DELETE FROM bill_items
                    WHERE bill_id = %s
                    """,
                    (bill_id,)
                )

            else:
                cursor.execute(
                    """
                    INSERT INTO bills (
                        bill_no,
                        bill_date,
                        waiter,
                        service_type,
                        kot_no,
                        room_no,
                        reg_no,
                        guest_name,
                        company,
                        payment_method,
                        paid,
                        subtotal,
                        discount,
                        service_charge,
                        cgst_rate,
                        sgst_rate,
                        cess_rate,
                        net_amount
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (bill_no,) + values
                )

                bill_id = cursor.lastrowid

            for item in data["items"]:
                qty = num(item["qty"])
                rate = num(item["rate"])

                amount = (
                    qty * rate
                ).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP
                )

                cursor.execute(
                    """
                    INSERT INTO bill_items (
                        bill_id,
                        item_code,
                        item_name,
                        quantity,
                        rate,
                        amount
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        bill_id,
                        item["code"],
                        item["item"],
                        qty,
                        rate,
                        amount
                    )
                )

            connection.commit()

            self.status.set(
                f"Bill {bill_no} saved to MariaDB."
            )

            messagebox.showinfo(
                "Saved",
                f"Bill {bill_no} saved successfully."
            )

        except (Error, RuntimeError, ValueError) as error:
            if connection is not None:
                connection.rollback()

            messagebox.showerror(
                "Database Error",
                str(error)
            )

        finally:
            if cursor is not None:
                cursor.close()

            if connection is not None:
                connection.close()

    # ========================================================
    # LOAD BILL
    # ========================================================

    def load_data(self, data):
        self.clear_form()

        for key in self.v:
            if key in data and key != "time":
                self.v[key].set(data[key])

        for key in DROPDOWN_FIELDS:
            value = self.v[key].get()

            if value:
                widget = self.fields[key]
                options = list(widget["values"])

                if value not in options:
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
                self.charges[key].set(str(value))

        items = data.get("items", [])

        while len(self.rows) < len(items):
            self.add_row()

        for index, item in enumerate(items):
            for key in self.table_keys:
                self.rows[index][key].set(
                    str(item.get(key, ""))
                )

        self.calculate()

    # ========================================================
    # FIND BILL
    # ========================================================

    def find_bill(self):
        if self.automation_running:
            return

        bill_no = simpledialog.askstring(
            "Find Bill",
            "Enter Bill No:",
            parent=self.root
        )

        if not bill_no:
            return

        bill_no = bill_no.strip()

        try:
            data = self.get_bill_from_database(
                bill_no
            )

            if data is None:
                messagebox.showinfo(
                    "Find",
                    "Bill not found in MariaDB."
                )
                return

            self.load_data(data)

            self.status.set(
                f"Loaded bill {bill_no} from MariaDB."
            )

        except (Error, RuntimeError, ValueError) as error:
            messagebox.showerror(
                "Database Error",
                str(error)
            )

    # ========================================================
    # CLONE BILL
    # ========================================================

    def clone_bill(self):
        if self.automation_running:
            return

        old_bill = self.v["bill"].get().strip()

        if not old_bill:
            messagebox.showwarning(
                "Clone",
                "Enter the original Bill No first."
            )
            return

        try:
            original = self.get_bill_from_database(
                old_bill
            )

            if original is None:
                messagebox.showwarning(
                    "Clone",
                    "Original bill not found in MariaDB."
                )
                return

            new_bill = simpledialog.askstring(
                "Clone Bill",
                "Enter new Bill No:",
                parent=self.root
            )

            if not new_bill:
                return

            new_bill = new_bill.strip()

            if not new_bill:
                messagebox.showwarning(
                    "Clone",
                    "New Bill No cannot be empty."
                )
                return

            if self.bill_exists(new_bill):
                messagebox.showwarning(
                    "Clone",
                    "New Bill No already exists."
                )
                return

            cloned = copy.deepcopy(original)
            cloned["bill"] = new_bill

            self.load_data(cloned)

            self.status.set(
                f"Cloned {old_bill} to {new_bill}. "
                "Click Save."
            )

            messagebox.showinfo(
                "Clone",
                "Bill cloned.\nClick Save to store it."
            )

        except (Error, RuntimeError, ValueError) as error:
            messagebox.showerror(
                "Database Error",
                str(error)
            )
# ============================================================
# APPLICATION STARTUP
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = BillingApp(root)
    root.mainloop()