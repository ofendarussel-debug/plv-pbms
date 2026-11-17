import calendar
import csv
import io
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, Response
from werkzeug.security import generate_password_hash
import database
from auth import login_required, role_required

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def fmt_short_date(date_str):
    try:
        dt = datetime.strptime(str(date_str), '%Y-%m-%d')
        return dt.strftime('%b %d, %Y').replace(' 0', ' ')
    except ValueError:
        return str(date_str)


def fmt_month_day(date_str):
    try:
        dt = datetime.strptime(str(date_str)[:10], '%Y-%m-%d')
        return dt.strftime('%b %d').replace(' 0', ' ')
    except ValueError:
        return str(date_str)


def fmt_time_12h(time_str):
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            dt = datetime.strptime(str(time_str), fmt)
            return dt.strftime('%I:%M %p').lstrip('0')
        except ValueError:
            continue
    return str(time_str)


# =========================================================
# A1 - ADMIN DASHBOARD
# =========================================================
@admin_bp.route('/dashboard')
@login_required
@role_required('admin')
def dashboard():
    conn = database.get_db()
    cursor = conn.cursor()
    today_str = datetime.now().strftime('%Y-%m-%d')

    cursor.execute("SELECT COUNT(*) FROM reservations WHERE status = 'Pending';")
    pending_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM reservations WHERE reservation_date = ? AND status IN ('Pending', 'Approved', 'Released');", (today_str,))
    todays_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM projectors WHERE status = 'Available';")
    available_projectors = cursor.fetchone()[0]

    cursor.execute('''
        SELECT COUNT(*) FROM reservations
        WHERE status = 'Released' AND reservation_date < ?;
    ''', (today_str,))
    overdue_count = cursor.fetchone()[0]

    # Pending requests table
    cursor.execute('''
        SELECT r.reservation_id, r.reservation_code, r.subject_name,
               r.reservation_date, r.start_time, r.end_time,
               s.section_name
        FROM reservations r
        JOIN sections s ON r.section_id = s.section_id
        WHERE r.status = 'Pending'
        ORDER BY r.reservation_date ASC, r.start_time ASC
        LIMIT 6;
    ''')
    pending_rows = []
    for r in cursor.fetchall():
        pending_rows.append({
            'reservation_id': r['reservation_id'],
            'reservation_code': r['reservation_code'],
            'section_name': r['section_name'],
            'subject_name': r['subject_name'],
            'date_time': f"{fmt_short_date(r['reservation_date'])} · {fmt_time_12h(r['start_time'])}–{fmt_time_12h(r['end_time'])}"
        })

    # Projector status today
    cursor.execute('''
        SELECT p.projector_id, p.projector_code, p.projector_name, p.status
        FROM projectors p
        ORDER BY p.projector_code ASC;
    ''')
    projectors = []
    for p in cursor.fetchall():
        st = p['status']
        if st == 'Available':
            label, badge = 'Available', 'pill-approved'
        elif st == 'Borrowed':
            label, badge = 'In use', 'pill-inuse'
        elif st == 'Under Maintenance':
            label, badge = 'Maintenance', 'pill-pending'
        else:
            label, badge = 'Inactive', 'pill-returned'
        projectors.append({
            'projector_code': p['projector_code'],
            'projector_name': p['projector_name'],
            'label': label,
            'badge': badge
        })

    conn.close()
    return render_template(
        'admin/dashboard.html',
        pending_count=pending_count,
        todays_count=todays_count,
        available_projectors=available_projectors,
        overdue_count=overdue_count,
        pending_rows=pending_rows,
        projectors=projectors
    )


