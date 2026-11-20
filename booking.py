from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
import database
from auth import login_required, role_required

booking_bp = Blueprint('booking', __name__)

TIME_SLOTS = [
    {"value": "07:00-10:00", "label": "7:00 AM - 10:00 AM", "short": "7:00–10:00 AM", "start": "07:00", "end": "10:00"},
    {"value": "10:00-13:00", "label": "10:00 AM - 1:00 PM", "short": "10:00 AM–1:00 PM", "start": "10:00", "end": "13:00"},
    {"value": "14:00-17:00", "label": "2:00 PM - 5:00 PM", "short": "2:00–5:00 PM", "start": "14:00", "end": "17:00"},
    {"value": "17:00-20:00", "label": "5:00 PM - 8:00 PM", "short": "5:00–8:00 PM", "start": "17:00", "end": "20:00"},
]


def get_slot_info(slot_value):
    for slot in TIME_SLOTS:
        if slot["value"] == slot_value:
            return slot
    return TIME_SLOTS[0]


def format_short_date(date_str):
    try:
        dt = datetime.strptime(str(date_str), '%Y-%m-%d')
        return dt.strftime('%b %d').replace(' 0', ' ')
    except ValueError:
        return str(date_str)


def format_full_date(date_str):
    try:
        dt = datetime.strptime(str(date_str), '%Y-%m-%d')
        return dt.strftime('%B %d, %Y').replace(' 0', ' ')
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


def get_user_created_suggestions():
    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT subject_name FROM reservations ORDER BY subject_name ASC;")
    subjects = [row['subject_name'] for row in cursor.fetchall()]

    cursor.execute("SELECT classroom_name FROM classrooms WHERE room_status = 'available' ORDER BY classroom_name ASC;")
    classrooms = [row['classroom_name'] for row in cursor.fetchall()]

    conn.close()
    return subjects, classrooms


