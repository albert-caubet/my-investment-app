import firebase_admin
from firebase_admin import credentials, firestore
import os
from google.cloud import firestore as google_firestore


# 1. Initialize the App (Singleton Pattern)
def init_db():
    # Check if the app is already initialized to avoid errors on Streamlit reruns
    if not firebase_admin._apps:
        cred = credentials.Certificate("firebaseServiceAccountKey.json")
        firebase_admin.initialize_app(cred)

    return firestore.client()


# Create a "shortcut" for the server timestamp
def get_timestamp():
    return google_firestore.SERVER_TIMESTAMP


# 2. Helper function to save a transaction
def record_transaction(data):
    db = init_db()
    # This creates a new document with an auto-generated ID in the 'transactions' collection
    db.collection("transactions").add(data)


# 3. Helper function to get all transactions
def get_all_transactions():
    db = init_db()
    docs = db.collection("transactions").stream()
    return [doc.to_dict() for doc in docs]