
from flask_login import UserMixin


class User(UserMixin):
    def __init__(self, user_id, name, email, password_hash):
        self.id = user_id
        self.name = name
        self.email = email
        self.password_hash = password_hash
