import json
import os

import firebase_admin
import streamlit as st
from firebase_admin import credentials, firestore
from google.cloud import firestore as google_firestore

#: The key file next to this module, for running on your own machine.
CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firebaseServiceAccountKey.json")


def credential_source():
    """The service-account JSON from FIREBASE_CREDENTIALS_JSON (GitHub Actions, a container
    .env), else the key file. ``credentials.Certificate`` accepts either."""
    raw = os.environ.get("FIREBASE_CREDENTIALS_JSON")
    return json.loads(raw) if raw else CREDENTIALS_PATH


# 1. Initialize the App (Singleton Pattern)
def init_db():
    # Check if the app is already initialized to avoid errors on Streamlit reruns
    if not firebase_admin._apps:
        cred = credentials.Certificate(credential_source())
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


def update_transaction(doc_id: str, data: dict) -> None:
    """Replace one transaction document wholesale.

    The caller supplies the complete body, built by the same function the create
    path uses, so an edited legacy document comes out in the current schema
    rather than as a patch over old field names.
    """
    db = init_db()
    db.collection("transactions").document(doc_id).set(data)
    clear_transaction_cache()


def delete_transaction(doc_id: str) -> None:
    db = init_db()
    db.collection("transactions").document(doc_id).delete()
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


# 4. Cash accounts: bank balances entered by hand, one document per account
CASH_COLLECTION = "cash_accounts"


@st.cache_data(ttl=60, show_spinner=False)
def get_cash_accounts():
    db = init_db()
    return [doc.to_dict() for doc in db.collection(CASH_COLLECTION).stream()]


def save_cash_accounts(accounts: list[dict]) -> None:
    """Replace every stored account with ``accounts``, in one atomic batch.

    The editor submits the whole list, deletions included, so replacing the
    collection is the simplest way to make what is stored match what was saved.
    """
    db = init_db()
    collection = db.collection(CASH_COLLECTION)
    batch = db.batch()
    for doc in collection.stream():
        batch.delete(doc.reference)
    for account in accounts:
        batch.set(collection.document(), account)
    batch.commit()
    get_cash_accounts.clear()


# 5. Portfolio design: the target allocation, one document overwritten on each save
DESIGN_COLLECTION = "portfolio_design"
DESIGN_DOCUMENT = "current"


@st.cache_data(ttl=60, show_spinner=False)
def get_portfolio_design():
    """``{"targets": {category: percent}, "saved": "YYYY-MM-DD"}``, or None before the first save."""
    snapshot = init_db().collection(DESIGN_COLLECTION).document(DESIGN_DOCUMENT).get()
    return snapshot.to_dict() if snapshot.exists else None


def save_portfolio_design(design: dict) -> None:
    init_db().collection(DESIGN_COLLECTION).document(DESIGN_DOCUMENT).set(design)
    get_portfolio_design.clear()
