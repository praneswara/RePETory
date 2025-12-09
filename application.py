import os
import io
import vonage
import random
import time
from io import BytesIO
import datetime as dt
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, abort, send_file, jsonify, make_response
)
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from functools import wraps
from passlib.hash import bcrypt

# ---------------- ReportLab ----------------

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

try:
    pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))
except:
    print("Korean fonts unavailable on AWS")



def generate_pdf(title, lines):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)

    # --- Title font ---
    try:
        p.setFont("HYGothic-Medium", 16)
    except:
        p.setFont("Helvetica-Bold", 16)

    y = 750
    p.drawString(50, y, str(title))

    # --- Body font ---
    try:
        p.setFont("HYGothic-Medium", 12)
    except:
        p.setFont("Helvetica", 12)

    y -= 40

    # --- Content Lines ---
    for line in lines:
        p.drawString(50, y, str(line))
        y -= 20

        # New page if space ends
        if y < 50:
            p.showPage()
            try:
                p.setFont("HYGothic-Medium", 12)
            except:
                p.setFont("Helvetica", 12)
            y = 750

    # Finalize PDF
    p.save()
    buffer.seek(0)
    return buffer

#----------------CONFIGURATIONS-------------------
# ------------------ LOAD ENV ------------------

VONAGE_API_KEY = os.getenv("VONAGE_API_KEY")
VONAGE_API_SECRET = os.getenv("VONAGE_API_SECRET")

# ------------------ FLASK APP ------------------
application = Flask(__name__, template_folder="templates")

from werkzeug.middleware.proxy_fix import ProxyFix
application.wsgi_app = ProxyFix(application.wsgi_app, x_proto=1, x_host=1)

@application.route("/health")
def health():
    return jsonify(status="ok"), 200


application.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")
application.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "devjwt")
application.config["JWT_ACCESS_TOKEN_EXPIRES"] = dt.timedelta(hours=24)
application.config["JWT_ALGORITHM"] = "HS256"
application.config["SESSION_COOKIE_SECURE"] = True
application.config["SESSION_COOKIE_SAMESITE"] = "None"
application.config["SESSION_COOKIE_HTTPONLY"] = True




ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# Enable CORS + JWT
CORS(application, supports_credentials=True)
jwt = JWTManager(application)

# ------------------ VONAGE CLIENT ------------------
if VONAGE_API_KEY and VONAGE_API_SECRET:
    client = vonage.Client(key=VONAGE_API_KEY, secret=VONAGE_API_SECRET)
    sms = vonage.Sms(client)
else:
    client = None
    sms = None
    print("⚠️ WARNING: Vonage API key/secret missing — OTP will NOT work.")




# ----------------- DB CONNECTION------------------

# def get_db():
#     conn = psycopg2.connect(
#         host=os.getenv("DB_HOST"),
#         database=os.getenv("DB_NAME"),
#         user=os.getenv("DB_USER"),
#         password=os.getenv("DB_PASSWORD"),
#         port=os.getenv("DB_PORT", "5432"),
#         cursor_factory=RealDictCursor,
#         sslmode="require"
#     )
#     return conn

def get_db():
    uri = os.getenv("DATABASE_URL")
    if not uri:
        raise ValueError("DATABASE_URL is not set")
    # psycopg2 connection
    conn = psycopg2.connect(uri, cursor_factory=RealDictCursor)
    return conn


def serialize_row(row):
    if not row:
        return row
    for k, v in list(row.items()):
        if isinstance(v, (dt.datetime, dt.date)):
            row[k] = v.isoformat()
    return row

#----------------------generate user id------------------------------------

def generate_user_id(name, mobile):
    # Take first 4 letters of name (lowercase)
    prefix = name[:4].lower()
    # Take last 4 digits of mobile
    suffix = mobile[-4:]
    return f"{prefix}_{suffix}"

# ---------------- ADMIN AUTH DECORATOR -------------
# -
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated_function

#------------------------FORCE HTTPS---------------------

# @application.before_request
# def redirect_https():
#     # Skip for AWS health check & internal service requests
#     if request.path in ('/health', '/api', '/api/', '/api/'):
#         return
    
#     # AWS forwards a header instead of request.is_secure()
#     if request.headers.get('X-Forwarded-Proto', 'http') != 'https':
#         return redirect(request.url.replace("http://", "https://"), code=301)

        
#-------------------------DOMAIN INDEX----------------------------

@application.route('/')
def home():
    return render_template("admin/index.html")

# ---------------------- ADMIN LOGIN ------------------------------

@application.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # Compare with env values
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid credentials", "error")

    return render_template("admin/login.html")


# ---------------------- ADMIN DASHBOARD ------------------------------

@application.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total_users FROM users;")
                total_users = cur.fetchone()["total_users"]

                cur.execute("SELECT COUNT(*) AS total_machines FROM machines;")
                total_machines = cur.fetchone()["total_machines"]

                cur.execute("SELECT COUNT(*) AS total_transactions FROM transactions;")
                total_transactions = cur.fetchone()["total_transactions"]
    except Exception as e:
        application.logger.error(f"Dashboard DB error: {e}")
        total_users = total_machines = total_transactions = 0

    stats = {
        "total_users": total_users,
        "total_machines": total_machines,
        "total_transactions": total_transactions,
    }
    return render_template("admin/dashboard.html", stats=stats)