def compute_slot_availability(target_date, start_time, end_time):
    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT projector_id, projector_code, projector_name, brand, model, status
        FROM projectors
        ORDER BY projector_code ASC;
    ''')
    projectors = cursor.fetchall()

    cursor.execute('''
        SELECT projector_id
        FROM reservations
        WHERE reservation_date = ?
          AND projector_id IS NOT NULL
          AND status IN ('Pending', 'Approved', 'Released')
          AND (? < end_time AND ? > start_time);
    ''', (target_date, start_time, end_time))
    assigned_busy_ids = {row['projector_id'] for row in cursor.fetchall()}

    cursor.execute('''
        SELECT COUNT(*)
        FROM reservations
        WHERE reservation_date = ?
          AND projector_id IS NULL
          AND status = 'Pending'
          AND (? < end_time AND ? > start_time);
    ''', (target_date, start_time, end_time))
    unassigned_pending_count = cursor.fetchone()[0]

    conn.close()

    unit_statuses = []
    available_count = 0

    for proj in projectors:
        p_id = proj['projector_id']
        db_status = proj['status']

        if db_status == 'Under Maintenance':
            display_status = 'Maintenance'
            badge_class = 'pill-pending'
        elif db_status == 'Inactive':
            display_status = 'Inactive'
            badge_class = 'pill-returned'
        elif db_status == 'Borrowed' or p_id in assigned_busy_ids:
            display_status = 'In use'
            badge_class = 'pill-inuse'
        else:
            display_status = 'Available'
            badge_class = 'pill-approved'
            available_count += 1

        unit_statuses.append({
            'projector_id': p_id,
            'projector_code': proj['projector_code'],
            'model': f"{proj['brand']} {proj['model']}",
            'display_status': display_status,
            'badge_class': badge_class
        })

    effective_available = max(0, available_count - unassigned_pending_count)

    return {
        'total_units': len(projectors),
        'available_count': effective_available,
        'is_available': effective_available > 0,
        'units': unit_statuses
    }


def generate_next_reference_code(cursor):
    cursor.execute("SELECT reservation_code FROM reservations ORDER BY reservation_id DESC LIMIT 1;")
    last_row = cursor.fetchone()
    if not last_row:
        return "PLV-0001"

    last_code = last_row['reservation_code']
    try:
        num = int(last_code.split('-')[1]) + 1
        return f"PLV-{num:04d}"
    except (IndexError, ValueError):
        cursor.execute("SELECT COUNT(*) FROM reservations;")
        count = cursor.fetchone()[0] + 1
        return f"PLV-{count:04d}"


def get_or_create_classroom(cursor, classroom_name):
    clean_name = classroom_name.strip()
    cursor.execute("SELECT classroom_id FROM classrooms WHERE LOWER(classroom_name) = LOWER(?);", (clean_name,))
    row = cursor.fetchone()
    if row:
        return row['classroom_id']

    cursor.execute('''
        INSERT INTO classrooms (classroom_name, building, room_status)
        VALUES (?, 'PLV Campus', 'available');
    ''', (clean_name,))
    return cursor.lastrowid


@booking_bp.route('/api/availability')
@login_required
def api_availability():
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    slot_val = request.args.get('time_slot') or TIME_SLOTS[0]['value']
    slot = get_slot_info(slot_val)

    availability = compute_slot_availability(date_str, slot['start'], slot['end'])
    availability['formatted_summary'] = f"{format_short_date(date_str)}, {slot['short']}"
    return jsonify(availability)


@booking_bp.route('/student/reserve', methods=['GET', 'POST'])
@login_required
@role_required('student')
def reserve():
    today_date = datetime.now().strftime('%Y-%m-%d')
    existing_subjects, existing_classrooms = get_user_created_suggestions()

    form_data = {
        'section_name': session.get('section_name', ''),
        'subject_name': '',
        'instructor_name': '',
        'classroom_name': '',
        'reservation_date': today_date,
        'time_slot': TIME_SLOTS[0]['value'],
        'remarks': ''
    }

    submitted_reservation = None

    if request.method == 'POST':
        action = request.form.get('action', 'review')

        form_data['subject_name'] = request.form.get('subject_name', '').strip()
        form_data['instructor_name'] = request.form.get('instructor_name', '').strip()
        form_data['classroom_name'] = request.form.get('classroom_name', '').strip()
        form_data['reservation_date'] = request.form.get('reservation_date', today_date).strip()
        form_data['time_slot'] = request.form.get('time_slot', TIME_SLOTS[0]['value']).strip()
        form_data['remarks'] = request.form.get('remarks', '').strip()

        slot = get_slot_info(form_data['time_slot'])
        availability = compute_slot_availability(
            form_data['reservation_date'],
            slot['start'],
            slot['end']
        )
        availability['formatted_summary'] = f"{format_short_date(form_data['reservation_date'])}, {slot['short']}"

        if action == 'edit':
            return render_template(
                'student/reserve.html',
                step=1,
                form_data=form_data,
                time_slots=TIME_SLOTS,
                existing_subjects=existing_subjects,
                existing_classrooms=existing_classrooms,
                slot=slot,
                availability=availability,
                submitted_reservation=None
            )

        if not form_data['subject_name'] or not form_data['instructor_name'] or not form_data['classroom_name'] or not form_data['reservation_date']:
            flash('Please fill in Subject, Instructor, Room / Classroom, and Date.', 'danger')
            return render_template(
                'student/reserve.html',
                step=1,
                form_data=form_data,
                time_slots=TIME_SLOTS,
                existing_subjects=existing_subjects,
                existing_classrooms=existing_classrooms,
                slot=slot,
                availability=availability,
                submitted_reservation=None
            )

        if action == 'review':
            return render_template(
                'student/reserve.html',
                step=2,
                form_data=form_data,
                time_slots=TIME_SLOTS,
                existing_subjects=existing_subjects,
                existing_classrooms=existing_classrooms,
                slot=slot,
                formatted_full_date=format_full_date(form_data['reservation_date']),
                availability=availability,
                submitted_reservation=None
            )

        if action == 'confirm':
            section_id = session.get('section_id')
            if not section_id:
                flash('Your account is not assigned to a section yet. Please contact the Administrator.', 'danger')
                return redirect(url_for('student_dashboard'))

            if not availability['is_available']:
                flash('Sorry, no projector units are available for that date and time slot.', 'danger')
                return render_template(
                    'student/reserve.html',
                    step=1,
                    form_data=form_data,
                    time_slots=TIME_SLOTS,
                    existing_subjects=existing_subjects,
                    existing_classrooms=existing_classrooms,
                    slot=slot,
                    availability=availability,
                    submitted_reservation=None
                )

            conn = database.get_db()
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE;")

                recheck = compute_slot_availability(
                    form_data['reservation_date'],
                    slot['start'],
                    slot['end']
                )
                if not recheck['is_available']:
                    conn.rollback()
                    conn.close()
                    flash('Another section just reserved the last available unit for that slot.', 'danger')
                    return redirect(url_for('booking.reserve'))

                classroom_id = get_or_create_classroom(cursor, form_data['classroom_name'])
                ref_code = generate_next_reference_code(cursor)

                cursor.execute('''
                    INSERT INTO reservations (
                        reservation_code, user_id, projector_id, section_id,
                        subject_name, instructor_name, classroom_id,
                        reservation_date, start_time, end_time, remarks, status
                    )
                    VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending');
                ''', (
                    ref_code,
                    session['user_id'],
                    section_id,
                    form_data['subject_name'],
                    form_data['instructor_name'],
                    classroom_id,
                    form_data['reservation_date'],
                    slot['start'],
                    slot['end'],
                    form_data['remarks'] or None
                ))

                cursor.execute('''
                    INSERT INTO audit_logs (user_id, action, description)
                    VALUES (?, 'CREATE_RESERVATION', ?);
                ''', (
                    session['user_id'],
                    f"Section {form_data['section_name']} submitted reservation {ref_code} for {form_data['subject_name']} ({form_data['reservation_date']} {slot['label']})."
                ))

                conn.commit()
            except Exception as e:
                conn.rollback()
                conn.close()
                flash(f'Error submitting reservation: {str(e)}', 'danger')
                return redirect(url_for('booking.reserve'))

            conn.close()

            submitted_reservation = {
                'reservation_code': ref_code,
                'section_name': form_data['section_name'],
                'subject_name': form_data['subject_name'],
                'instructor_name': form_data['instructor_name'],
                'classroom_name': form_data['classroom_name'],
                'formatted_date': format_full_date(form_data['reservation_date']),
                'time_label': slot['label'],
                'remarks': form_data['remarks'] or 'None'
            }

            return render_template(
                'student/reserve.html',
                step=3,
                form_data=form_data,
                time_slots=TIME_SLOTS,
                existing_subjects=existing_subjects,
                existing_classrooms=existing_classrooms,
                slot=slot,
                availability=availability,
                submitted_reservation=submitted_reservation
            )

    slot = get_slot_info(form_data['time_slot'])
    availability = compute_slot_availability(form_data['reservation_date'], slot['start'], slot['end'])
    availability['formatted_summary'] = f"{format_short_date(form_data['reservation_date'])}, {slot['short']}"

    return render_template(
        'student/reserve.html',
        step=1,
        form_data=form_data,
        time_slots=TIME_SLOTS,
        existing_subjects=existing_subjects,
        existing_classrooms=existing_classrooms,
        slot=slot,
        availability=availability,
        submitted_reservation=None
    )


@booking_bp.route('/student/reservations', methods=['GET', 'POST'])
@login_required
@role_required('student')
def my_reservations():
    """Displays all reservations for the logged-in section representative with status filters and cancellation."""
    conn = database.get_db()
    cursor = conn.cursor()
    user_id = session['user_id']

    if request.method == 'POST':
        action = request.form.get('action')
        res_id = request.form.get('reservation_id', type=int)

        if action == 'cancel' and res_id:
            cursor.execute('''
                SELECT reservation_code, status FROM reservations
                WHERE reservation_id = ? AND user_id = ?;
            ''', (res_id, user_id))
            target = cursor.fetchone()

            if target and target['status'] in ('Pending', 'Approved'):
                cursor.execute('''
                    UPDATE reservations
                    SET status = 'Cancelled', projector_id = NULL
                    WHERE reservation_id = ?;
                ''', (res_id,))
                cursor.execute('''
                    INSERT INTO audit_logs (user_id, action, description)
                    VALUES (?, 'CANCEL_RESERVATION', ?);
                ''', (user_id, f"Section {session.get('section_name')} cancelled reservation {target['reservation_code']}."))
                conn.commit()
                flash(f"Reservation {target['reservation_code']} has been cancelled.", 'info')
            else:
                flash('Only Pending or Approved reservations can be cancelled.', 'danger')

        conn.close()
        return redirect(url_for('booking.my_reservations', status=request.args.get('status', 'All')))

    status_filter = request.args.get('status', 'All')

    query = '''
        SELECT r.reservation_id, r.reservation_code, r.subject_name, r.instructor_name,
               r.reservation_date, r.start_time, r.end_time, r.remarks, r.status,
               r.rejection_reason, c.classroom_name, p.projector_code, p.projector_name
        FROM reservations r
        JOIN classrooms c ON r.classroom_id = c.classroom_id
        LEFT JOIN projectors p ON r.projector_id = p.projector_id
        WHERE r.user_id = ?
    '''
    params = [user_id]

    if status_filter == 'Returned':
        query += " AND r.status = 'Completed'"
    elif status_filter in ('Pending', 'Approved', 'Released', 'Rejected', 'Cancelled'):
        query += " AND r.status = ?"
        params.append(status_filter)

    query += " ORDER BY r.reservation_date DESC, r.start_time DESC;"
    cursor.execute(query, params)
    rows = cursor.fetchall()

    reservations = []
    for r in rows:
        disp_status = 'Returned' if r['status'] == 'Completed' else r['status']
        reservations.append({
            'reservation_id': r['reservation_id'],
            'reservation_code': r['reservation_code'],
            'subject_name': r['subject_name'],
            'instructor_name': r['instructor_name'],
            'classroom_name': r['classroom_name'],
            'formatted_date': format_full_date(r['reservation_date']),
            'time_range': f"{format_time_12h(r['start_time'])} – {format_time_12h(r['end_time'])}",
            'remarks': r['remarks'] or 'None',
            'status': disp_status,
            'raw_status': r['status'],
            'projector_label': f"{r['projector_code']} ({r['projector_name']})" if r['projector_code'] else 'Not yet assigned',
            'rejection_reason': r['rejection_reason'] or ''
        })

    conn.close()
    return render_template(
        'student/reservations.html',
        reservations=reservations,
        status_filter=status_filter
    )

@booking_bp.route('/student/reservations/<int:reservation_id>')
@login_required
@role_required('student')
def view_reservation(reservation_id):
    """View single reservation details & status timeline."""
    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT r.*, s.section_name, p.projector_code
        FROM reservations r
        LEFT JOIN sections s ON r.section_id = s.section_id
        LEFT JOIN projectors p ON r.projector_id = p.projector_id
        WHERE r.reservation_id = ? AND r.user_id = ?;
    ''', (reservation_id, session['user_id']))
    row = cursor.fetchone()
    conn.close()

    if not row:
        flash('Reservation not found.', 'warning')
        return redirect(url_for('booking.my_reservations'))

    detail = dict(row)
    detail['room'] = detail.get('classroom_name') or 'N/A'
    detail['date_label'] = detail.get('reservation_date') or 'N/A'
    detail['submitted_label'] = detail.get('created_at') or 'N/A'

    slot_val = f"{detail.get('start_time', '')}-{detail.get('end_time', '')}"
    matched_slot = next((s for s in TIME_SLOTS if s['value'] == slot_val), None)
    detail['time_label'] = matched_slot['label'] if matched_slot else slot_val

    status_lower = (detail.get('status') or '').strip().lower()
    is_approved = status_lower in ('approved', 'released', 'completed', 'returned')
    is_released = status_lower in ('released', 'completed', 'returned')
    is_returned = status_lower in ('completed', 'returned')
    is_rejected = status_lower in ('rejected', 'declined', 'cancelled')

    return render_template(
        'student/view_reservation.html',
        detail=detail,
        reservation=detail,
        is_approved=is_approved,
        is_released=is_released,
        is_returned=is_returned,
        is_rejected=is_rejected
    )

