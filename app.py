
import os
import sqlite3
from pathlib import Path

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash
)
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash

from models import User

import re
from datetime import date

import secrets
from datetime import date

from io import BytesIO
from html import escape

from flask import send_file, abort
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
)
from reportlab.lib.styles import getSampleStyleSheet

from functools import wraps
from flask import abort
from flask_login import login_required, current_user

from flask import request, redirect, url_for, render_template, flash
from flask_login import login_required, current_user
from datetime import datetime
from flask import abort

# Create the Flask application
app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "railease.db"

# Use an environment variable for the secret key.
# The fallback is only for local development.
app.config["SECRET_KEY"] = os.environ.get(
    "RAILEASE_SECRET_KEY",
    "dev-only-change-this-secret-before-deployment"
)

# Protect forms against cross-site request forgery (CSRF)
CSRFProtect(app)

# Configure Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."


# Connect to SQLite
def get_db_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


# Create the users table if it doesn't exist

def init_db():
    with get_db_connection() as connection:

        # User accounts
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add role column to existing databases
        user_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(users)"
            ).fetchall()
        }

        if "role" not in user_columns:
            connection.execute("""
                ALTER TABLE users
                ADD COLUMN role TEXT NOT NULL DEFAULT 'user'
            """)

        # Train information
        connection.execute("""
            CREATE TABLE IF NOT EXISTS trains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                train_number TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                source TEXT NOT NULL,
                destination TEXT NOT NULL,
                departure_time TEXT NOT NULL,
                arrival_time TEXT NOT NULL
            )
        """)

        # Safely add missing seat-capacity columns
        train_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(trains)"
            ).fetchall()
        }

        for column in ("sleeper_seats", "ac3_seats", "ac2_seats"):
            if column not in train_columns:
                connection.execute(
                    f"ALTER TABLE trains ADD COLUMN {column} "
                    "INTEGER NOT NULL DEFAULT 0"
                )

        # Search history
        connection.execute("""
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                destination TEXT NOT NULL,
                journey_date TEXT NOT NULL,
                searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_search_history_user
            ON search_history(user_id, searched_at)
        """)

        # Seat inventory by train, date, and class
        connection.execute("""
            CREATE TABLE IF NOT EXISTS seat_availability (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                train_number TEXT NOT NULL,
                journey_date TEXT NOT NULL,
                class_code TEXT NOT NULL,
                total_seats INTEGER NOT NULL,
                booked_seats INTEGER NOT NULL DEFAULT 0,
                UNIQUE (train_number, journey_date, class_code),
                FOREIGN KEY (train_number)
                    REFERENCES trains(train_number),
                CHECK (total_seats >= 0),
                CHECK (booked_seats >= 0),
                CHECK (booked_seats <= total_seats)
            )
        """)

        # Bookings
        connection.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_reference TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                train_number TEXT NOT NULL,
                journey_date TEXT NOT NULL,
                class_code TEXT NOT NULL,
                passenger_count INTEGER NOT NULL,
                passenger_names TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'CONFIRMED',
                booked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (train_number)
                    REFERENCES trains(train_number),
                CHECK (passenger_count BETWEEN 1 AND 6),
                CHECK (status IN ('CONFIRMED', 'CANCELLED'))
            )
        """)

        # Insert demo trains only if the table is empty
        train_count = connection.execute(
            "SELECT COUNT(*) FROM trains"
        ).fetchone()[0]

        if train_count == 0:
            sample_trains = [
                ("12022", "Jan Shatabdi Express",
                 "Durgapur", "Howrah", "06:00", "09:10"),
                ("13012", "Howrah Intercity Express",
                 "Durgapur", "Howrah", "08:30", "11:45"),
                ("12322", "Kolkata Mail",
                 "Howrah", "New Delhi", "19:50", "10:00"),
                ("12382", "Poorva Express",
                 "Howrah", "New Delhi", "08:15", "06:00"),
                ("13011", "Intercity Express",
                 "Howrah", "Durgapur", "17:00", "20:15"),
                ("12361", "Asansol Express",
                 "Durgapur", "Asansol", "10:00", "11:10"),
                ("13019", "Bagh Express",
                 "Howrah", "Patna", "21:45", "09:30"),
            ]

            connection.executemany("""
                INSERT INTO trains (
                    train_number, name, source, destination,
                    departure_time, arrival_time
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, sample_trains)