# ------------------------- ADMIN LIST OF USERS VIEW --------------------------------

@application.route("/admin/users")
@admin_required
def admin_users():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_id, name, mobile, points, bottles, created_at
                    FROM users
                    ORDER BY created_at DESC;
                """)
                users = [serialize_row(u) for u in cur.fetchall()]
    except Exception as e:
        application.logger.error(f"/admin/users DB error: {e}")
        users = []

    return render_template("admin/users.html", users=users)


@application.route("/admin/users/report", methods=["POST"])
@admin_required
def export_filtered_users():
    # Register Korean font safely (avoid crash on EB)
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    except Exception as e:
        application.logger.warning(f"Could not register Korean font: {e}")

    payload = request.get_json()
    if not payload or "data" not in payload:
        return jsonify({"error": "No data"}), 400

    data = payload["data"]

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)

    styles = getSampleStyleSheet()

    # Try Korean font, fallback if missing
    try:
        styles["Normal"].fontName = "HYSMyeongJo-Medium"
        styles["Heading1"].fontName = "HYSMyeongJo-Medium"
    except:
        styles["Normal"].fontName = "Helvetica"
        styles["Heading1"].fontName = "Helvetica-Bold"

    elements = []
    elements.append(Paragraph("사용자 보고서", styles["Heading1"]))
    elements.append(Spacer(1, 12))

    # Table header
    table_data = [["ID", "이름", "전화번호", "포인트", "병"]]

    # Rows
    for u in data:
        table_data.append([
            u.get("user_id", ""),
            u.get("name", ""),
            u.get("mobile", ""),
            u.get("points", ""),
            u.get("bottles", "")
        ])

    # Build table
    table = Table(table_data, repeatRows=1)
    try:
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "HYSMyeongJo-Medium"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.7, colors.black)
        ]))
    except:
        # fallback for systems without Korean fonts (AWS Beanstalk)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.7, colors.black)
        ]))

    elements.append(table)

    try:
        doc.build(elements)
    except Exception as e:
        application.logger.error(f"PDF build error (users report): {e}")
        return jsonify({"error": "Failed to generate PDF"}), 500

    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="filtered_users.pdf"
    )


# -------------------------- ADMIN INDIVIDUAL USER VIEW ----------------------------------

@application.route("/admin/users/<string:user_id>")
@admin_required
def admin_user_detail(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Fetch user
                cur.execute("""
                    SELECT user_id, name, mobile, points, bottles, created_at
                    FROM users
                    WHERE user_id=%s;
                """, (user_id,))
                user = cur.fetchone()

                if not user:
                    abort(404)

                # Fetch transactions
                cur.execute("""
                    SELECT id, type, points, bottles, machine_id, brand_id, created_at
                    FROM transactions
                    WHERE user_id=%s
                    ORDER BY created_at DESC;
                """, (user_id,))
                transactions = [serialize_row(t) for t in cur.fetchall()]

                # Convert datetime to ISO format
                user = serialize_row(user)

    except Exception as e:
        application.logger.error(f"/admin/users/{user_id} error: {e}")
        abort(500)

    return render_template("admin/user_detail.html", user=user, transactions=transactions)

@application.route("/admin/users/<string:user_id>/report", methods=["POST"])
@admin_required
def export_individual_user_report(user_id):
    # Safely register Korean font (avoid crash on EB)
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    except Exception as e:
        application.logger.warning(f"Could not register Korean font: {e}")

    # Fetch user
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE user_id=%s;", (user_id,))
                user = cur.fetchone()
                if not user:
                    abort(404)
                user = serialize_row(user)
    except Exception as e:
        application.logger.error(f"/admin/users/{user_id}/report DB error: {e}")
        abort(500)

    # JSON payload
    payload = request.get_json() or {}
    data = payload.get("data", [])

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)

    # Styles with font fallback
    styles = getSampleStyleSheet()
    try:
        styles["Normal"].fontName = "HYSMyeongJo-Medium"
        styles["Heading1"].fontName = "HYSMyeongJo-Medium"
    except:
        styles["Normal"].fontName = "Helvetica"
        styles["Heading1"].fontName = "Helvetica-Bold"

    elements = []
    elements.append(Paragraph("사용자 거래 보고서", styles["Heading1"]))
    elements.append(Spacer(1, 12))

    # User info block
    user_info = f"""
    사용자 ID: {user.get('user_id')}<br/>
    이름: {user.get('name')}<br/>
    전화번호: {user.get('mobile')}<br/>
    포인트: {user.get('points')}<br/>
    병 수: {user.get('bottles')}<br/>
    생성 날짜: {user.get('created_at')}
    """

    elements.append(Paragraph(user_info, styles["Normal"]))
    elements.append(Spacer(1, 15))

    # Table header
    table_data = [["ID", "유형", "포인트", "병", "머신 ID", "날짜"]]

    # Table rows
    for t in data:
        table_data.append([
            t.get("id", ""),
            t.get("type", ""),
            t.get("points", ""),
            t.get("bottles", ""),
            t.get("machine_id", ""),
            t.get("created_at", "")
        ])

    table = Table(table_data, repeatRows=1)

    # Styling with fallback
    try:
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "HYSMyeongJo-Medium"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
        ]))
    except:
        # fallback if Korean fonts unavailable
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
        ]))

    elements.append(table)

    # Build PDF
    try:
        doc.build(elements)
    except Exception as e:
        application.logger.error(f"PDF build error (user {user_id} report): {e}")
        return jsonify({"error": "Failed to generate PDF"}), 500

    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{user_id}_filtered_report.pdf"
    )


# ------------------------- ADMIN LIST OF MACHINES VIEW --------------------------------

@application.route("/admin/machines")
@admin_required
def admin_machines():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, machine_id, name, city, lat, lng,
                           current_bottles, max_capacity, total_bottles,
                           is_full, last_emptied, created_at
                    FROM machines
                    ORDER BY id;
                """)
                machines = [serialize_row(m) for m in cur.fetchall()]
    except Exception as e:
        application.logger.error(f"/admin/machines DB error: {e}")
        machines = []

    return render_template("admin/machines.html", machines=machines)

