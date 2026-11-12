"""Gerenciamento local de usuários do TokenScope (não expõe rotas HTTP)."""
import argparse
from getpass import getpass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app import app
from auth import User, db, clean_email


def migrate():
    """Remove apenas a restrição antiga de conta única. Preserva os registros."""
    with app.app_context():
        with db.engine.begin() as conn:
            conn.execute(text('ALTER TABLE public.users DROP CONSTRAINT IF EXISTS ck_tokenscope_single_user'))
            seq = conn.execute(text("SELECT pg_get_serial_sequence('public.users', 'id')")).scalar()
            if seq:
                conn.execute(text('SELECT setval(CAST(:seq AS regclass), '
                                  'GREATEST(COALESCE((SELECT MAX(id) FROM public.users), 0), '
                                  'nextval(CAST(:seq AS regclass))), true)'), {'seq': seq})
    print('Migração concluída. Usuários existentes preservados.')


def create():
    name = input('Nome completo: ').strip()
    email = clean_email(input('E-mail: '))
    if not 2 <= len(name) <= 120:
        raise ValueError('O nome deve conter de 2 a 120 caracteres.')
    if len(email) > 254 or '@' not in email or email.startswith('@') or email.endswith('@') or ' ' in email:
        raise ValueError('Informe um e-mail válido.')
    password = getpass('Senha (não aparece na tela): ')
    confirm = getpass('Confirme a senha: ')
    if not 12 <= len(password) <= 128:
        raise ValueError('A senha deve conter de 12 a 128 caracteres.')
    if password != confirm:
        raise ValueError('As senhas não coincidem.')
    with app.app_context():
        if User.query.filter_by(email=email).first():
            raise ValueError('Já existe uma conta com esse e-mail.')
        user = User(name=name, email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise ValueError('Não foi possível criar a conta. Execute a migração primeiro e confira o e-mail.') from None
    print(f'Conta criada com sucesso para {email}.')


def list_users():
    with app.app_context():
        records = db.session.query(User.id, User.name, User.email).order_by(User.id).all()
        if not records:
            print('Nenhuma conta cadastrada.')
        for id_, name, email in records:
            print(f'{id_:>4} | {name} | {email}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Administração local de contas do TokenScope')
    parser.add_argument('acao', choices=['migrate', 'criar', 'listar'])
    args = parser.parse_args()
    try:
        if args.acao == 'migrate':
            migrate()
        elif args.acao == 'criar':
            create()
        else:
            list_users()
    except (ValueError, OSError) as exc:
        parser.exit(1, f'Erro: {exc}\n')