# Find a user by their ID when Flask restores a login session
@login_manager.user_loader
def load_user(user_id):
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

    if row is None:
        return None

    return User(
        row["id"],
        row["name"],
        row["email"],
        row["password_hash"]
    )


# Homepage
@app.route("/")
def home():
    return render_template("index.html")


# Registration
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please fill in every field.", "error")
            return render_template("register.html")

        if len(name) > 100 or len(email) > 254:
            flash("Name or email is too long.", "error")
            return render_template("register.html")

        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            flash("Please enter a valid email address.", "error")
            return render_template("register.html")

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return render_template("register.html")

        password_hash = generate_password_hash(password)

        try:
            with get_db_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO users (name, email, password_hash)
                    VALUES (?, ?, ?)
                    """,
                    (name, email, password_hash)
                )
        except sqlite3.IntegrityError:
            flash("An account with this email already exists.", "error")
            return render_template("register.html")

        flash("Account created! You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


# Login
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with get_db_connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,)
            ).fetchone()

        if row is not None and check_password_hash(
            row["password_hash"], password
        ):
            user = User(
                row["id"],
                row["name"],
                row["email"],
                row["password_hash"]
            )
            login_user(user)
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


# Private dashboard
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", user=current_user)


# Logout must be a POST request
@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have logged out successfully.", "success")
    return redirect(url_for("home"))


# Prepare the database when the application starts
init_db()

# Train search page
@app.route("/search", methods=["GET", "POST"])
@login_required
def search_trains():
    results = []
    searched = False

    source = ""
    destination = ""
    journey_date = ""

    if request.method == "POST":
        source = request.form.get("source", "").strip()
        destination = request.form.get("destination", "").strip()
        journey_date = request.form.get("journey_date", "").strip()

        # Basic validation
        if not source or not destination or not journey_date:
            flash("Please fill in all search fields.", "error")

        elif len(source) > 100 or len(destination) > 100:
            flash("Station names are too long.", "error")

        elif source.casefold() == destination.casefold():
            flash("Source and destination must be different.", "error")

        else:
            try:
                parsed_date = date.fromisoformat(journey_date)
            except ValueError:
                flash("Please select a valid journey date.", "error")
            else:
                if parsed_date < date.today():
                    flash("Journey date cannot be in the past.", "error")
                else:
                    searched = True

                    with get_db_connection() as connection:
                        # Save the search for the logged-in user.
                        # Never accept user_id from the browser.
                        connection.execute("""
                            INSERT INTO search_history (
                                user_id, source, destination, journey_date
                            )
                            VALUES (?, ?, ?, ?)
                        """, (
                            current_user.id,
                            source,
                            destination,
                            journey_date
                        ))

                        # Find direct trains matching the route
                        results = connection.execute("""
                            SELECT train_number, name, source, destination,
                                   departure_time, arrival_time
                            FROM trains
                            WHERE source = ? COLLATE NOCASE
                              AND destination = ? COLLATE NOCASE
                            ORDER BY departure_time
                        """, (source, destination)).fetchall()

                    if not results:
                        flash(
                            "No demo trains found for this route. "
                            "Try another station pair.",
                            "info"
                        )

    return render_template(
        "train_search.html",
        results=results,
        searched=searched,
        source=source,
        destination=destination,
        journey_date=journey_date,
        today=date.today().isoformat()
    )


# Personal search history
@app.route("/search-history")
@login_required
def search_history():
    with get_db_connection() as connection:
        searches = connection.execute("""
            SELECT source, destination, journey_date, searched_at
            FROM search_history
            WHERE user_id = ?
            ORDER BY searched_at DESC, id DESC
        """, (current_user.id,)).fetchall()

    return render_template(
        "search_history.html",
        searches=searches
    )

@app.route("/train/<train_number>")
@login_required
def train_details(train_number):
    journey_date = request.args.get("date", "")

    # Validate train number and journey date
    if not re.fullmatch(r"\d{4,6}", train_number):
        flash("Invalid train number.", "error")
        return redirect(url_for("search_trains"))

    try:
        parsed_date = date.fromisoformat(journey_date)
    except ValueError:
        flash("Please select a valid journey date.", "error")
        return redirect(url_for("search_trains"))

    if parsed_date < date.today():
        flash("Journey date cannot be in the past.", "error")
        return redirect(url_for("search_trains"))

    classes = [
    {"code": "SL", "name": "Sleeper"},
    {"code": "3A", "name": "AC 3 Tier"},
    {"code": "2A", "name": "AC 2 Tier"},
    ]

    with get_db_connection() as connection:
        train = connection.execute("""
            SELECT train_number, name, source, destination,
                   departure_time, arrival_time
            FROM trains
            WHERE train_number = ?
        """, (train_number,)).fetchone()

        if train is None:
            flash("Train not found.", "error")
            return redirect(url_for("search_trains"))

        # Initialize demo inventory once per train/date/class.
        # Existing inventory is never reset on subsequent visits.
        capacity_by_class = {
            "SL": train["sleeper_seats"],
            "3A": train["ac3_seats"],
            "2A": train["ac2_seats"],
        }

        for travel_class in classes:
            connection.execute("""
                INSERT OR IGNORE INTO seat_availability (
                    train_number, journey_date, class_code,
                    total_seats, booked_seats
                )
                VALUES (?, ?, ?, ?, 0)
            """, (
                train_number,
                journey_date,
                travel_class["code"],
                capacity_by_class[travel_class["code"]],
            ))

        availability = connection.execute("""
            SELECT class_code, total_seats, booked_seats,
                   total_seats - booked_seats AS available_seats
            FROM seat_availability
            WHERE train_number = ? AND journey_date = ?
            ORDER BY CASE class_code
                WHEN 'SL' THEN 1
                WHEN '3A' THEN 2
                WHEN '2A' THEN 3
                ELSE 4
            END
        """, (train_number, journey_date)).fetchall()

    return render_template(
        "train_details.html",
        train=train,
        journey_date=journey_date,
        availability=availability
    )

@app.route("/book/<train_number>", methods=["GET", "POST"])
@login_required
def book_ticket(train_number):
    journey_date = request.args.get(
        "date", date.today().isoformat()
    )

    try:
        parsed_date = date.fromisoformat(journey_date)
    except ValueError:
        flash("Invalid journey date.", "error")
        return redirect(url_for("search_trains"))

    if parsed_date < date.today():
        flash("Journey date cannot be in the past.", "error")
        return redirect(url_for("search_trains"))

    classes = {
        "SL": "Sleeper",
        "3A": "AC 3 Tier",
        "2A": "AC 2 Tier",
    }

    with get_db_connection() as connection:
        train = connection.execute("""
            SELECT train_number, name, source, destination,
                   departure_time, arrival_time
            FROM trains
            WHERE train_number = ?
        """, (train_number,)).fetchone()

    if train is None:
        flash("Train not found.", "error")
        return redirect(url_for("search_trains"))

    if request.method == "GET":
        return render_template(
            "book_ticket.html",
            train=train,
            journey_date=journey_date,
            classes=classes,
        )

    class_code = request.form.get("class_code", "")
    passenger_names = [
        name.strip()
        for name in request.form.getlist("passenger_names")
        if name.strip()
    ]

    if class_code not in classes:
        flash("Please select a valid travel class.", "error")
        return redirect(url_for(
            "book_ticket",
            train_number=train_number,
            date=journey_date,
        ))

    if not 1 <= len(passenger_names) <= 6:
        flash("Enter between 1 and 6 passenger names.", "error")
        return redirect(url_for(
            "book_ticket",
            train_number=train_number,
            date=journey_date,
        ))

    if any(len(name) > 100 for name in passenger_names):
        flash("Each passenger name must be 100 characters or fewer.", "error")
        return redirect(url_for(
            "book_ticket",
            train_number=train_number,
            date=journey_date,
        ))

    booking_reference = "RE" + secrets.token_hex(5).upper()
    names_to_save = "\n".join(passenger_names)

    try:
        with get_db_connection() as connection:
            # Lock the database for this booking transaction.
            connection.execute("BEGIN IMMEDIATE")

            seats = connection.execute("""
                SELECT total_seats, booked_seats
                FROM seat_availability
                WHERE train_number = ?
                  AND journey_date = ?
                  AND class_code = ?
            """, (
                train_number,
                journey_date,
                class_code,
            )).fetchone()

            if seats is None:
                connection.rollback()
                flash(
                    "Seat information is not initialized. "
                    "Open train details first.",
                    "error",
                )
                return redirect(url_for(
                    "train_details",
                    train_number=train_number,
                    date=journey_date,
                ))

            available = seats["total_seats"] - seats["booked_seats"]

            if available < len(passenger_names):
                connection.rollback()
                flash(
                    f"Only {available} seats remain in "
                    f"{classes[class_code]}.",
                    "error",
                )
                return redirect(url_for(
                    "book_ticket",
                    train_number=train_number,
                    date=journey_date,
                ))

            connection.execute("""
                UPDATE seat_availability
                SET booked_seats = booked_seats + ?
                WHERE train_number = ?
                  AND journey_date = ?
                  AND class_code = ?
            """, (
                len(passenger_names),
                train_number,
                journey_date,
                class_code,
            ))

            connection.execute("""
                INSERT INTO bookings (
                    booking_reference,
                    user_id,
                    train_number,
                    journey_date,
                    class_code,
                    passenger_count,
                    passenger_names
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                booking_reference,
                current_user.id,
                train_number,
                journey_date,
                class_code,
                len(passenger_names),
                names_to_save,
            ))

    except sqlite3.Error:
        app.logger.exception("Ticket booking failed")
        flash(
            "We could not complete your booking. Please try again.",
            "error",
        )
        return redirect(url_for(
            "book_ticket",
            train_number=train_number,
            date=journey_date,
        ))

    return render_template(
        "booking_success.html",
        booking_reference=booking_reference,
        train=train,
        journey_date=journey_date,
        class_name=classes[class_code],
        passenger_names=passenger_names,
    )

