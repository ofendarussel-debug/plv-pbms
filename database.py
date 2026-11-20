import os
import sqlite3
from werkzeug.security import generate_password_hash
from config import Config


def get_db():
    """Opens a connection to the SQLite database with foreign keys enabled."""
    db_dir = os.path.dirname(Config.DATABASE_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    """Creates all required tables, constraints, and indexes."""
    conn = get_db()
    cursor = conn.cursor()

    # 1. SECTIONS TABLE (Created dynamically as representatives register)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sections (
            section_id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_name TEXT UNIQUE NOT NULL,
            year_level INTEGER NOT NULL DEFAULT 1,
            course TEXT NOT NULL DEFAULT 'GENERAL'
        );
    ''')

    # 2. CLASSROOMS TABLE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS classrooms (
            classroom_id INTEGER PRIMARY KEY AUTOINCREMENT,
            classroom_name TEXT UNIQUE NOT NULL,
            building TEXT NOT NULL,
            room_status TEXT NOT NULL DEFAULT 'available' CHECK(room_status IN ('available', 'unavailable'))
        );
    ''')

    # 3. USERS TABLE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id_number TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            email TEXT NULL,
            contact_number TEXT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('student', 'admin')),
            section_id INTEGER NULL,
            account_status TEXT NOT NULL DEFAULT 'active' CHECK(account_status IN ('active', 'inactive')),
            last_login TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (section_id) REFERENCES sections(section_id) ON DELETE SET NULL
        );
    ''')

    # Enforce strictly ONE student representative account per section at the database level
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_one_rep_per_section
        ON users(section_id)
        WHERE role = 'student' AND section_id IS NOT NULL;
    ''')

    # 4. PROJECTORS TABLE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS projectors (
            projector_id INTEGER PRIMARY KEY AUTOINCREMENT,
            projector_code TEXT UNIQUE NOT NULL,
            projector_name TEXT NOT NULL,
            brand TEXT NOT NULL,
            model TEXT NOT NULL,
            condition TEXT NOT NULL DEFAULT 'Good',
            status TEXT NOT NULL DEFAULT 'Available' CHECK(status IN ('Available', 'Borrowed', 'Under Maintenance', 'Inactive')),
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')

    # 5. RESERVATIONS TABLE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reservations (
            reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_code TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            projector_id INTEGER NULL,
            section_id INTEGER NOT NULL,
            subject_name TEXT NOT NULL,
            instructor_name TEXT NOT NULL,
            classroom_id INTEGER NOT NULL,
            reservation_date DATE NOT NULL,
            start_time TIME NOT NULL,
            end_time TIME NOT NULL,
            remarks TEXT NULL,
            status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending', 'Approved', 'Released', 'Rejected', 'Cancelled', 'Completed')),
            processed_by INTEGER NULL,
            approved_at TIMESTAMP NULL,
            rejection_reason TEXT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (projector_id) REFERENCES projectors(projector_id),
            FOREIGN KEY (section_id) REFERENCES sections(section_id),
            FOREIGN KEY (classroom_id) REFERENCES classrooms(classroom_id),
            FOREIGN KEY (processed_by) REFERENCES users(user_id)
        );
    ''')

    # 6. USAGE RECORDS TABLE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usage_records (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id INTEGER NOT NULL,
            released_by INTEGER NOT NULL,
            received_by INTEGER NULL,
            released_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            returned_at TIMESTAMP NULL,
            condition_before TEXT NOT NULL,
            condition_after TEXT NULL,
            usage_status TEXT NOT NULL DEFAULT 'In Use' CHECK(usage_status IN ('In Use', 'Returned', 'Overdue')),
            notes TEXT NULL,
            FOREIGN KEY (reservation_id) REFERENCES reservations(reservation_id),
            FOREIGN KEY (released_by) REFERENCES users(user_id),
            FOREIGN KEY (received_by) REFERENCES users(user_id)
        );
    ''')

    # 7. AUDIT LOGS TABLE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NULL,
            action TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
        );
    ''')

    # Performance Indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_res_date_status ON reservations(reservation_date, status);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_res_projector ON reservations(projector_id, reservation_date);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_res_user ON reservations(user_id, status);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_usage_status ON usage_records(usage_status);')

    conn.commit()
    conn.close()
    print("PLV Database schema initialized successfully.")


def admin_exists():
    """Returns True if the single Administrator account already exists."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin';")
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0


def seed_initial_projectors():
    """Populates the 5 PLV projector inventory units (PLV-PRJ-001 to PLV-PRJ-005)."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM projectors;")
    if cursor.fetchone()[0] == 0:
        cursor.executemany('''
            INSERT INTO projectors (projector_code, projector_name, brand, model, condition, status)
            VALUES (?, ?, ?, ?, ?, ?);
        ''', [
            ('PLV-PRJ-001', 'Projector A', 'Epson', 'EB-X06', 'Good', 'Available'),
            ('PLV-PRJ-002', 'Projector B', 'Epson', 'EB-X06', 'Good', 'Available'),
            ('PLV-PRJ-003', 'Projector C', 'BenQ', 'MX550', 'Good', 'Available'),
            ('PLV-PRJ-004', 'Projector D', 'BenQ', 'MX550', 'Good', 'Available'),
            ('PLV-PRJ-005', 'Projector E', 'Epson', 'EB-X06', 'Good', 'Available')
        ])
        conn.commit()

    conn.close()


def seed_demo_data():
    """Seeds Franchezca Mhaica Olivar (BSIT 2-2) without pre-populating any other sections."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT OR IGNORE INTO sections (section_name, year_level, course)
        VALUES ('BSIT 2-2', 2, 'BSIT');
    ''')
    cursor.execute("SELECT section_id FROM sections WHERE section_name = 'BSIT 2-2';")
    sec_row = cursor.fetchone()
    if not sec_row:
        conn.close()
        return
    section_id = sec_row['section_id']

    cursor.execute("SELECT user_id FROM users WHERE role = 'student' AND section_id = ?;", (section_id,))
    if not cursor.fetchone():
        cursor.execute('''
            INSERT OR IGNORE INTO users (
                school_id_number, full_name, username, email,
                password_hash, role, section_id, account_status
            )
            VALUES (?, ?, ?, ?, ?, 'student', ?, 'active');
        ''', (
            '102825',
            'Franchezca Mhaica Olivar',
            'cheeze',
            'chezca@gmail.com',
            generate_password_hash('chezca1028'),
            section_id
        ))
        conn.commit()

    conn.close()

def seed_admin_account():
    """Creates the single default Administrator account so /setup is never needed."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin';")
        if cursor.fetchone()[0] == 0:
            cursor.execute('''
                INSERT INTO users (
                    school_id_number, full_name, username, email,
                    password_hash, role, section_id, account_status
                )
                VALUES (?, ?, ?, ?, ?, 'admin', NULL, 'active');
            ''', (
                'ADMIN-001',
                'PLV System Administrator',
                'admin',
                'admin@plv.edu.ph',
                generate_password_hash('admin')
            ))
            conn.commit()
            print("Admin account ready -> Username: admin | Password: admin")
    finally:
        conn.close()

if __name__ == '__main__':
    init_db()
    seed_initial_projectors()
    seed_admin_account()
    seed_demo_data()
    print("Database ready with 5 PLV projector units and open section registration.")