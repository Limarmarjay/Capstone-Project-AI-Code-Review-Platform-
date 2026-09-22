from flask import Flask
from flask_cors import CORS

from app.routes.auth import auth_bp
from app.routes.collections import COLLECTION_NAMES, make_collection_blueprint
from app.routes.github import github_bp
from app.routes.health import health_bp
from app.routes.submission import submission_bp

# 'submission' has a dedicated blueprint (runs repo_diff_pipeline ingestion/
# validation/parsing) instead of the generic store-whatever-you-post one.
GENERIC_COLLECTION_NAMES = [name for name in COLLECTION_NAMES if name != 'submission']


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(github_bp)
    app.register_blueprint(submission_bp)
    for name in GENERIC_COLLECTION_NAMES:
        app.register_blueprint(make_collection_blueprint(name))

    return app
