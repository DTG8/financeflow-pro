#!/usr/bin/env python3
"""
Load actual bank statement data into the database
"""

import pandas as pd
from app import app, db, Transaction, process_bank_statement
import os

def load_real_data():
    """Load the actual CSV data into the database"""
    
    with app.app_context():
        # Create database tables
        db.create_all()
        
        # Process the actual CSV file
        csv_path = "documents/Ciudad_Infrastructure_Limited_transactions_1760088547618.csv"
        
        if os.path.exists(csv_path):
            print(f"Processing {csv_path}...")
            processed_count = process_bank_statement(csv_path, "Ciudad_Infrastructure_Limited_transactions.csv")
            print(f"Successfully processed {processed_count} transactions!")
            
            # Show some statistics
            total_transactions = Transaction.query.count()
            total_customers = db.session.query(Transaction.customer_name).distinct().count()
            total_amount = db.session.query(db.func.sum(Transaction.amount_paid)).scalar() or 0
            
            print(f"\nDatabase Statistics:")
            print(f"Total transactions: {total_transactions}")
            print(f"Unique customers: {total_customers}")
            print(f"Total amount: N{total_amount:,.2f}")
            
            # Show sample customers
            print(f"\nSample customers:")
            customers = db.session.query(Transaction.customer_name).distinct().limit(10).all()
            for customer in customers:
                print(f"- {customer[0]}")
                
        else:
            print(f"CSV file not found: {csv_path}")

if __name__ == "__main__":
    load_real_data()