@application.route("/admin/machines/report", methods=["POST"])
@admin_required
def export_filtered_machines():
    # Safely register Korean font (EB does NOT have these fonts)
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    except Exception as e:
        application.logger.warning(f"Korean font registration failed: {e}")

    # Validate JSON payload
    payload = request.get_json()
    if not payload or "data" not in payload:
        return jsonify({"error": "No data provided"}), 400

    data = payload["data"]

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20,
        rightMargin=20
    )

    # Styles with Korean fallback
    styles = getSampleStyleSheet()
    try:
        styles["Normal"].fontName = "HYSMyeongJo-Medium"
        styles["Heading1"].fontName = "HYSMyeongJo-Medium"
    except:
        styles["Normal"].fontName = "Helvetica"
        styles["Heading1"].fontName = "Helvetica-Bold"

    # Heading formatting (Korean-safe)
    styles["Heading1"].bold = False
    styles["Heading1"].italic = False
    styles["Heading1"].fontSize = 16
    styles["Heading1"].leading = 20

    elements = []
    elements.append(Paragraph("기계 보고서 (필터링됨)", styles["Heading1"]))
    elements.append(Spacer(1, 12))

    # Table headers
    header = [
        "Machine ID", "Name", "City", "Current",
        "Max", "Total", "Full?", "Last Emptied"
    ]

    table_data = [header]

    # Table rows
    for m in data:
        table_data.append([
            m.get("machine_id", ""),
            m.get("name", ""),
            m.get("city", ""),
            m.get("current_bottles", ""),
            m.get("max_capacity", ""),
            m.get("total_bottles", ""),
            m.get("is_full", ""),
            m.get("last_emptied", ""),
        ])

    # Column widths
    col_widths = [60, 70, 60, 45, 45, 45, 40, 135]

    table = Table(table_data, colWidths=col_widths, repeatRows=1)

    # Apply Korean font style OR fallback if not available
    try:
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "HYSMyeongJo-Medium"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
    except:
        # FALLBACK: use Helvetica if EB doesn't have Korean fonts
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))

    elements.append(table)

    # Build PDF
    try:
        doc.build(elements)
    except Exception as e:
        application.logger.error(f"PDF building failed (machine report): {e}")
        return jsonify({"error": "PDF generation failed"}), 500

    buffer.seek(0)

    return send_file(
        buffer,
        download_name="filtered_machines_report.pdf",
        as_attachment=True,
        mimetype="application/pdf"
    )


# ---------------------- ADMIN INDIVIDUAL MACHINE VIEW --------------------------------