@app.route("/my-bookings")
@login_required
def my_bookings():
    with get_db_connection() as connection:
        bookings = connection.execute("""
            SELECT
                b.booking_reference,
                b.train_number,
                t.name AS train_name,
                t.source,
                t.destination,
                b.journey_date,
                b.class_code,
                b.passenger_count,
                b.passenger_names,
                b.status,
                b.booked_at
            FROM bookings AS b
            JOIN trains AS t
                ON t.train_number = b.train_number
            WHERE b.user_id = ?
            ORDER BY b.id DESC
        """, (current_user.id,)).fetchall()

    return render_template(
        "my_bookings.html",
        bookings=bookings
    )

@app.route("/booking/<booking_reference>/ticket")
@login_required
def download_ticket(booking_reference):
    with get_db_connection() as connection:
        booking = connection.execute("""
            SELECT
                b.booking_reference,
                b.train_number,
                t.name AS train_name,
                t.source,
                t.destination,
                t.departure_time,
                t.arrival_time,
                b.journey_date,
                b.class_code,
                b.passenger_count,
                b.passenger_names,
                b.status,
                b.booked_at
            FROM bookings AS b
            JOIN trains AS t
                ON t.train_number = b.train_number
            WHERE b.booking_reference = ?
              AND b.user_id = ?
        """, (
            booking_reference,
            current_user.id
        )).fetchone()

    if booking is None:
        abort(404)

    pdf_buffer = BytesIO()

    document = SimpleDocTemplate(
        pdf_buffer,
        pagesize=A4,
        title="RailEase Demo Ticket"
    )

    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("RailEase", styles["Title"]))
    story.append(Paragraph("DEMO BOOKING TICKET", styles["Heading2"]))
    story.append(Spacer(1, 16))

    details = [
        ("Booking reference", booking["booking_reference"]),
        ("Booking status", booking["status"]),
        ("Train", booking["train_name"]),
        ("Train number", booking["train_number"]),
        ("From", booking["source"]),
        ("To", booking["destination"]),
        ("Departure", booking["departure_time"]),
        ("Arrival", booking["arrival_time"]),
        ("Journey date", booking["journey_date"]),
        ("Class", booking["class_code"]),
        ("Passenger count", booking["passenger_count"]),
    ]

    for label, value in details:
        story.append(Paragraph(
            f"<b>{escape(str(label))}:</b> "
            f"{escape(str(value))}",
            styles["Normal"]
        ))
        story.append(Spacer(1, 8))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "<b>Passengers</b>",
        styles["Heading3"]
    ))

    for name in booking["passenger_names"].splitlines():
        story.append(Paragraph(
            escape(name),
            styles["Normal"]
        ))

    story.append(Spacer(1, 20))
    story.append(Paragraph(
        "This PDF is a demonstration document from RailEase. "
        "It is not an official Indian Railways ticket or proof "
        "of a real reservation.",
        styles["Italic"]
    ))

    document.build(story)
    pdf_buffer.seek(0)

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=(
            f"RailEase-{booking['booking_reference']}.pdf"
        )
    )
   
