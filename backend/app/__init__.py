from flask import Flask
from flask_cors import CORS

from app.routes.auth import auth_bp
from app.routes.collections import COLLECTION_NAMES, make_collection_blueprint
from app.routes.health import health_bp


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    for name in COLLECTION_NAMES:
        app.register_blueprint(make_collection_blueprint(name))

    return app
