from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash
from config import Config
import database
from auth import auth_bp, login_required, role_required
from booking import booking_bp
from admin import admin_bp

csrf = CSRFProtect()


def format_long_date(date_str):
    try:
        dt = datetime.strptime(str(date_str), '%Y-%m-%d')
        return dt.strftime('%B %d, %Y').replace(' 0', ' ')
    except ValueError:
        return str(date_str)


def format_short_date(date_str):
    try:
        dt = datetime.strptime(str(date_str), '%Y-%m-%d')
        return dt.strftime('%b %d, %Y').replace(' 0', ' ')
    except ValueError:
        return str(date_str)


def format_time_12h(time_str):
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            dt = datetime.strptime(str(time_str), fmt)
            return dt.strftime('%I:%M %p').lstrip('0')
        except ValueError:
            continue
    return str(time_str)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    csrf.init_app(app)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(booking_bp)
    app.register_blueprint(admin_bp)

    with app.app_context():
        database.init_db()
        database.seed_initial_projectors()
        database.seed_admin_account()
        database.seed_demo_data()

    @app.route('/')
    def index():
        if 'user_id' in session:
            if session.get('role') == 'student':
                return redirect(url_for('student_dashboard'))
            elif session.get('role') == 'admin':
                return redirect(url_for('admin.dashboard'))
        return redirect(url_for('auth.login'))

    @app.route('/setup', methods=['GET', 'POST'])
    def setup_admin():
        """Creates the single Administrator account for the PLV system."""
        if database.admin_exists():
            flash('Administrator account is already configured. Please sign in via the Admin Portal.', 'info')
            return redirect(url_for('auth.admin_login'))

        if request.method == 'POST':
            school_id_number = request.form.get('school_id_number', '').strip()
            full_name = request.form.get('full_name', '').strip()
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            confirm_password = request.form.get('confirm_password', '')

            if not school_id_number or not full_name or not username or not password:
                flash('Admin ID, Full Name, Username, and Password are required.', 'danger')
                return render_template('setup_admin.html')

            if len(username) < 4:
                flash('Username must be at least 4 characters long.', 'danger')
                return render_template('setup_admin.html')

            if len(password) < 6:
                flash('Password must be at least 6 characters long.', 'danger')
                return render_template('setup_admin.html')

            if password != confirm_password:
                flash('Passwords do not match.', 'danger')
                return render_template('setup_admin.html')

            conn = database.get_db()
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO users (school_id_number, full_name, username, email, password_hash, role, section_id, account_status)
                    VALUES (?, ?, ?, ?, ?, 'admin', NULL, 'active');
                ''', (school_id_number, full_name, username, email or None, generate_password_hash(password)))
                admin_id = cursor.lastrowid

                cursor.execute('''
                    INSERT INTO audit_logs (user_id, action, description)
                    VALUES (?, 'INITIAL_SETUP', ?);
                ''', (admin_id, f'Created single PLV Administrator account: {username}'))

                conn.commit()
            except Exception as e:
                conn.rollback()
                conn.close()
                flash(f'Could not create administrator account: {str(e)}', 'danger')
                return render_template('setup_admin.html')

            conn.close()
            flash('Administrator account created! Sign in below using your Admin credentials.', 'success')
            return redirect(url_for('auth.admin_login'))

        return render_template('setup_admin.html')

    @app.route('/student/dashboard')
    @login_required
    @role_required('student')
    def student_dashboard():
        conn = database.get_db()
        cursor = conn.cursor()
        user_id = session['user_id']

        cursor.execute("SELECT COUNT(*) FROM reservations WHERE user_id = ? AND status = 'Pending';", (user_id,))
        pending_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reservations WHERE user_id = ? AND status IN ('Approved', 'Released');", (user_id,))
        approved_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reservations WHERE user_id = ?;", (user_id,))
        total_reservations = cursor.fetchone()[0]

        today_str = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT r.reservation_id, r.reservation_code, r.subject_name, r.instructor_name,
                   r.reservation_date, r.start_time, r.end_time, r.status,
                   c.classroom_name, p.projector_code
            FROM reservations r
            JOIN classrooms c ON r.classroom_id = c.classroom_id
            LEFT JOIN projectors p ON r.projector_id = p.projector_id
            WHERE r.user_id = ? AND r.status IN ('Approved', 'Released') AND r.reservation_date >= ?
            ORDER BY r.reservation_date ASC, r.start_time ASC
            LIMIT 1;
        ''', (user_id, today_str))
        upcoming_row = cursor.fetchone()

        upcoming_reservation = None
        if upcoming_row:
            upcoming_reservation = {
                'reservation_code': upcoming_row['reservation_code'],
                'long_date': format_long_date(upcoming_row['reservation_date']),
                'time_range': f"{format_time_12h(upcoming_row['start_time'])} - {format_time_12h(upcoming_row['end_time'])}",
                'subject_name': upcoming_row['subject_name'],
                'classroom_name': upcoming_row['classroom_name'],
                'projector_code': upcoming_row['projector_code'] or 'Pending Unit'
            }

        cursor.execute('''
            SELECT r.reservation_id, r.reservation_code, r.subject_name,
                   r.reservation_date, r.start_time, r.end_time, r.status,
                   r.rejection_reason, c.classroom_name, p.projector_code
            FROM reservations r
            JOIN classrooms c ON r.classroom_id = c.classroom_id
            LEFT JOIN projectors p ON r.projector_id = p.projector_id
            WHERE r.user_id = ?
            ORDER BY r.created_at DESC
            LIMIT 6;
        ''', (user_id,))
        raw_rows = cursor.fetchall()

        recent_reservations = []
        for row in raw_rows:
            display_status = 'Returned' if row['status'] == 'Completed' else row['status']
            recent_reservations.append({
                'reservation_id': row['reservation_id'],
                'reservation_code': row['reservation_code'],
                'subject_name': row['subject_name'],
                'date_time_label': f"{format_short_date(row['reservation_date'])} · {format_time_12h(row['start_time'])}–{format_time_12h(row['end_time'])}",
                'classroom_name': row['classroom_name'],
                'status': display_status,
                'projector_code': row['projector_code'] or 'Not yet assigned',
                'rejection_reason': row['rejection_reason'] or 'No specific reason provided.'
            })

        conn.close()
        return render_template(
            'student/dashboard.html',
            pending_count=pending_count,
            approved_count=approved_count,
            total_reservations=total_reservations,
            upcoming_reservation=upcoming_reservation,
            recent_reservations=recent_reservations
        )

    @app.errorhandler(404)
    def page_not_found(e):
        if request.path.startswith('/static/') or request.path == '/favicon.ico':
            return '', 404
        flash('The requested page was not found.', 'warning')
        if 'user_id' in session:
            if session.get('role') == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('student_dashboard'))
        return redirect(url_for('auth.login'))

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)