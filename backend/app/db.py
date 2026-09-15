import os

from pymongo import MongoClient

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('MONGO_DB_NAME', 'Capstone_Proj_Database')

_client = MongoClient(MONGO_URI)
db = _client[DB_NAME]
