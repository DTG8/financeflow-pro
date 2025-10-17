#!/usr/bin/env python3
"""
Startup script for FinanceFlow Pro
Ensures database is properly initialized before starting the app
"""

import os
from app import app, db

def initialize_database():
    """Initialize database tables"""
    with app.app_context():
        try:
            # Create all tables
            db.create_all()
            print("✅ Database tables created successfully")
            
            # Check if tables exist
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            print(f"📊 Database tables: {tables}")
            
        except Exception as e:
            print(f"❌ Database initialization error: {e}")
            raise

if __name__ == '__main__':
    print("🚀 Starting FinanceFlow Pro...")
    
    # Initialize database
    initialize_database()
    
    # Start the app
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Starting server on port {port}")
    
    app.run(host='0.0.0.0', port=port, debug=False)
