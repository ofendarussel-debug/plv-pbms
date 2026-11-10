
import sqlite3
import os
from werkzeug.security import generate_password_hash
from config import Config


# DATABASE CONNECTION
def get_db():
    db_dir = os.path.dirname(Config.DATABASE_PATH)

    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    return conn


# Database
def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # SECTIONS
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sections (
            section_id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_name TEXT UNIQUE NOT NULL,
            year_level INTEGER NOT NULL,
            course TEXT NOT NULL
        );
    ''')

    # CLASSROOMS
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS classrooms (
            classroom_id INTEGER PRIMARY KEY AUTOINCREMENT,
            classroom_name TEXT UNIQUE NOT NULL,
            building TEXT NOT NULL
        );
    ''')

    # USERS
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
                CHECK(role IN ('student', 'teacher', 'admin')),
            section_id INTEGER NULL,
            account_status TEXT NOT NULL DEFAULT 'active'
                CHECK(account_status IN ('active', 'inactive')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (section_id)
                REFERENCES sections(section_id)
                ON DELETE SET NULL
        );
    ''')

    # PROJECTORS
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS projectors (
            projector_name TEXT PRIMARY KEY NOT NULL,
            brand TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Available'
                CHECK(status IN (
                    'Available',
                    'Borrowed',
                    'Under Maintenance',
                    'Inactive'
                )),
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')

    # RESERVATIONS
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reservations (
            reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_code TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            projector_name TEXT NOT NULL,
            classroom_id INTEGER NOT NULL,
            section_id INTEGER NOT NULL,
            reservation_date DATE NOT NULL,
            start_time TIME NOT NULL,
            end_time TIME NOT NULL,
            purpose TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending'
                CHECK(status IN (
                    'Pending',
                    'Approved',
                    'Rejected',
                    'Cancelled',
                    'Completed'
                )),
            approved_by INTEGER NULL,
            rejection_reason TEXT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (user_id)
                REFERENCES users(user_id),

            FOREIGN KEY (projector_name)
                REFERENCES projectors(projector_name),

            FOREIGN KEY (classroom_id)
                REFERENCES classrooms(classroom_id),

            FOREIGN KEY (section_id)
                REFERENCES sections(section_id),

            FOREIGN KEY (approved_by)
                REFERENCES users(user_id)
        );
    ''')

    # BORROWING 
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS borrowing_records (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id INTEGER NOT NULL,
            released_by INTEGER NOT NULL,
            received_by INTEGER NULL,
            borrowed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            returned_at TIMESTAMP NULL,
            borrowing_status TEXT NOT NULL DEFAULT 'Borrowed'
                CHECK(borrowing_status IN (
                    'Not Released',
                    'Borrowed',
                    'Returned',
                    'Overdue'
                )),
            remarks TEXT NULL,

            FOREIGN KEY (reservation_id)
                REFERENCES reservations(reservation_id),

            FOREIGN KEY (released_by)
                REFERENCES users(user_id),

            FOREIGN KEY (received_by)
                REFERENCES users(user_id)
        );
    ''')

    # AUDIT 
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NULL,
            action TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (user_id)
                REFERENCES users(user_id)
                ON DELETE SET NULL
        );
    ''')

    # DATABASE INDEXES
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_res_search
        ON reservations(reservation_date, projector_name, status);
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_borrow_status
        ON borrowing_records(borrowing_status);
    ''')

    conn.commit()
    conn.close()

    print("Database Initialized Successfully")


def seed_sample_data():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT 1 FROM users
        WHERE role = 'admin'
        LIMIT 1;
    ''')

    if cursor.fetchone() is None:

        
        hashed_pwd = generate_password_hash('admin')

        cursor.execute('''
            INSERT INTO users (
                full_name,
                username,
                password_hash,
                role,
                section_id,
                account_status
            )
            VALUES (?, ?, ?, ?, ?, ?);
        ''', (
            'System Administrator',
            'admin',
            hashed_pwd,
            'admin',
            None,
            'active'
        ))

        print("Initial administrator account created.")

    else:
        print("Administrator account already exists.")

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    seed_sample_data()