@application.route("/admin/machines/<string:machine_id>")
@admin_required
def admin_machine_detail(machine_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:

                # Fetch machine
                cur.execute("""
                    SELECT id, machine_id, name, city, lat, lng,
                           current_bottles, max_capacity, total_bottles,
                           is_full, last_emptied, created_at
                    FROM machines
                    WHERE machine_id=%s;
                """, (machine_id,))
                machine = cur.fetchone()

                if not machine:
                    abort(404)

                # Fetch machine transactions
                cur.execute("""
                    SELECT id, user_id, type, points, bottles,
                           machine_id, brand_id, created_at
                    FROM transactions
                    WHERE machine_id=%s
                    ORDER BY created_at DESC;
                """, (machine["machine_id"],))
                transactions = [serialize_row(t) for t in cur.fetchall()]

                machine = serialize_row(machine)

    except Exception as e:
        application.logger.error(f"/admin/machines/{machine_id} error: {e}")
        abort(500)

    # Safe fill percentage
    try:
        current = machine.get("current_bottles") or 0
        max_cap = machine.get("max_capacity") or 0
        fill_percentage = (current / max_cap * 100) if max_cap else 0
    except:
        fill_percentage = 0

    return render_template(
        "admin/machine_detail.html",
        machine=machine,
        transactions=transactions,
        fill_percentage=fill_percentage
    )

@application.route("/admin/machines/<string:machine_id>/report-filtered", methods=["POST"])
@admin_required
def admin_machine_filtered_pdf(machine_id):
    # Register Korean font safely (Elastic Beanstalk usually lacks CID fonts)
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    except Exception as e:
        application.logger.warning(f"Korean font registration failed: {e}")

    # Extract JSON payload
    payload = request.get_json() or {}
    machine = payload.get("machine", {})
    transactions = payload.get("transactions", [])

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)

    styles = getSampleStyleSheet()

    # Apply Korean font if available, fallback otherwise
    try:
        styles["Normal"].fontName = "HYSMyeongJo-Medium"
        styles["Heading1"].fontName = "HYSMyeongJo-Medium"
    except:
        styles["Normal"].fontName = "Helvetica"
        styles["Heading1"].fontName = "Helvetica-Bold"

    elements = []

    # Report Title
    elements.append(Paragraph("기계 상세 보고서 (Machine Detail Report)", styles["Heading1"]))
    elements.append(Spacer(1, 12))

    # Machine info table
    info_data = [
        ["Machine ID", machine.get("machine_id", "")],
        ["Name", machine.get("name", "")],
        ["City", machine.get("city", "")],
        ["Latitude", machine.get("lat", "")],
        ["Longitude", machine.get("lng", "")],
        ["Total Bottles", machine.get("total", "")],
        ["Current Capacity", f"{machine.get('current', '')} / {machine.get('max', '')}"],
        ["Is Full", machine.get("full", "")],
        ["Created At", machine.get("created_at", "")],
        ["Last Emptied", machine.get("last_emptied", "")]
    ]

    info_table = Table(info_data, colWidths=[120, 300])

    try:
        info_table.setStyle(TableStyle([
            ("FONTNAME", (0,0), (-1,-1), "HYSMyeongJo-Medium"),
            ("GRID", (0,0), (-1,-1), 0.5, colors.black),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("ALIGN", (0,0), (-1,-1), "LEFT"),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ]))
    except:
        # fallback style if Korean font fails
        info_table.setStyle(TableStyle([
            ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
            ("GRID", (0,0), (-1,-1), 0.5, colors.black),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("ALIGN", (0,0), (-1,-1), "LEFT"),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ]))

    elements.append(info_table)
    elements.append(Spacer(1, 20))

    # Transactions table
    table_data = [["ID", "User ID", "Type", "Points", "Bottles", "Date"]]

    for t in transactions:
        table_data.append([
            t.get("id", ""),
            t.get("user_id", ""),
            t.get("type", ""),
            t.get("points", ""),
            t.get("bottles", ""),
            t.get("created_at", "")
        ])

    trx_table = Table(table_data, repeatRows=1)

    try:
        trx_table.setStyle(TableStyle([
            ("FONTNAME", (0,0), (-1,-1), "HYSMyeongJo-Medium"),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ]))
    except:
        trx_table.setStyle(TableStyle([
            ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ]))

    elements.append(trx_table)

    try:
        doc.build(elements)
    except Exception as e:
        application.logger.error(f"PDF build failed for machine {machine_id}: {e}")
        return jsonify({"error": "PDF generation failed"}), 500

    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{machine_id}_filtered_report.pdf",
        mimetype="application/pdf"
    )


# -------------------------- ADMIN EMPTYING MACHINE ----------------------------------

