import os

import firebase_admin
import streamlit as st
from firebase_admin import credentials, firestore
from google.cloud import firestore as google_firestore

#: Overridable so the app is not tied to being launched from the repo root.
CREDENTIALS_PATH = os.environ.get(
    "FIREBASE_CREDENTIALS",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "firebaseServiceAccountKey.json"),
)


# 1. Initialize the App (Singleton Pattern)
def init_db():
    # Check if the app is already initialized to avoid errors on Streamlit reruns
    if not firebase_admin._apps:
        cred = credentials.Certificate(CREDENTIALS_PATH)
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
    # Otherwise a freshly saved trade stays invisible until the cache expires.
    clear_transaction_cache()


# 3. Helper function to get all transactions
@st.cache_data(ttl=60, show_spinner=False)
def get_all_transactions():
    """Every transaction document, each carrying its Firestore id as ``_doc_id``.

    Cached because this used to re-stream the whole collection on every widget
    interaction. Ordering is left to the caller: the chronological replay needs
    ``(date, timestamp)`` anyway, which Firestore cannot express without an index.
    """
    db = init_db()
    docs = db.collection("transactions").stream()
    return [{**doc.to_dict(), "_doc_id": doc.id} for doc in docs]


def clear_transaction_cache():
    get_all_transactions.clear()
