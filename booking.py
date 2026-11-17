from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
import database
from auth import login_required, role_required

booking_bp = Blueprint('booking', __name__)

TIME_SLOTS = [
    {"value": "08:00-10:00", "label": "8:00 AM - 10:00 AM", "short": "8:00–10:00 AM", "start": "08:00", "end": "10:00"},
    {"value": "10:00-12:00", "label": "10:00 AM - 12:00 PM", "short": "10:00 AM–12:00 PM", "start": "10:00", "end": "12:00"},
    {"value": "13:00-15:00", "label": "1:00 PM - 3:00 PM", "short": "1:00–3:00 PM", "start": "13:00", "end": "15:00"},
    {"value": "15:00-17:00", "label": "3:00 PM - 5:00 PM", "short": "3:00–5:00 PM", "start": "15:00", "end": "17:00"},
    {"value": "17:00-19:00", "label": "5:00 PM - 7:00 PM", "short": "5:00–7:00 PM", "start": "17:00", "end": "19:00"},
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
          AND status IN ('Pending', 'Approved')
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