import webbrowser
from flask import Flask, request, redirect
from flask_sqlalchemy import SQLAlchemy
import requests
import time
import colorlog
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import json

# Configura el manejador para la salida de la consola
console_handler = colorlog.StreamHandler()
console_handler.setFormatter(colorlog.ColoredFormatter(
    '%(log_color)s%(levelname)s: %(message)s',
    log_colors={
        'DEBUG':    'blue',
        'INFO':     'green',
        'WARN':     'orange',
        'WARNING':  'yellow',
        'ERROR':    'red',
        'CRITICAL': 'red,bg_white',
    }
))

# Configura el manejador para el archivo de registro
file_handler = logging.FileHandler('livechat_errors.log')
file_handler.setLevel(logging.ERROR)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

# Crea un logger personalizado
logger = colorlog.getLogger('example')
logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.setLevel(logging.DEBUG)

# Carga las variables de entorno del archivo .env en el directorio actual
load_dotenv()


# Configura tus credenciales aquí
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI') or 'http://localhost:8088/callback'
CREATE_JSON_FILES = os.getenv('CREATE_JSON_FILES') or "0"
CHAT_COUNT = 0

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///livechat_data.db'
db = SQLAlchemy(app)

# Define the chats table


class User(db.Model):
    id = db.Column(db.String, primary_key=True)
    name = db.Column(db.String)
    email = db.Column(db.String)

    def __init__(self, id, name, email):
        self.id = id
        self.name = name
        self.email = email


class Chat(db.Model):
    id = db.Column(db.String, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('user.id'))
    agent_id = db.Column(db.String, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime)

    def __init__(self, id, created_at, user_id, agent_id):
        self.id = id
        self.created_at = created_at
        self.user_id = user_id
        self.agent_id = agent_id


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    chat_id = db.Column(db.String, db.ForeignKey('chat.id'), nullable=False)
    author_id = db.Column(db.String, db.ForeignKey('user.id'))
    message = db.Column(db.String)
    created_at = db.Column(db.DateTime)

    def __init__(self, chat_id, message, author_id, created_at):
        self.chat_id = chat_id
        self.message = message
        self.author_id = author_id
        self.created_at = created_at


class Migration(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    page_id = db.Column(db.Integer)
    last_record = db.Column(db.String)

    def __init__(self, page_id, last_record):
        self.page_id = page_id
        self.last_record = last_record


class Token(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    access_token = db.Column(db.String, nullable=False)
    refresh_token = db.Column(db.String, nullable=False)
    expires_in = db.Column(db.Integer, nullable=False)
    expiry_time = db.Column(db.DateTime, nullable=False)

    def __init__(self, access_token, refresh_token, expires_in):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in
        self.expiry_time = datetime.now() + timedelta(seconds=expires_in)


# Create the chats table if it doesn't exist
with app.app_context():
    db.create_all()


@app.route('/')
def index():
    # Redirige a la página de autorización de LiveChat
    url = f'https://accounts.livechat.com/?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}'
    print('Redirigendo a ' + url)
    return redirect(url, code=302)


@app.route('/callback')
def callback():
    code = request.args.get('code')
    if code:
        token = refresh_token_if_expired(code)
        if token:
            result = load_get_chats(token)
            return "Token de acceso obtenido: " + token + ". " + result
        else:
            return "No se pudo obtener el token de acceso"
    else:
        return "No se proporcionó código en la redirección"


@app.route('/load_status')
def load_status():
    last_migration = db.session.query(
        Migration).order_by(Migration.id.desc()).first()
    if last_migration:
        return f"Última página cargada: {last_migration.last_page}. Último registro: {last_migration.last_record}."
    else:
        return "La carga de chats aún no ha comenzado."


def get_access_token(code):
    # Realiza una solicitud POST para obtener un token de acceso utilizando el código proporcionado
    url = 'https://accounts.livechat.com/v2/token'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'redirect_uri': REDIRECT_URI
    }
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        token_info = response.json()
        access_token = token_info.get('access_token')
        refresh_token = token_info.get('refresh_token')
        expires_in = token_info.get('expires_in')
        token = Token(access_token, refresh_token, expires_in)
        db.session.add(token)
        db.session.commit()
        return access_token
    else:
        return None


def refresh_token_if_expired(code):
    token = db.session.query(Token).order_by(Token.id.desc()).first()
    if token is None:
        return get_access_token(code)
    elif datetime.now() >= token.expiry_time:
        logger.critical('Token expirado, refrescando...')
        url = 'https://accounts.livechat.com/v2/token'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': token.refresh_token,
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
        }
        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 200:
            token_info = response.json()
            new_access_token = token_info.get('access_token')
            new_refresh_token = token_info.get('refresh_token')
            new_expires_in = token_info.get('expires_in')
            new_token = Token(new_access_token,
                              new_refresh_token, new_expires_in)
            db.session.add(new_token)
            db.session.commit()
            return new_access_token
        else:
            return None
    else:
        return token.access_token


