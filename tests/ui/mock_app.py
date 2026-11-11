"""The Flask app shared by mock_server.py and the per-page route modules."""
from flask import Flask

from mock_data import STATIC

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
