"""Autenticação local do TokenScope com PostgreSQL.

Acesso restrito: contas criadas exclusivamente pelo administrador no terminal.
"""
import os
import secrets
import time
import threading
from datetime import timedelta
from pathlib import Path

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
auth = Blueprint('auth', __name__)
_attempts = {}
_attempts_lock = threading.Lock()


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (ValueError, TypeError):
        return None


def get_or_create_secret(app):
    configured = os.getenv('SECRET_KEY', '').strip()
    if configured:
        if len(configured) < 32:
            raise RuntimeError('SECRET_KEY precisa ter pelo menos 32 caracteres.')
        return configured
    instance = Path(app.instance_path)
    instance.mkdir(parents=True, exist_ok=True)
    secret_file = instance / '.secret_key'
    if not secret_file.exists():
        try:
            with secret_file.open('x', encoding='utf-8') as f:
                f.write(secrets.token_urlsafe(48))
        except FileExistsError:
            pass
    return secret_file.read_text(encoding='utf-8').strip()


def init_auth(app):
    url = os.getenv('DATABASE_URL', '').strip()
    if not url:
        raise RuntimeError('Configure DATABASE_URL no .env para usar o PostgreSQL (veja README.md).')
    if not url.startswith(('postgresql+psycopg://', 'postgresql://')):
        raise RuntimeError('DATABASE_URL precisa usar PostgreSQL, não SQLite.')
    app.config.update(
        SECRET_KEY=get_or_create_secret(app),
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE','0') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        WTF_CSRF_TIME_LIMIT=3600,
        MAX_CONTENT_LENGTH=1_000_000,
    )
    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Entre na sua conta para acessar o TokenScope.'
    login_manager.login_message_category = 'info'
    app.register_blueprint(auth)
    with app.app_context():
        db.create_all()


def clean_email(value):
    return (value or '').strip().lower()


def limited(identifier):
    now = time.monotonic()
    with _attempts_lock:
        # Atenção: proteção local em memória para esta versão de usuário único.
        attempts = [t for t in _attempts.get(identifier, []) if now - t < 900]
        _attempts[identifier] = attempts
        return len(attempts) >= 5


def failed(identifier):
    with _attempts_lock:
        _attempts.setdefault(identifier, []).append(time.monotonic())


def cleared(identifier):
    with _attempts_lock:
        _attempts.pop(identifier, None)


@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = clean_email(request.form.get('email'))
        password = request.form.get('password') or ''
        client = request.remote_addr or 'local'
        identifier = f'{client}:{email}'
        if limited(identifier) or limited(f'ip:{client}'):
            flash('Muitas tentativas. Tente novamente em 15 minutos.', 'error')
        else:
            user = User.query.filter_by(email=email).first()
            if user and check_password_hash(user.password_hash, password):
                cleared(identifier)
                login_user(user, fresh=True)
                session.permanent = True
                flash('Login realizado com sucesso.', 'success')
                return redirect(url_for('index'))
            failed(identifier)
            failed(f'ip:{client}')
            flash('E-mail ou senha incorretos.', 'error')
    return render_template('login.html', title='Entrar')


@auth.post('/sair')
@login_required
def logout():
    logout_user()
    session.clear()
    flash('Você saiu da sua conta.', 'info')
    return redirect(url_for('auth.login'))
