"""
Script to remove duplicate transactions from the database
Keeps the oldest transaction (lowest ID) and removes duplicates
"""
from app import app, db, Transaction
from sqlalchemy import func

def remove_duplicates():
    """Remove duplicate transactions based on key fields"""
    
    with app.app_context():
        # Find duplicates based on: reference, transaction_date, customer_name, amount_paid
        # Group by these fields and keep only the first (oldest) transaction
        
        print("Finding duplicate transactions...")
        
        # Get all transactions
        all_transactions = Transaction.query.all()
        print(f"Total transactions before cleanup: {len(all_transactions)}")
        
        # Create a dictionary to track unique transactions
        unique_transactions = {}
        duplicates_to_delete = []
        
        for transaction in all_transactions:
            # Create a unique key based on transaction details
            key = (
                transaction.reference or '',
                transaction.transaction_date,
                transaction.customer_name,
                transaction.amount_paid,
                transaction.bank_name
            )
            
            if key in unique_transactions:
                # This is a duplicate, mark for deletion
                duplicates_to_delete.append(transaction.id)
                print(f"Duplicate found: {transaction.customer_name} - NGN {transaction.amount_paid} - {transaction.reference}")
            else:
                # This is the first occurrence, keep it
                unique_transactions[key] = transaction.id
        
        print(f"\nFound {len(duplicates_to_delete)} duplicate transactions")
        
        if duplicates_to_delete:
            # Delete duplicates
            deleted = Transaction.query.filter(Transaction.id.in_(duplicates_to_delete)).delete(synchronize_session=False)
            db.session.commit()
            print(f"Deleted {deleted} duplicate transactions")
            
            # Show final count
            final_count = Transaction.query.count()
            print(f"\nTotal transactions after cleanup: {final_count}")
        else:
            print("No duplicates found!")

if __name__ == "__main__":
    remove_duplicates()