# =========================================================
# A2 - REVIEW REQUESTS (Approve / Reject / Release / Return)
# =========================================================
@admin_bp.route('/requests', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def requests_page():
    conn = database.get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')
        res_id = request.form.get('reservation_id', type=int)
        current_filter = request.form.get('current_filter', 'Pending')

        cursor.execute('''
            SELECT r.*, s.section_name, p.projector_code
            FROM reservations r
            JOIN sections s ON r.section_id = s.section_id
            LEFT JOIN projectors p ON r.projector_id = p.projector_id
            WHERE r.reservation_id = ?;
        ''', (res_id,))
        target = cursor.fetchone()

        if not target:
            conn.close()
            flash('Reservation not found.', 'danger')
            return redirect(url_for('admin.requests_page', status=current_filter))

        # 1. APPROVE & ASSIGN PROJECTOR
        if action == 'approve':
            projector_id = request.form.get('projector_id', type=int)
            if not projector_id:
                conn.close()
                flash('Please select an available projector unit before approving.', 'danger')
                return redirect(url_for('admin.requests_page', status=current_filter, selected=res_id))

            # Verify projector is not double-booked for the same date and overlapping time
            cursor.execute('''
                SELECT reservation_code FROM reservations
                WHERE projector_id = ?
                  AND reservation_date = ?
                  AND reservation_id != ?
                  AND status IN ('Approved', 'Released')
                  AND (? < end_time AND ? > start_time);
            ''', (projector_id, target['reservation_date'], res_id, target['start_time'], target['end_time']))
            conflict = cursor.fetchone()
            if conflict:
                conn.close()
                flash(f"Conflict: That projector is already assigned to {conflict['reservation_code']} during that time slot.", 'danger')
                return redirect(url_for('admin.requests_page', status=current_filter, selected=res_id))

            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
                UPDATE reservations
                SET status = 'Approved', projector_id = ?, processed_by = ?, approved_at = ?, rejection_reason = NULL
                WHERE reservation_id = ?;
            ''', (projector_id, session['user_id'], now_str, res_id))

            cursor.execute("SELECT projector_code FROM projectors WHERE projector_id = ?;", (projector_id,))
            p_code = cursor.fetchone()['projector_code']

            cursor.execute('''
                INSERT INTO audit_logs (user_id, action, description)
                VALUES (?, 'APPROVE_RESERVATION', ?);
            ''', (session['user_id'], f"Approved {target['reservation_code']} ({target['section_name']}) and assigned {p_code}."))
            conn.commit()
            conn.close()
            flash(f"Reservation {target['reservation_code']} approved and assigned to {p_code}.", 'success')
            return redirect(url_for('admin.requests_page', status='Approved', selected=res_id))

        # 2. REJECT REQUEST (OR REJECT APPROVAL)
        elif action == 'reject':
            reason = request.form.get('rejection_reason', '').strip()
            if not reason:
                conn.close()
                flash('Please enter a reason when rejecting a reservation request.', 'danger')
                return redirect(url_for('admin.requests_page', status=current_filter, selected=res_id))

            cursor.execute('''
                UPDATE reservations
                SET status = 'Rejected', projector_id = NULL, processed_by = ?, rejection_reason = ?
                WHERE reservation_id = ?;
            ''', (session['user_id'], reason, res_id))

            cursor.execute('''
                INSERT INTO audit_logs (user_id, action, description)
                VALUES (?, 'REJECT_RESERVATION', ?);
            ''', (session['user_id'], f"Rejected {target['reservation_code']} ({target['section_name']}): {reason}"))
            conn.commit()
            conn.close()
            flash(f"Reservation {target['reservation_code']} has been rejected.", 'info')
            return redirect(url_for('admin.requests_page', status='Rejected', selected=res_id))

        # 3. MARK AS RELEASED (Equipment Handed Over to Section)
        elif action == 'release':
            if not target['projector_id']:
                conn.close()
                flash('Cannot release: No projector unit is assigned to this reservation.', 'danger')
                return redirect(url_for('admin.requests_page', status=current_filter, selected=res_id))

            cursor.execute("UPDATE reservations SET status = 'Released' WHERE reservation_id = ?;", (res_id,))
            cursor.execute("UPDATE projectors SET status = 'Borrowed' WHERE projector_id = ?;", (target['projector_id'],))
            cursor.execute('''
                INSERT INTO usage_records (reservation_id, released_by, condition_before, usage_status)
                VALUES (?, ?, 'Good', 'In Use');
            ''', (res_id, session['user_id']))

            cursor.execute('''
                INSERT INTO audit_logs (user_id, action, description)
                VALUES (?, 'RELEASE_PROJECTOR', ?);
            ''', (session['user_id'], f"Released {target['projector_code']} for {target['reservation_code']} ({target['section_name']})."))
            conn.commit()
            conn.close()
            flash(f"{target['projector_code']} marked as Released to {target['section_name']}.", 'success')
            return redirect(url_for('admin.requests_page', status='Released', selected=res_id))

        # 4. MARK AS RETURNED (Equipment Returned After Class)
        elif action == 'return':
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("UPDATE reservations SET status = 'Completed' WHERE reservation_id = ?;", (res_id,))
            if target['projector_id']:
                cursor.execute("UPDATE projectors SET status = 'Available' WHERE projector_id = ?;", (target['projector_id'],))
            cursor.execute('''
                UPDATE usage_records
                SET received_by = ?, returned_at = ?, condition_after = 'Good', usage_status = 'Returned'
                WHERE reservation_id = ? AND usage_status = 'In Use';
            ''', (session['user_id'], now_str, res_id))

            cursor.execute('''
                INSERT INTO audit_logs (user_id, action, description)
                VALUES (?, 'RETURN_PROJECTOR', ?);
            ''', (session['user_id'], f"Marked {target['projector_code']} as returned for {target['reservation_code']}."))
            conn.commit()
            conn.close()
            flash(f"{target['projector_code']} marked as Returned and is now Available.", 'success')
            return redirect(url_for('admin.requests_page', status='All', selected=res_id))

    # GET: Filter & Display Requests
    status_filter = request.args.get('status', 'Pending')
    selected_id = request.args.get('selected', type=int)

    query = '''
        SELECT r.*, s.section_name, c.classroom_name, u.full_name AS student_name,
               p.projector_code, p.projector_name
        FROM reservations r
        JOIN sections s ON r.section_id = s.section_id
        JOIN classrooms c ON r.classroom_id = c.classroom_id
        JOIN users u ON r.user_id = u.user_id
        LEFT JOIN projectors p ON r.projector_id = p.projector_id
    '''
    params = []
    if status_filter == 'Returned':
        query += " WHERE r.status = 'Completed'"
    elif status_filter in ('Pending', 'Approved', 'Released', 'Rejected'):
        query += " WHERE r.status = ?"
        params.append(status_filter)

    query += " ORDER BY r.reservation_date ASC, r.start_time ASC;"
    cursor.execute(query, params)
    raw_list = cursor.fetchall()

    requests_list = []
    for r in raw_list:
        disp_status = 'Returned' if r['status'] == 'Completed' else r['status']
        requests_list.append({
            'reservation_id': r['reservation_id'],
            'reservation_code': r['reservation_code'],
            'section_name': r['section_name'],
            'student_name': r['student_name'],
            'subject_name': r['subject_name'],
            'instructor_name': r['instructor_name'],
            'classroom_name': r['classroom_name'],
            'reservation_date': r['reservation_date'],
            'formatted_date': fmt_short_date(r['reservation_date']),
            'requested_on': fmt_month_day(r['created_at']),
            'approved_on': fmt_short_date(r['approved_at'][:10]) if r['approved_at'] else '—',
            'time_slot': f"{fmt_time_12h(r['start_time'])} – {fmt_time_12h(r['end_time'])}",
            'start_time': r['start_time'],
            'end_time': r['end_time'],
            'remarks': r['remarks'] or 'None',
            'status': disp_status,
            'raw_status': r['status'],
            'projector_id': r['projector_id'],
            'projector_code': r['projector_code'] or 'Unassigned',
            'rejection_reason': r['rejection_reason'] or ''
        })

    # Choose selected item (defaults to first item in list)
    selected_req = None
    if requests_list:
        if selected_id:
            for item in requests_list:
                if item['reservation_id'] == selected_id:
                    selected_req = item
                    break
        if not selected_req:
            selected_req = requests_list[0]

    # Fetch available projectors for the selected request's date/time slot
    available_projectors = []
    if selected_req:
        cursor.execute('''
            SELECT projector_id, projector_code, projector_name, brand, model
            FROM projectors
            WHERE status = 'Available'
              AND projector_id NOT IN (
                  SELECT projector_id FROM reservations
                  WHERE projector_id IS NOT NULL
                    AND reservation_date = ?
                    AND reservation_id != ?
                    AND status IN ('Approved', 'Released')
                    AND (? < end_time AND ? > start_time)
              )
            ORDER BY projector_code ASC;
        ''', (selected_req['reservation_date'], selected_req['reservation_id'], selected_req['start_time'], selected_req['end_time']))
        available_projectors = cursor.fetchall()

    conn.close()
    return render_template(
        'admin/requests.html',
        status_filter=status_filter,
        requests_list=requests_list,
        selected_req=selected_req,
        available_projectors=available_projectors
    )


# =========================================================
# A3 - PROJECTORS INVENTORY MANAGEMENT
# =========================================================
@admin_bp.route('/projectors', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def projectors_page():
    conn = database.get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            code = request.form.get('projector_code', '').strip().upper()
            name = request.form.get('projector_name', '').strip()
            brand = request.form.get('brand', '').strip()
            model = request.form.get('model', '').strip()
            status = request.form.get('status', 'Available')

            if not code or not name or not model:
                flash('Asset tag, Unit name, and Model are required.', 'danger')
            else:
                try:
                    cursor.execute('''
                        INSERT INTO projectors (projector_code, projector_name, brand, model, condition, status)
                        VALUES (?, ?, ?, ?, 'Good', ?);
                    ''', (code, name, brand or model.split()[0], model, status))
                    cursor.execute('''
                        INSERT INTO audit_logs (user_id, action, description)
                        VALUES (?, 'ADD_PROJECTOR', ?);
                    ''', (session['user_id'], f"Added projector unit {code} ({name})."))
                    conn.commit()
                    flash(f'Projector {code} added successfully.', 'success')
                except Exception as e:
                    conn.rollback()
                    flash(f'Could not add projector: {str(e)}', 'danger')

        elif action == 'update_status':
            p_id = request.form.get('projector_id', type=int)
            new_status = request.form.get('new_status', 'Available')
            cursor.execute("SELECT projector_code FROM projectors WHERE projector_id = ?;", (p_id,))
            p_row = cursor.fetchone()
            if p_row:
                cursor.execute("UPDATE projectors SET status = ? WHERE projector_id = ?;", (new_status, p_id))
                cursor.execute('''
                    INSERT INTO audit_logs (user_id, action, description)
                    VALUES (?, 'UPDATE_PROJECTOR', ?);
                ''', (session['user_id'], f"Changed {p_row['projector_code']} status to {new_status}."))
                conn.commit()
                flash(f"{p_row['projector_code']} updated to {new_status}.", 'success')

    cursor.execute('''
        SELECT p.*,
               (SELECT s.section_name || ' · ' || c.classroom_name
                FROM reservations r
                JOIN sections s ON r.section_id = s.section_id
                JOIN classrooms c ON r.classroom_id = c.classroom_id
                WHERE r.projector_id = p.projector_id AND r.status = 'Released'
                LIMIT 1) AS active_use
        FROM projectors p
        ORDER BY p.projector_code ASC;
    ''')
    projectors = cursor.fetchall()
    conn.close()
    return render_template('admin/projectors.html', projectors=projectors)


# =========================================================
# A4 - SECTION REPRESENTATIVE ACCOUNTS
# =========================================================
@admin_bp.route('/accounts', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def accounts_page():
    conn = database.get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')
        user_id = request.form.get('user_id', type=int)

        cursor.execute('''
            SELECT u.*, s.section_name
            FROM users u
            LEFT JOIN sections s ON u.section_id = s.section_id
            WHERE u.user_id = ? AND u.role = 'student';
        ''', (user_id,))
        rep = cursor.fetchone()

        if rep:
            if action == 'toggle_status':
                new_status = 'inactive' if rep['account_status'] == 'active' else 'active'
                cursor.execute("UPDATE users SET account_status = ? WHERE user_id = ?;", (new_status, user_id))
                cursor.execute('''
                    INSERT INTO audit_logs (user_id, action, description)
                    VALUES (?, 'TOGGLE_ACCOUNT', ?);
                ''', (session['user_id'], f"Set {rep['section_name']} representative ({rep['username']}) to {new_status}."))
                conn.commit()
                flash(f"Account for {rep['section_name']} is now {new_status}.", 'success')

            elif action == 'reset_password':
                new_pass = request.form.get('new_password', '').strip()
                if len(new_pass) < 6:
                    flash('New password must be at least 6 characters.', 'danger')
                else:
                    cursor.execute("UPDATE users SET password_hash = ? WHERE user_id = ?;", (generate_password_hash(new_pass), user_id))
                    cursor.execute('''
                        INSERT INTO audit_logs (user_id, action, description)
                        VALUES (?, 'RESET_PASSWORD', ?);
                    ''', (session['user_id'], f"Reset password for {rep['section_name']} ({rep['username']})."))
                    conn.commit()
                    flash(f"Password updated for {rep['section_name']} ({rep['username']}).", 'success')

    cursor.execute('''
        SELECT u.user_id, u.school_id_number, u.full_name, u.username, u.email,
               u.contact_number, u.account_status, u.last_login, s.section_name
        FROM users u
        LEFT JOIN sections s ON u.section_id = s.section_id
        WHERE u.role = 'student'
        ORDER BY s.section_name ASC;
    ''')
    accounts = []
    for a in cursor.fetchall():
        accounts.append({
            'user_id': a['user_id'],
            'section_name': a['section_name'] or 'Unassigned',
            'full_name': a['full_name'],
            'school_id_number': a['school_id_number'],
            'username': a['username'],
            'email': a['email'] or '—',
            'account_status': a['account_status'],
            'last_login': fmt_short_date(a['last_login'][:10]) if a['last_login'] else 'Never'
        })

    conn.close()
    return render_template('admin/accounts.html', accounts=accounts)


# =========================================================
# A5 - CALENDAR AND REPORTS
# =========================================================
@admin_bp.route('/reports')
@login_required
@role_required('admin')
def reports_page():
    now = datetime.now()
    year = request.args.get('year', default=now.year, type=int)
    month = request.args.get('month', default=now.month, type=int)

    month_prefix = f"{year:04d}-{month:02d}"
    month_name = f"{calendar.month_name[month]} {year}"

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM reservations WHERE reservation_date LIKE ?;", (f"{month_prefix}%",))
    month_total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM reservations WHERE reservation_date LIKE ? AND status IN ('Approved', 'Released', 'Completed');", (f"{month_prefix}%",))
    month_approved = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM reservations WHERE reservation_date LIKE ? AND status = 'Completed';", (f"{month_prefix}%",))
    month_completed = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM reservations WHERE reservation_date LIKE ? AND status = 'Rejected';", (f"{month_prefix}%",))
    month_rejected = cursor.fetchone()[0]

    # Group bookings by date for calendar grid
    cursor.execute('''
        SELECT r.reservation_date, r.reservation_code, r.subject_name, r.status,
               s.section_name, p.projector_code
        FROM reservations r
        JOIN sections s ON r.section_id = s.section_id
        LEFT JOIN projectors p ON r.projector_id = p.projector_id
        WHERE r.reservation_date LIKE ?
        ORDER BY r.reservation_date ASC, r.start_time ASC;
    ''', (f"{month_prefix}%",))

    by_date = {}
    for row in cursor.fetchall():
        d = row['reservation_date']
        by_date.setdefault(d, []).append({
            'code': row['reservation_code'],
            'section': row['section_name'],
            'subject': row['subject_name'],
            'status': 'Returned' if row['status'] == 'Completed' else row['status'],
            'projector': row['projector_code'] or 'Unassigned'
        })

    cal = calendar.Calendar(firstweekday=6)  # Sunday first like Figma
    weeks = []
    for week in cal.monthdayscalendar(year, month):
        week_cells = []
        for day_num in week:
            if day_num == 0:
                week_cells.append({'day': None})
            else:
                date_key = f"{year:04d}-{month:02d}-{day_num:02d}"
                items = by_date.get(date_key, [])
                week_cells.append({
                    'day': day_num,
                    'date_str': date_key,
                    'count': len(items),
                    'items': items
                })
        weeks.append(week_cells)

    conn.close()
    return render_template(
        'admin/reports.html',
        year=year,
        month=month,
        month_name=month_name,
        month_total=month_total,
        month_approved=month_approved,
        month_completed=month_completed,
        month_rejected=month_rejected,
        weeks=weeks
    )


@admin_bp.route('/reports/export')
@login_required
@role_required('admin')
def export_csv():
    """Exports all reservation records as a downloadable CSV report."""
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT r.reservation_code, s.section_name, u.full_name AS representative,
               r.subject_name, r.instructor_name, c.classroom_name,
               r.reservation_date, r.start_time, r.end_time,
               COALESCE(p.projector_code, 'Unassigned') AS projector,
               r.status
        FROM reservations r
        JOIN sections s ON r.section_id = s.section_id
        JOIN users u ON r.user_id = u.user_id
        JOIN classrooms c ON r.classroom_id = c.classroom_id
        LEFT JOIN projectors p ON r.projector_id = p.projector_id
        ORDER BY r.reservation_date DESC, r.start_time ASC;
    ''')
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Ref No', 'Section', 'Representative', 'Subject', 'Instructor',
        'Classroom', 'Date', 'Start Time', 'End Time', 'Projector Unit', 'Status'
    ])
    for r in rows:
        writer.writerow(list(r))

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=PLV_Projector_Reservations_Report.csv'}
    )