@application.route("/admin/machine/<string:machine_id>/empty", methods=["POST"])
@admin_required
def admin_empty_machine(machine_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:

                # Fetch machine details
                cur.execute("""
                    SELECT name, current_bottles
                    FROM machines
                    WHERE machine_id = %s;
                """, (machine_id,))
                machine = cur.fetchone()

                if not machine:
                    abort(404)

                previous_count = machine.get("current_bottles") or 0

                # Empty the machine
                cur.execute("""
                    UPDATE machines
                    SET current_bottles = 0,
                        is_full = FALSE,
                        last_emptied = NOW()
                    WHERE machine_id = %s;
                """, (machine_id,))

                conn.commit()

    except Exception as e:
        application.logger.error(f"/admin/machine/{machine_id}/empty DB error: {e}")
        flash("Failed to empty machine. Please try again.", "danger")
        return redirect(url_for("admin_machine_detail", machine_id=machine_id))

    flash(
        f"Machine '{machine.get('name')}' emptied successfully! "
        f"Bottles collected: {previous_count}",
        "success"
    )
    return redirect(url_for("admin_machine_detail", machine_id=machine_id))


# -------------------------- ADMIN ADD NEW MACHINE ----------------------------------

@application.route("/admin/machines/add", methods=["GET", "POST"])
@admin_required
def admin_add_machine():
    if request.method == "POST":
        machine_id = request.form.get("machine_id", "").strip()
        name = request.form.get("name", "").strip()
        city = request.form.get("city", "").strip()
        lat = request.form.get("lat", type=float)
        lng = request.form.get("lng", type=float)
        max_capacity = request.form.get("max_capacity", type=int)

        # Basic validation (optional)
        if not machine_id or not name or not city:
            flash("Missing required fields.", "danger")
            return redirect(url_for("admin_add_machine"))

        try:
            with get_db() as conn:
                with conn.cursor() as cur:

                    # Check if machine ID already exists
                    cur.execute("SELECT 1 FROM machines WHERE machine_id = %s;", (machine_id,))
                    if cur.fetchone():
                        flash(f"Machine ID '{machine_id}' already exists.", "danger")
                        return redirect(url_for("admin_add_machine"))

                    # Insert machine
                    cur.execute("""
                        INSERT INTO machines (
                            machine_id, name, city, lat, lng, max_capacity,
                            current_bottles, total_bottles, is_full, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, 0, 0, FALSE, NOW());
                    """, (machine_id, name, city, lat, lng, max_capacity))

                    conn.commit()

        except Exception as e:
            application.logger.error(f"/admin/machines/add error: {e}")
            flash("Error adding machine. Please try again.", "danger")
            return redirect(url_for("admin_add_machine"))

        flash(f"Machine '{name}' added successfully!", "success")
        return redirect(url_for("admin_machines"))

    return render_template("admin/add_machine.html")


@application.route("/admin/transactions")
@admin_required
def admin_transactions():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, user_id, type, points, bottles,
                           machine_id, brand_id, created_at
                    FROM transactions
                    ORDER BY created_at DESC;
                """)
                transactions = [serialize_row(t) for t in cur.fetchall()]

    except Exception as e:
        application.logger.error(f"/admin/transactions DB error: {e}")
        transactions = []
        flash("Failed to load transactions.", "danger")

    return render_template("admin/transactions.html", transactions=transactions)


@application.route("/admin/transactions/report", methods=["POST"])
@admin_required
def export_filtered_transactions():
    # Safely register font (avoid crash if missing on EB)
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    except Exception as e:
        application.logger.warning(f"Could not register Korean font: {e}")

    payload = request.get_json()
    if not payload or "data" not in payload:
        return jsonify({"error": "No data provided"}), 400

    data = payload["data"]

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)

    # Setup styles
    styles = getSampleStyleSheet()

    # Use Korean font if available, else fallback
    try:
        styles["Normal"].fontName = "HYSMyeongJo-Medium"
        styles["Heading1"].fontName = "HYSMyeongJo-Medium"
    except:
        styles["Normal"].fontName = "Helvetica"
        styles["Heading1"].fontName = "Helvetica-Bold"

    elements = []
    elements.append(Paragraph("필터링된 거래 보고서", styles["Heading1"]))
    elements.append(Spacer(1, 12))

    # Table header
    table_data = [["ID", "사용자 ID", "유형", "포인트", "병", "머신 ID", "날짜"]]

    # Rows
    for t in data:
        table_data.append([
            t.get("id", ""),
            t.get("user_id", ""),
            t.get("type", ""),
            t.get("points", ""),
            t.get("bottles", ""),
            t.get("machine_id", ""),
            t.get("created_at", ""),
        ])

    # Build table
    table = Table(table_data, repeatRows=1)
    try:
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "HYSMyeongJo-Medium"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
        ]))
    except:
        # Fallback if font unavailable
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006d71")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
        ]))

    elements.append(table)

    try:
        doc.build(elements)
    except Exception as e:
        application.logger.error(f"PDF build error: {e}")
        return jsonify({"error": "Failed to generate PDF"}), 500

    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="filtered_transactions.pdf"
    )


# -------------------------- ADMIN LOGOUT ----------------------------------

@application.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))

# -------------------------- HELPERS ----------------------------------

def get_user_or_404(user_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
            user = cur.fetchone()

    if not user:
        abort(404, description="User not found")

    return user


# ------------------------- BASE ROUTE ---------------------------------

@application.route("/api", methods=["GET"])
def api():
    return jsonify(message="WELCOME TO POLYGREEN"), 201

# ------------------- AUTHENTICATION ENDPOINTS-------------------------
# ------------------ OTP STORE ------------------

@application.route("/api/auth/check-user", methods=["POST"])
def check_user():
    data = request.get_json() or {}
    mobile = str(data.get("mobile", "")).strip()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE mobile=%s", (mobile,))
            exists = cur.fetchone() is not None

    return jsonify(ok=True, exists=exists)

@application.route("/api/auth/send-otp", methods=["POST"])
def send_otp_db():
    data = request.get_json() or {}
    mobile = str(data.get("mobile", "")).strip()

    if not (mobile.isdigit() and 8 <= len(mobile) <= 15):
        return jsonify(ok=False, message="Invalid mobile number"), 400

    otp = str(random.randint(1000, 9999))
    expires_at = dt.datetime.utcnow() + dt.timedelta(minutes=5)

    # ✅ Save OTP first (always succeed)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_otps (mobile, otp, expires_at, verified, updated_at)
                VALUES (%s, %s, %s, FALSE, NOW())
                ON CONFLICT (mobile)
                DO UPDATE SET
                    otp = EXCLUDED.otp,
                    expires_at = EXCLUDED.expires_at,
                    verified = FALSE,
                    updated_at = NOW()
            """, (mobile, otp, expires_at))
        conn.commit()

    # ✅ Now try sending SMS safely
    if not sms:
        return jsonify(ok=True, message="OTP generated (SMS service unavailable)")

    try:
        response = sms.send_message({
            "from": "PolyGreen",
            "to": mobile,
            "text": f"Your OTP is {otp}",
        })

        status = response["messages"][0]["status"]
        if status != "0":
            error_text = response["messages"][0].get("error-text", "Unknown error")
            application.logger.warning(f"Vonage SMS failed: {error_text}")
            return jsonify(ok=True, message="OTP generated, SMS delivery pending")

    except Exception as e:
        application.logger.error(f"Vonage connection error: {e}")
        return jsonify(ok=True, message="OTP generated, SMS delivery retryable")

    return jsonify(ok=True, message="OTP sent successfully")


