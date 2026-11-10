from flask import Flask, render_template, request, redirect, url_for, flash
from config import Config
import database

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    with app.app_context():
        database.init_db()
        database.seed_sample_data()

    @app.route('/')
    def index():
        return render_template('login.html')

    @app.route('/health')
    def health_check():
        return {"status": "online", "system": "Projector Borrowing and Monitoring System (PBMS)"}, 200

    return app

app = create_app()

if __name__  == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)