@app.route(
    "/booking/<booking_reference>/cancel",
    methods=["POST"]
)
@login_required
def cancel_booking(booking_reference):
    try:
        with get_db_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")

            booking = connection.execute("""
                SELECT train_number, journey_date,
                       class_code, passenger_count, status
                FROM bookings
                WHERE booking_reference = ?
                  AND user_id = ?
            """, (
                booking_reference,
                current_user.id
            )).fetchone()

            if booking is None:
                connection.rollback()
                abort(404)

            if booking["status"] != "CONFIRMED":
                connection.rollback()
                flash(
                    "This booking is already cancelled.",
                    "error"
                )
                return redirect(url_for("my_bookings"))

            seats = connection.execute("""
                SELECT booked_seats
                FROM seat_availability
                WHERE train_number = ?
                  AND journey_date = ?
                  AND class_code = ?
            """, (
                booking["train_number"],
                booking["journey_date"],
                booking["class_code"]
            )).fetchone()

            if (
                seats is None
                or seats["booked_seats"] < booking["passenger_count"]
            ):
                connection.rollback()
                flash(
                    "Could not restore seats safely. "
                    "Please check the demo seat inventory.",
                    "error"
                )
                return redirect(url_for("my_bookings"))

            connection.execute("""
                UPDATE seat_availability
                SET booked_seats = booked_seats - ?
                WHERE train_number = ?
                  AND journey_date = ?
                  AND class_code = ?
            """, (
                booking["passenger_count"],
                booking["train_number"],
                booking["journey_date"],
                booking["class_code"]
            ))

            connection.execute("""
                UPDATE bookings
                SET status = 'CANCELLED'
                WHERE booking_reference = ?
                  AND user_id = ?
                  AND status = 'CONFIRMED'
            """, (
                booking_reference,
                current_user.id
            ))

    except sqlite3.Error:
        app.logger.exception("Booking cancellation failed")
        flash("Could not cancel the booking.", "error")
        return redirect(url_for("my_bookings"))

    flash("Demo booking cancelled successfully.", "success")
    return redirect(url_for("my_bookings"))    