@application.route("/api/auth/verify-otp", methods=["POST"])
def verify_otp_db():
    data = request.get_json() or {}
    mobile = str(data.get("mobile", "")).strip()
    otp = str(data.get("otp", "")).strip()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT otp, expires_at, verified
                FROM user_otps
                WHERE mobile=%s
            """, (mobile,))
            row = cur.fetchone()

            if not row:
                return jsonify(ok=False, message="OTP not found"), 400

            if row["verified"]:
                return jsonify(ok=False, message="OTP already used"), 400

            if dt.datetime.utcnow() > row["expires_at"]:
                return jsonify(ok=False, message="OTP expired"), 400

            if row["otp"] != otp:
                return jsonify(ok=False, message="Invalid OTP"), 400

            cur.execute("""
                UPDATE user_otps SET verified=TRUE WHERE mobile=%s
            """, (mobile,))
        conn.commit()

    return jsonify(ok=True, message="OTP verified")

@application.route("/api/auth/set-new-password", methods=["POST"])
def set_new_password():
    data = request.get_json() or {}
    mobile = str(data.get("mobile", "")).strip()
    new_password = data.get("new_password", "").strip()

    if not new_password:
        return jsonify(ok=False, message="Password required"), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT verified FROM user_otps
                WHERE mobile=%s
            """, (mobile,))
            otp_row = cur.fetchone()

            if not otp_row or not otp_row["verified"]:
                return jsonify(ok=False, message="OTP not verified"), 400

            new_hash = bcrypt.hash(new_password)

            cur.execute("""
                UPDATE users SET password_hash=%s WHERE mobile=%s
            """, (new_hash, mobile))

            cur.execute("DELETE FROM user_otps WHERE mobile=%s", (mobile,))
        conn.commit()

    return jsonify(ok=True, message="Password reset successful")

@application.route("/api/auth/reset-password", methods=["POST"])
@jwt_required()
def reset_password_loggedin():
    uid = get_jwt_identity()
    data = request.get_json() or {}

    old_password = data.get("old_password", "").strip()
    new_password = data.get("new_password", "").strip()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE user_id=%s", (uid,))
            user = cur.fetchone()

            if not user or not bcrypt.verify(old_password[:72], user["password_hash"]):
                return jsonify(ok=False, message="Incorrect password"), 401

            new_hash = bcrypt.hash(new_password)
            cur.execute("""
                UPDATE users SET password_hash=%s WHERE user_id=%s
            """, (new_hash, uid))
        conn.commit()

    return jsonify(ok=True, message="Password updated")


#--------------------------REGISTER API--------------------------------

@application.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    name = data.get("name")
    mobile = str(data.get("mobile"))
    password = data.get("password")

    if not (name and mobile and password):
        return jsonify(message="Missing fields"), 400

    # Generate custom user_id
    user_id = generate_user_id(name, mobile)

    with get_db() as conn:
        with conn.cursor() as cur:

            # Check duplicates
            cur.execute("SELECT id FROM users WHERE mobile=%s OR user_id=%s",
                        (mobile, user_id))
            if cur.fetchone():
                return jsonify(message="mobile or user_id already used"), 400

            # Hash password
            password_hash = bcrypt.hash(password)

            # Insert user
            cur.execute("""
                INSERT INTO users (user_id, name, mobile, password_hash, points, bottles, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                RETURNING user_id, name, mobile, points, bottles
            """, (user_id, name, mobile, password_hash, 0, 0))

            new_user = cur.fetchone()

        conn.commit()

    # Create JWT
    token = create_access_token(
        identity=new_user["user_id"],
        additional_claims={
            "mobile": new_user["mobile"],
            "name": new_user["name"]
        }
    )

    return jsonify(
        message="Registered",
        access_token=token,
        user={
            "user_id": new_user["user_id"],
            "name": new_user["name"],
            "mobile": new_user["mobile"],
            "points": new_user["points"],
            "bottles": new_user["bottles"]
        }
    ), 201


#--------------------------LOGIN API-----------------------------------

