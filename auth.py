import re
import sqlite3
from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash, generate_password_hash
import database

auth_bp = Blueprint('auth', __name__)


def login_required(f):
    """Ensures a valid, active user is signed in before accessing a protected route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            flash('Please log in to continue.', 'danger')
            return redirect(url_for('auth.login'))

        conn = database.get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT account_status FROM users WHERE user_id = ?;", (user_id,))
            row = cursor.fetchone()
        finally:
            conn.close()

        if not row or row['account_status'] != 'active':
            session.clear()
            flash('Your session expired or your account is inactive. Please log in again.', 'info')
            return redirect(url_for('auth.login'))

        return f(*args, **kwargs)
    return decorated_function


def role_required(*allowed_roles):
    """Restricts access to specific user roles ('student' or 'admin')."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in first.', 'danger')
                return redirect(url_for('auth.login'))

            current_role = session.get('role')
            if current_role not in allowed_roles:
                flash('Access denied: You do not have permission to view that page.', 'danger')
                if current_role == 'student':
                    return redirect(url_for('student_dashboard'))
                elif current_role == 'admin':
                    return redirect(url_for('admin.dashboard'))
                return redirect(url_for('auth.login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def normalize_section_name(raw_section):
    """Normalizes spacing and casing (e.g., '  bsit   2-2 ' -> 'BSIT 2-2')."""
    return ' '.join(raw_section.strip().upper().split())


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Student Section Representative Login (/auth/login)."""
    if 'user_id' in session:
        if session.get('role') == 'student':
            return redirect(url_for('student_dashboard'))
        elif session.get('role') == 'admin':
            return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            flash('Please enter both your section username and password.', 'danger')
            return render_template('login.html')

        conn = database.get_db()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.user_id, u.school_id_number, u.full_name, u.username,
                       u.email, u.password_hash, u.role, u.section_id,
                       u.account_status, s.section_name
                FROM users u
                LEFT JOIN sections s ON u.section_id = s.section_id
                WHERE u.username = ?;
            ''', (username,))
            user = cursor.fetchone()

            if user and user['role'] == 'admin':
                flash('Administrator accounts must sign in through the Administrator Login page.', 'info')
                return redirect(url_for('auth.admin_login'))

            if user and user['role'] == 'student' and user['account_status'] == 'active' and check_password_hash(user['password_hash'], password):
                now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute("UPDATE users SET last_login = ? WHERE user_id = ?;", (now_str, user['user_id']))

                session.clear()
                session['user_id'] = user['user_id']
                session['school_id_number'] = user['school_id_number']
                session['username'] = user['username']
                session['full_name'] = user['full_name']
                session['email'] = user['email'] or 'Not provided'
                session['role'] = 'student'
                session['section_id'] = user['section_id']
                session['section_name'] = user['section_name'] or 'Unassigned Section'

                cursor.execute('''
                    INSERT INTO audit_logs (user_id, action, description)
                    VALUES (?, 'STUDENT_LOGIN', ?);
                ''', (user['user_id'], f"Section representative '{user['username']}' ({session['section_name']}) logged in."))
                conn.commit()
                return redirect(url_for('student_dashboard'))
            else:
                flash('Invalid section username or password, or your account has been deactivated.', 'danger')
        finally:
            conn.close()

    return render_template('login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """
    Self-Registration for Section Representatives (/auth/register).
    Allows typing any section name while strictly enforcing 1 account per section.
    """
    if 'user_id' in session:
        if session.get('role') == 'student':
            return redirect(url_for('student_dashboard'))
        return redirect(url_for('admin.dashboard'))

    form_data = {
        'section_name': '',
        'full_name': '',
        'school_id_number': '',
        'username': '',
        'email': ''
    }

    if request.method == 'POST':
        section_name = normalize_section_name(request.form.get('section_name', ''))
        full_name = request.form.get('full_name', '').strip()
        school_id_number = request.form.get('school_id_number', '').strip()
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        form_data = {
            'section_name': section_name,
            'full_name': full_name,
            'school_id_number': school_id_number,
            'username': username,
            'email': email
        }

        if not section_name or not full_name or not school_id_number or not username or not password:
            flash('Section, Full Name, Student Number, Username, and Password are required.', 'danger')
            return render_template('register.html', form_data=form_data)

        if len(username) < 4:
            flash('Username must be at least 4 characters long.', 'danger')
            return render_template('register.html', form_data=form_data)

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return render_template('register.html', form_data=form_data)

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('register.html', form_data=form_data)

        conn = database.get_db()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT section_id, section_name FROM sections WHERE UPPER(section_name) = ?;", (section_name,))
            sec_row = cursor.fetchone()

            if sec_row:
                section_id = sec_row['section_id']
                cursor.execute('''
                    SELECT full_name FROM users
                    WHERE role = 'student' AND section_id = ?;
                ''', (section_id,))
                existing_rep = cursor.fetchone()
                if existing_rep:
                    flash(
                        f'Section {section_name} already has a registered representative ({existing_rep["full_name"]}). '
                        f'Only 1 account per section is allowed.',
                        'danger'
                    )
                    return render_template('register.html', form_data=form_data)
            else:
                course_match = re.match(r'^([A-Z]+)', section_name)
                course = course_match.group(1) if course_match else 'GENERAL'
                year_match = re.search(r'\b([1-5])\b|([1-5])-', section_name)
                year_level = int(year_match.group(1) or year_match.group(2)) if year_match else 1

                cursor.execute('''
                    INSERT INTO sections (section_name, year_level, course)
                    VALUES (?, ?, ?);
                ''', (section_name, year_level, course))
                section_id = cursor.lastrowid

            cursor.execute("SELECT user_id FROM users WHERE username = ?;", (username,))
            if cursor.fetchone():
                flash('That username is already taken. Please choose another.', 'danger')
                return render_template('register.html', form_data=form_data)

            cursor.execute("SELECT user_id FROM users WHERE school_id_number = ?;", (school_id_number,))
            if cursor.fetchone():
                flash('That Student Number is already registered.', 'danger')
                return render_template('register.html', form_data=form_data)

            cursor.execute('''
                INSERT INTO users (
                    school_id_number, full_name, username, email,
                    password_hash, role, section_id, account_status
                )
                VALUES (?, ?, ?, ?, ?, 'student', ?, 'active');
            ''', (
                school_id_number,
                full_name,
                username,
                email or None,
                generate_password_hash(password),
                section_id
            ))
            new_user_id = cursor.lastrowid

            cursor.execute('''
                INSERT INTO audit_logs (user_id, action, description)
                VALUES (?, 'SECTION_REGISTER', ?);
            ''', (new_user_id, f"Registered section representative '{full_name}' ({username}) for {section_name}."))

            conn.commit()
            flash(f'Account created for {section_name}! You can now log in.', 'success')
            return redirect(url_for('auth.login'))

        except sqlite3.IntegrityError:
            conn.rollback()
            flash(f'Section {section_name}, username, or student number already has an account.', 'danger')
            return render_template('register.html', form_data=form_data)
        except Exception as e:
            conn.rollback()
            flash(f'Could not create account: {str(e)}', 'danger')
            return render_template('register.html', form_data=form_data)
        finally:
            conn.close()

    return render_template('register.html', form_data=form_data)


@auth_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Dedicated Administrator Login Page (/auth/admin/login)."""
    if not database.admin_exists():
        flash('No Administrator account exists yet. Please complete initial setup first.', 'info')
        return redirect(url_for('setup_admin'))

    if 'user_id' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('student_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            flash('Please enter the Administrator username and password.', 'danger')
            return render_template('admin_login.html')

        conn = database.get_db()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, school_id_number, full_name, username, email,
                       password_hash, role, account_status
                FROM users
                WHERE username = ? AND role = 'admin';
            ''', (username,))
            admin_user = cursor.fetchone()

            if admin_user and admin_user['account_status'] == 'active' and check_password_hash(admin_user['password_hash'], password):
                now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute("UPDATE users SET last_login = ? WHERE user_id = ?;", (now_str, admin_user['user_id']))

                session.clear()
                session['user_id'] = admin_user['user_id']
                session['school_id_number'] = admin_user['school_id_number']
                session['username'] = admin_user['username']
                session['full_name'] = admin_user['full_name']
                session['email'] = admin_user['email'] or 'admin@plv.edu.ph'
                session['role'] = 'admin'

                cursor.execute('''
                    INSERT INTO audit_logs (user_id, action, description)
                    VALUES (?, 'ADMIN_LOGIN', ?);
                ''', (admin_user['user_id'], f"Administrator '{admin_user['username']}' signed in."))
                conn.commit()
                return redirect(url_for('admin.dashboard'))
            else:
                flash('Invalid Administrator credentials.', 'danger')
        finally:
            conn.close()

    return render_template('admin_login.html')


@auth_bp.route('/logout')
def logout():
    """Safely logs out any active or stale session and closes DB connections."""
    user_id = session.get('user_id')
    username = session.get('username')
    was_admin = session.get('role') == 'admin'

    if user_id:
        conn = database.get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?;", (user_id,))
            valid_user_id = user_id if cursor.fetchone() else None

            cursor.execute('''
                INSERT INTO audit_logs (user_id, action, description)
                VALUES (?, 'LOGOUT', ?);
            ''', (valid_user_id, f"User '{username}' logged out."))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()

    session.clear()
    flash('You have been logged out.', 'info')
    if was_admin:
        return redirect(url_for('auth.admin_login'))
    return redirect(url_for('auth.login'))