def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        with get_db_connection() as connection:
            user = connection.execute(
                "SELECT role FROM users WHERE id = ?",
                (current_user.id,)
            ).fetchone()

        if user is None or user["role"] != "admin":
            abort(403)

        return view(*args, **kwargs)

    return wrapped_view


@app.route("/admin")
@admin_required
def admin_dashboard():
    with get_db_connection() as connection:
        total_users = connection.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        total_trains = connection.execute(
            "SELECT COUNT(*) FROM trains"
        ).fetchone()[0]

        total_bookings = connection.execute(
            "SELECT COUNT(*) FROM bookings"
        ).fetchone()[0]

        bookings = connection.execute("""
            SELECT b.booking_reference, b.journey_date,
                   b.status, b.passenger_count,
                   u.name AS user_name,
                   t.train_number, t.name AS train_name
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            JOIN trains t ON b.train_number = t.train_number
            ORDER BY b.booked_at DESC
            LIMIT 10
        """).fetchall()

    return render_template(
        "admin_dashboard.html",
        total_users=total_users,
        total_trains=total_trains,
        total_bookings=total_bookings,
        bookings=bookings
    )
    
@app.route("/admin/trains/new", methods=["GET", "POST"])
@admin_required
def admin_train_new():
    if request.method == "POST":
        train_number = request.form.get("train_number", "").strip()
        name = request.form.get("name", "").strip()
        source = request.form.get("source", "").strip()
        destination = request.form.get("destination", "").strip()
        departure_time = request.form.get("departure_time", "").strip()
        arrival_time = request.form.get("arrival_time", "").strip()

        if not all([
            train_number, name, source, destination,
            departure_time, arrival_time
        ]):
            flash("Please fill in every field.", "error")
            return render_template("admin_train_form.html", train=None)

        if source.casefold() == destination.casefold():
            flash("Source and destination must be different.", "error")
            return render_template("admin_train_form.html", train=None)

        try:
            datetime.strptime(departure_time, "%H:%M")
            datetime.strptime(arrival_time, "%H:%M")
        except ValueError:
            flash("Enter valid departure and arrival times.", "error")
            return render_template("admin_train_form.html", train=None)

        try:
            sleeper_seats = int(request.form.get("sleeper_seats", "0"))
            ac3_seats = int(request.form.get("ac3_seats", "0"))
            ac2_seats = int(request.form.get("ac2_seats", "0"))

            if min(sleeper_seats, ac3_seats, ac2_seats) < 0:
                raise ValueError

            if max(sleeper_seats, ac3_seats, ac2_seats) > 10000:
                raise ValueError

        except ValueError:
            flash("Seat capacities must be whole numbers from 0 to 10000.", "error")
            return render_template("admin_train_form.html", train=None)

        try:
            with get_db_connection() as connection:
                connection.execute("""
              INSERT INTO trains (
        train_number, name, source, destination,
        departure_time, arrival_time,
        sleeper_seats, ac3_seats, ac2_seats
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
    train_number, name, source, destination,
    departure_time, arrival_time,
    sleeper_seats, ac3_seats, ac2_seats
     ))

            flash("Train added successfully!", "success")
            return redirect(url_for("admin_trains"))

        except sqlite3.IntegrityError:
            flash(
                "That train number already exists. Use a unique number.",
                "error"
            )

    return render_template("admin_train_form.html", train=None)   
    
@app.route("/admin/trains")
@admin_required
def admin_trains():
    with get_db_connection() as connection:
        trains = connection.execute("""
            SELECT id, train_number, name, source, destination,
                   departure_time, arrival_time
            FROM trains
            ORDER BY train_number
        """).fetchall()

    return render_template("admin_trains.html", trains=trains)

@app.route("/admin/trains/<int:train_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_train_edit(train_id):
    with get_db_connection() as connection:
        train = connection.execute(
            "SELECT * FROM trains WHERE id = ?",
            (train_id,)
        ).fetchone()

    try:
                sleeper_seats = int(request.form.get("sleeper_seats", "0"))
                ac3_seats = int(request.form.get("ac3_seats", "0"))
                ac2_seats = int(request.form.get("ac2_seats", "0"))

                if min(sleeper_seats, ac3_seats, ac2_seats) < 0:
                    raise ValueError

                if max(sleeper_seats, ac3_seats, ac2_seats) > 10000:
                    raise ValueError

    except ValueError:
                flash("Seat capacities must be whole numbers from 0 to 10000.", "error")
                return render_template("admin_train_form.html", train=train)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        source = request.form.get("source", "").strip()
        destination = request.form.get("destination", "").strip()
        departure_time = request.form.get("departure_time", "").strip()
        arrival_time = request.form.get("arrival_time", "").strip()

        if not all([name, source, destination, departure_time, arrival_time]):
            flash("Please fill in every field.", "error")
            return render_template("admin_train_form.html", train=train)

        if source.casefold() == destination.casefold():
            flash("Source and destination must be different.", "error")
            return render_template("admin_train_form.html", train=train)

        try:
            datetime.strptime(departure_time, "%H:%M")
            datetime.strptime(arrival_time, "%H:%M")
        except ValueError:
            flash("Enter valid departure and arrival times.", "error")
            return render_template("admin_train_form.html", train=train)

        with get_db_connection() as connection:
            connection.execute("""
    UPDATE trains
    SET name = ?, source = ?, destination = ?,
        departure_time = ?, arrival_time = ?,
        sleeper_seats = ?, ac3_seats = ?, ac2_seats = ?
    WHERE id = ?""", (
    name, source, destination,
    departure_time, arrival_time,
    sleeper_seats, ac3_seats, ac2_seats,
    train_id
    ))

        flash("Train updated successfully!", "success")
        return redirect(url_for("admin_trains"))

    return render_template("admin_train_form.html", train=train)

@app.route("/admin/trains/<int:train_id>/delete", methods=["POST"])
@admin_required
def admin_train_delete(train_id):
    print("DELETE ROUTE REACHED. Train ID:", train_id)

    with get_db_connection() as connection:
        train = connection.execute(
            "SELECT train_number FROM trains WHERE id = ?",
            (train_id,)
        ).fetchone()

        print("TRAIN FOUND:", dict(train) if train else None)

        if train is None:
            abort(404)

        booking = connection.execute(
            "SELECT 1 FROM bookings WHERE train_number = ? LIMIT 1",
            (train["train_number"],)
        ).fetchone()

        print("BOOKING FOUND:", booking is not None)

        if booking:
            flash(
                "This train has booking history and cannot be deleted.",
                "error"
            )
            return redirect(url_for("admin_trains"))

        connection.execute(
            "DELETE FROM seat_availability WHERE train_number = ?",
            (train["train_number"],)
        )

        result = connection.execute(
            "DELETE FROM trains WHERE id = ?",
            (train_id,)
        )

        print("ROWS DELETED:", result.rowcount)

    flash("Train deleted successfully.", "success")
    return redirect(url_for("admin_trains"))


if __name__ == "__main__":
    app.run(debug=True)

