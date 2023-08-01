import webbrowser
from flask import Flask, request, redirect
from flask_sqlalchemy import SQLAlchemy
import requests
import time
from tqdm import tqdm
import colorlog
import logging
from datetime import datetime
from dotenv import load_dotenv
import os

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
    # nuevo campo para almacenar la fecha de creación
    created_at = db.Column(db.DateTime)
    user_id = db.Column(db.String, db.ForeignKey('user.id'))
    agent_id = db.Column(db.String, db.ForeignKey('user.id'))

    def __init__(self, id, created_at, user_id, agent_id):
        self.id = id
        self.created_at = created_at
        self.user_id = user_id
        self.agent_id = agent_id


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    chat_id = db.Column(db.String, db.ForeignKey('chat.id'), nullable=False)
    message = db.Column(db.String)

    def __init__(self, chat_id, message):
        self.chat_id = chat_id
        self.message = message


class Migration(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    last_page = db.Column(db.Integer)
    last_record = db.Column(db.String)

    def __init__(self, last_page, last_record):
        self.last_page = last_page
        self.last_record = last_record


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
    # Este es el punto final al que LiveChat redirigirá después de la autorización
    # Podemos obtener el código desde aquí y utilizarlo para obtener un token de acceso
    code = request.args.get('code')
    if code:
        token = get_access_token(code)
        if token:
            result = load_get_chats(token)
            return "Token de acceso obtenido: " + token + ". " + result
        else:
            return "No se pudo obtener el token de acceso"
    else:
        return "No se proporcionó código en la redirección"


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
        return response.json().get('access_token')
    else:
        return None


def load_get_chats(token):
    last_page, last_record = get_last_migration_info()
    chat_count = 0
    while True:
        last_page, last_record, response, added_chats = get_chats(
            token, last_page, last_record)
        chat_count += added_chats
        if last_page is None:
            if response is not None and response.status_code != 200:
                # Aquí el token ya pudo haber expirado
                break
            else:
                # Algo salió mal. Sal del bucle.
                break
        update_migration_info(last_page, last_record)
        logger.debug('Delay de 5 segundos...')
        time.sleep(5)  # Retraso de 5 segundos
    return "Se han guardado " + str(chat_count) + " chats en la base de datos."


def get_chats(token, page=None, last_record=None):
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }

    data = {
        'limit': 100,  # Máximo permitido por LiveChat
        'page': page or 1
    }

    try:
        response = requests.post(
            'https://api.livechatinc.com/v3.5/agent/action/list_archives', headers=headers, json=data)

        if response.status_code == 200:
            chats = response.json()['chats']
            chat_count = len(chats)

            # Actualiza el último registro migrado
            last_record = chats[-1]['id'] if chats else last_record

            for chat in chats:
                id = chat['id']
                created_at = datetime.strptime(
                    chat['thread']['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ")
                users = chat['users']
                user_ids = [user['id'] for user in users]
                user_names = [user['name'] for user in users]
                user_emails = [user['email'] for user in users]
                chat_text = [event['text'] for event in chat['thread']
                             ['events'] if event['type'] == 'message']

                if len(chat_text) <= 2:
                    logger.warn(
                        f'Omitiendo chat con (${len(chat_text)}) mensajes - Contenido: {" ".join(chat_text)}')
                    continue

                for idx, user_id in enumerate(user_ids):
                    get_or_create_user(
                        user_id, user_names[idx], user_emails[idx])

                existing_chat = db.session.get(Chat, id)

                if existing_chat is None:
                    new_chat = Chat(id, created_at, user_ids[0], user_ids[1] if len(
                        user_ids) > 1 else None)
                    db.session.add(new_chat)
                    db.session.commit()

                for message_text in chat_text:
                    new_message = Message(id, message_text)
                    db.session.add(new_message)

                user_names_str = ' & '.join(user_names)
                db.session.commit()
                logger.info(f'Guardado chat entre: {user_names_str}')

            next_page = page + 1 if chats else None
            return next_page, last_record, response, chat_count

        else:
            logger.error('Failed to retrieve chats. Status code: {}. Response: {}'.format(
                response.status_code, response.text))
            return None, last_record, response, 0

    except Exception as e:
        logger.error('Error occurred: {}'.format(e))
        return None, last_record, None, 0


def get_or_create_user(user_id, name, email):
    user = db.session.get(User, user_id)
    if user is None:
        user = User(user_id, name, email)
        db.session.add(user)
        db.session.commit()
        logger.info(f'Creado usuario no existente: ${name} - ${email}')
    return user


def get_last_migration_info():
    # Obtener el último registro de Migration
    last_migration = db.session.query(
        Migration).order_by(Migration.id.desc()).first()
    if last_migration is None:
        return 1, None  # Página predeterminada 1 y último registro None si no hay información de migración
    return last_migration.last_page, last_migration.last_record


def update_migration_info(last_page, last_record):
    # Actualizar la información de migración
    migration = Migration(last_page, last_record)
    db.session.add(migration)
    db.session.commit()


if __name__ == "__main__":
    webbrowser.open_new('http://localhost:8088')
    app.run(host='localhost', port=8088)