@application.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    mobile = str(data.get("mobile", "")).strip()
    password = data.get("password", "").strip()

    # Validate inputs
    if not (mobile and password):
        return jsonify(message="Missing mobile or password"), 400

    # Validate mobile format
    if not (mobile.isdigit() and 8 <= len(mobile) <= 15):
        return jsonify(message="Invalid mobile number format"), 400

    # Fetch user
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE mobile=%s", (mobile,))
            u = cur.fetchone()

    # bcrypt limitation fix
    password_truncated = password[:72]

    # Verify
    if not u or not bcrypt.verify(password_truncated, u["password_hash"]):
        return jsonify(message="Invalid credentials"), 401

    # Create JWT
    token = create_access_token(
        identity=str(u["user_id"]),
        additional_claims={
            "mobile": u["mobile"],
            "name": u["name"]
        }
    )

    # Return response
    return jsonify(
        access_token=token,
        user={
            "user_id": u["user_id"],
            "name": u["name"],
            "mobile": u["mobile"],
            "points": u["points"],
            "bottles": u["bottles"]
        }
    ), 200


# ------------------------------USER ENDPOINTS----------------------------

#------------------------------USER DETAILS API-----------------------------
@application.route("/api/users/me", methods=["GET"])
@jwt_required()
def me():
    uid_str = get_jwt_identity()   # this is the user_id string
    u = get_user_or_404(uid_str)   # fetch by user_id
    u = serialize_row(u)

    return jsonify(
        user_id=u["user_id"],
        name=u["name"],
        mobile=u["mobile"],
        points=u["points"],
        bottles=u["bottles"],
        created_at=u.get("created_at")
    )

# ---------------------TRANSACTION & POINTS ENDPOINTS-----------------------------

#---------------------PAST 5 TRANSACTIONS SUMMARY API----------------------------------

@application.route("/api/points/summary", methods=["GET"])
@jwt_required()
def points_summary():
    user_id = get_jwt_identity()  # this is the string user_id
    u = get_user_or_404(user_id)  # fetch user by user_id

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, points, type, created_at
                FROM transactions
                WHERE user_id=%s
                ORDER BY created_at DESC
                LIMIT 5
            """, (u["user_id"],))
            rows = cur.fetchall()

    # convert datetimes to isoformat
    recent = [serialize_row(r) for r in rows]

    return jsonify(
        total_points=u["points"],
        recent=[
            {
                "id": r["id"],
                "points": r["points"],
                "type": r["type"],
                "created_at": r["created_at"]
            }
            for r in recent
        ]
    )


#----------------------------SHOW ALL TRANSACTIONS API-----------------------------------------------

# @application.route("/api/transactions", methods=["GET"])
# @jwt_required()
# def transactions():
#     uid_str = get_jwt_identity()
#     uid = int(uid_str)
#     with get_db() as conn:
#         with conn.cursor() as cur:
#             cur.execute("""
#                 SELECT id, type, points, brand_id, machine_id, created_at
#                 FROM transactions
#                 WHERE user_id=%s
#                 ORDER BY created_at DESC
#             """, (uid,))
#             rows = cur.fetchall()
#     rows = [serialize_row(r) for r in rows]
#     return jsonify(
#         items=[{"id": r["id"], "type": r["type"], "points": r["points"], "brand_id": r.get("brand_id"), "machine_id": r.get("machine_id"), "created_at": r["created_at"]} for r in rows]
#     )

# ----------------------------REDEEM ENDPOINTS--------------------------------------------------------

#----------------------------LIST ALL REDEEM BRANDS API---------------------------------------------------

# @application.route("/api/redeem/brands", methods=["GET"])
# @jwt_required()
# def redeem_brands():
#     with get_db() as conn:
#         with conn.cursor() as cur:
#             cur.execute("SELECT id, name, min_points FROM reward_brand WHERE active = TRUE")
#             rows = cur.fetchall()
#     return jsonify(items=[{"id": r["id"], "name": r["name"], "min_points": r["min_points"]} for r in rows])

#--------------------------REDEEM REQUEST API----------------------------------------------------------

# @application.route("/api/redeem/request", methods=["POST"])
# @jwt_required()
# def redeem_request():
#     uid_str = get_jwt_identity()
#     uid = int(uid_str)
#     u = get_user_or_404(uid)

#     data = request.get_json() or {}
#     brand_id = data.get("brand_id")
#     pts = int(data.get("points", 0))

#     with get_db() as conn:
#         with conn.cursor() as cur:
#             # check brand
#             cur.execute("SELECT * FROM reward_brand WHERE id=%s", (brand_id,))
#             brand = cur.fetchone()
#             if not brand or not brand["active"]:
#                 return jsonify(message="Invalid brand"), 400
#             if pts < brand["min_points"]:
#                 return jsonify(message=f"Minimum required for this brand is {brand['min_points']}"), 400
#             # refresh user points
#             cur.execute("SELECT points FROM users WHERE id=%s", (u["id"],))
#             user_row = cur.fetchone()
#             if not user_row or user_row["points"] < pts:
#                 return jsonify(message="Not enough points"), 400

#             # deduct points and insert transaction
#             cur.execute("UPDATE users SET points = points - %s WHERE id=%s", (pts, u["id"]))
#             cur.execute("""
#                 INSERT INTO transactions (user_id, type, points, brand_id, created_at)
#                 VALUES (%s, %s, %s, %s, NOW())
#                 RETURNING id
#             """, (u["id"], "redeem", pts, brand["id"]))
#             trx_id = cur.fetchone()["id"]
#         conn.commit()