@booking_bp.route('/student/reservations/<int:reservation_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('student')
def edit_reservation(reservation_id):
    """Edit a pending reservation."""
    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT r.*, s.section_name
        FROM reservations r
        LEFT JOIN sections s ON r.section_id = s.section_id
        WHERE r.reservation_id = ? AND r.user_id = ?;
    ''', (reservation_id, session['user_id']))
    reservation = cursor.fetchone()

    if not reservation:
        conn.close()
        flash('Reservation not found.', 'warning')
        return redirect(url_for('booking.my_reservations'))

    if reservation['status'] != 'Pending':
        conn.close()
        flash('Only pending reservations can be edited.', 'warning')
        return redirect(url_for('booking.my_reservations'))

    if request.method == 'POST':
        subject_name = request.form.get('subject_name', '').strip()
        instructor_name = request.form.get('instructor_name', '').strip()
        classroom_name = request.form.get('classroom_name', '').strip()
        reservation_date = request.form.get('reservation_date', '').strip()
        time_slot = request.form.get('time_slot', '').strip()
        remarks = request.form.get('remarks', '').strip()

        slot = next((s for s in TIME_SLOTS if s['value'] == time_slot), None)

        if not all([subject_name, instructor_name, classroom_name, reservation_date, slot]):
            flash('Please fill in all required fields.', 'danger')
        else:
            cursor.execute('''
                UPDATE reservations
                SET subject_name = ?, instructor_name = ?, classroom_name = ?,
                    reservation_date = ?, start_time = ?, end_time = ?, remarks = ?
                WHERE reservation_id = ? AND user_id = ?;
            ''', (
                subject_name, instructor_name, classroom_name,
                reservation_date, slot['start'], slot['end'], remarks or None,
                reservation_id, session['user_id']
            ))
            conn.commit()
            conn.close()
            flash('Reservation updated successfully.', 'success')
            return redirect(url_for('booking.my_reservations'))

    conn.close()
    return render_template(
        'student/edit_reservation.html',
        reservation=reservation,
        detail=reservation,
        time_slots=TIME_SLOTS
    )


@booking_bp.route('/student/reservations/<int:reservation_id>/cancel', methods=['POST'])
@login_required
@role_required('student')
def cancel_reservation(reservation_id):
    """Cancel a pending reservation."""
    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE reservations
        SET status = 'Cancelled'
        WHERE reservation_id = ? AND user_id = ? AND status = 'Pending';
    ''', (reservation_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('Reservation cancelled.', 'info')
    return redirect(url_for('booking.my_reservations'))

@booking_bp.route('/student/profile', methods=['GET', 'POST'])
@login_required
@role_required('student')
def profile():
    """User Profile (View, Edit Information, & Change Password)."""
    conn = database.get_db()
    cursor = conn.cursor()
    user_id = session['user_id']

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_info':
            full_name = request.form.get('full_name', '').strip()
            email = request.form.get('email', '').strip()
            contact_number = request.form.get('contact_number', '').strip()

            if not full_name:
                flash('Representative Full Name cannot be empty.', 'danger')
            else:
                cursor.execute('''
                    UPDATE users
                    SET full_name = ?, email = ?, contact_number = ?
                    WHERE user_id = ?;
                ''', (full_name, email or None, contact_number or None, user_id))
                session['full_name'] = full_name
                session['email'] = email or 'Not provided'
                conn.commit()
                flash('Personal information updated successfully.', 'success')

        elif action == 'change_password':
            current_pw = request.form.get('current_password', '')
            new_pw = request.form.get('new_password', '')
            confirm_pw = request.form.get('confirm_password', '')

            cursor.execute("SELECT password_hash FROM users WHERE user_id = ?;", (user_id,))
            row = cursor.fetchone()

            if not row or not check_password_hash(row['password_hash'], current_pw):
                flash('Current password is incorrect.', 'danger')
            elif len(new_pw) < 6:
                flash('New password must be at least 6 characters long.', 'danger')
            elif new_pw != confirm_pw:
                flash('New passwords do not match.', 'danger')
            else:
                cursor.execute("UPDATE users SET password_hash = ? WHERE user_id = ?;", (generate_password_hash(new_pw), user_id))
                cursor.execute('''
                    INSERT INTO audit_logs (user_id, action, description)
                    VALUES (?, 'CHANGE_PASSWORD', ?);
                ''', (user_id, f"Representative '{session.get('username')}' changed their password."))
                conn.commit()
                flash('Password changed successfully!', 'success')

    cursor.execute('''
        SELECT u.school_id_number, u.full_name, u.username, u.email, u.contact_number,
               s.section_name, s.year_level, s.course
        FROM users u
        LEFT JOIN sections s ON u.section_id = s.section_id
        WHERE u.user_id = ?;
    ''', (user_id,))
    user_info = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM reservations WHERE user_id = ?;", (user_id,))
    total_reqs = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM reservations WHERE user_id = ? AND status = 'Pending';", (user_id,))
    pending_reqs = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM reservations WHERE user_id = ? AND status = 'Completed';", (user_id,))
    returned_reqs = cursor.fetchone()[0]

    conn.close()
    return render_template(
        'student/profile.html',
        user_info=user_info,
        stats={'total': total_reqs, 'pending': pending_reqs, 'returned': returned_reqs}
    )