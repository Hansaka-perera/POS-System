# db.py
import mysql.connector

DB_CONFIG = dict(
    host="localhost",
    port=3308,
    user="root",
    password="",
    database="pos_system",
)

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def dict_cursor(conn):
    return conn.cursor(dictionary=True)