#     coupon = f"{brand['name'][:3].upper()}-{u['id']}-{str(trx_id).zfill(4)}"
#     return jsonify(message="Redeem successful", coupon=coupon)

# ----------------------LIST ALL MACHINES API ------------------------------------------------

@application.route("/api/machines", methods=["GET"])
@jwt_required()
def list_machines():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM machines")
            rows = cur.fetchall()

    out = []
    for r in rows:
        r = serialize_row(r)
        max_capacity = r.get("max_capacity") or 0
        current = r.get("current_bottles") or 0

        out.append({
            "id": r.get("id"),
            "machine_id": r.get("machine_id"),
            "name": r.get("name"),
            "city": r.get("city"),
            "lat": r.get("lat"),
            "lng": r.get("lng"),
            "current_bottles": current,
            "max_capacity": max_capacity,
            "available_space": max_capacity - current,
            "is_full": bool(r.get("is_full")),
            "last_emptied": r.get("last_emptied")
        })

    return jsonify(items=out)


# --------------------- MACHINE ENDPOINTS --------------------------------------------------

#--------------------MACHINE FECTH USER API----------------------------------------------------

@application.route("/api/user/fetch", methods=["POST"])
def fetchuser():
    data = request.get_json() or {}
    mobile = str(data.get("mobile", "")).strip()

    # Validate mobile format
    if not mobile or not mobile.isdigit() or not (8 <= len(mobile) <= 15):
        return jsonify(message="Invalid mobile number"), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, name, mobile, bottles, points FROM users WHERE mobile=%s",
                (mobile,)
            )
            u = cur.fetchone()

    if not u:
        return jsonify(message="User not found. Please register in the mobile application."), 404

    return jsonify(
        user_id=u["user_id"],
        name=u["name"],
        mobile=u["mobile"],
        points=u["points"],
        bottles=u["bottles"]
    ), 200


#------------------BOTTLE INSERT API----------------------------------------------------------

@application.route("/api/machine/insert", methods=["POST"])
def machine_insert():
    data = request.get_json() or {}
    machine_id = data.get("machine_id")
    user_id = data.get("user_id")
    bottle_count = int(data.get("bottle_count", 1))
    points_per_bottle = int(data.get("points_per_bottle", 10))

    # Validation
    if not (machine_id and user_id):
        return jsonify(message="machine_id and user_id required"), 400

    if bottle_count <= 0:
        return jsonify(message="bottle_count must be at least 1"), 400

    with get_db() as conn:
        with conn.cursor() as cur:

            # Check user
            cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
            user = cur.fetchone()
            if not user:
                return jsonify(message="User not found"), 404

            # Check machine
            cur.execute("SELECT * FROM machines WHERE machine_id=%s", (machine_id,))
            machine = cur.fetchone()
            if not machine:
                return jsonify(message="Machine not found"), 404

            current = machine.get("current_bottles") or 0
            max_cap = machine.get("max_capacity") or 0
            available_space = max_cap - current

            # Machine full check
            if bottle_count > available_space:
                return jsonify(
                    message=f"Machine is full! Only {available_space} bottles can be accepted",
                    available_space=available_space,
                    requested=bottle_count
                ), 400

            # Determine new state
            new_current = current + bottle_count
            will_be_full = new_current >= max_cap

            earned_points = bottle_count * points_per_bottle

            # Update user
            cur.execute("""
                UPDATE users
                SET points = points + %s,
                    bottles = bottles + %s
                WHERE user_id=%s
            """, (earned_points, bottle_count, user_id))

            # Update machine
            cur.execute("""
                UPDATE machines
                SET current_bottles = current_bottles + %s,
                    total_bottles = total_bottles + %s,
                    is_full = %s
                WHERE machine_id = %s
            """, (bottle_count, bottle_count, will_be_full, machine_id))

            # Insert transaction
            cur.execute("""
                INSERT INTO transactions (user_id, type, points, bottles, machine_id, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                RETURNING id
            """, (user_id, "earn", earned_points, bottle_count, machine_id))

            trx_id = cur.fetchone()["id"]

        conn.commit()

    # Fetch updated values
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, points, bottles FROM users WHERE user_id=%s", (user_id,))
            new_user = cur.fetchone()
            cur.execute("SELECT current_bottles, max_capacity, is_full FROM machines WHERE machine_id=%s", (machine_id,))
            new_machine = cur.fetchone()

    return jsonify(
        message="Points and bottles added successfully",
        earned_points=earned_points,
        bottles_added=bottle_count,
        user_total_points=new_user["points"],
        user_total_bottles=new_user["bottles"],
        machine_current_bottles=new_machine["current_bottles"],
        machine_available_space=new_machine["max_capacity"] - new_machine["current_bottles"],
        machine_is_full=bool(new_machine["is_full"])
    ), 200


# ------------------------MAIN application-------------------------------------------------------

if __name__ == "__main__":
    application.run()