def load_get_chats(token):
    global CHAT_COUNT
    page_id, last_record = get_last_migration_info()
    while True:
        next_page_id, last_record = get_chats(
            token, page_id, last_record)
        page_id = next_page_id
        if page_id is None:
            # Aquí el token ya pudo haber expirado o hubo algún error
            break
        update_migration_info(page_id, last_record)
    return "Se han guardado " + str(CHAT_COUNT) + " chats en la base de datos."


def get_chats(token, page_id=None, last_record=None):
    global CHAT_COUNT
    global CREATE_JSON_FILES

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }

    data = {}

    if page_id is not None:
        logger.warning(f'Buscando siguiente página: {page_id} type: {type(page_id)}')
        data['page_id'] = page_id

    try:
        response = requests.post(
            'https://api.livechatinc.com/v3.5/agent/action/list_archives', headers=headers, json=data)

        if response.status_code == 200:
            chats = response.json()['chats']

            if CREATE_JSON_FILES == "1":
                # Crear el directorio si no existe
                if not os.path.exists('JSONData'):
                    os.makedirs('JSONData')

                # Crear el nombre del archivo
                filename = f'JSONData/chats-page{page_id}-{time.time()}.json'

                # Abrir el archivo para escribir
                with open(filename, 'w') as json_file:
                    json.dump(response.json(), json_file)

            for chat in chats:
                id = chat['id']
                created_at = datetime.strptime(
                    chat['thread']['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ")
                users = chat['users']
                user_ids = [user['id'] for user in users]
                user_names = [user.get('name', 'None') for user in users]
                user_emails = [user.get('email', 'None') for user in users]
                chat_messages = [{
                    'text': event['text'], 
                    'created_at': datetime.strptime(event['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ"), 
                    'author_id': event['author_id']
                } for event in chat['thread']['events'] if event['type'] == 'message']

                for idx, user_id in enumerate(user_ids):
                    get_or_create_user(
                        user_id, user_names[idx] or None, user_emails[idx] or None)

                existing_chat = db.session.get(Chat, id)

                if existing_chat is None:
                    new_chat = Chat(id, created_at, user_ids[0], user_ids[1] if len(
                        user_ids) > 1 else None)
                    try:
                        db.session.add(new_chat)
                        db.session.commit()
                        logger.info('Creando nuevo chat...')
                        CHAT_COUNT = CHAT_COUNT+1 #Aumenta el contador sólo cuando no es un chat repetido o que se omite
                    except Exception as e:
                        logger.error(f'Error on update dataBase with {new_chat}')
                        logger.error('Error occurred: {}'.format(e))
                        return None, last_record

                for message_data in chat_messages:
                    new_message = Message(id, message_data['text'], message_data['author_id'], message_data['created_at'])
                    db.session.add(new_message)

                user_names_str = ' & '.join(user_names)
                db.session.commit()
                logger.info(
                    f'--- No. ({CHAT_COUNT}) Guardado chat entre: {user_names_str}')

            # Actualiza el último registro migrado
            last_record = chats[-1]['id'] if chats else last_record
            next_page_id = response.json().get('next_page_id')
            return next_page_id, last_record

        else:
            logger.error('Failed to retrieve chats. Status code: {}. Response: {}'.format(
                response.status_code, response.text))
            return None, last_record

    except Exception as e:
        logger.error('Error occurred: {}'.format(e))
        return None, last_record


def get_or_create_user(user_id, name='None', email='None'):
    user = db.session.get(User, user_id)
    if user is None:
        user = User(user_id, name, email)
        db.session.add(user)
        db.session.commit()
        logger.debug(f'Creado usuario no existente: {name} - {email}')
    return user


def get_last_migration_info():
    # Obtener el último registro de Migration
    last_migration = db.session.query(
        Migration).order_by(Migration.id.desc()).first()
    if last_migration is None:
        return None, None  # Página predeterminada None y último registro None si no hay información de migración
    return last_migration.page_id, last_migration.last_record


def update_migration_info(page_id, last_record):
    # Actualizar la información de migración
    migration = Migration(page_id, last_record)
    db.session.add(migration)
    db.session.commit()


if __name__ == "__main__":
    webbrowser.open_new('http://localhost:8088')
    app.run(host='localhost', port=8088)
