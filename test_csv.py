#!/usr/bin/env python3
"""
Test script to process the actual bank statement CSV file
"""

import pandas as pd
import sqlite3
from datetime import datetime
import os

def test_csv_processing():
    """Test processing the actual CSV file"""
    
    # Read the CSV file
    csv_path = "documents/Ciudad_Infrastructure_Limited_transactions_1760088547618.csv"
    
    print("Reading CSV file...")
    df = pd.read_csv(csv_path)
    
    print(f"CSV loaded successfully!")
    print(f"Shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")
    print(f"First few rows:")
    print(df.head(3))
    
    # Check data types and sample data
    print(f"\nData types:")
    print(df.dtypes)
    
    # Check for missing values
    print(f"\nMissing values:")
    print(df.isnull().sum())
    
    # Sample customer names
    print(f"\nSample customer names:")
    print(df['Customer (fullname)'].head(10).tolist())
    
    # Sample banks
    print(f"\nSample banks:")
    print(df['Card Bank'].value_counts().head(10))
    
    # Amount statistics
    print(f"\nAmount statistics:")
    print(f"Total amount: N{df['Amount Paid'].sum():,.2f}")
    print(f"Average amount: N{df['Amount Paid'].mean():,.2f}")
    print(f"Min amount: N{df['Amount Paid'].min():,.2f}")
    print(f"Max amount: N{df['Amount Paid'].max():,.2f}")
    
    # Date range
    df['Transaction Date'] = pd.to_datetime(df['Transaction Date'], format='mixed')
    print(f"\nDate range:")
    print(f"From: {df['Transaction Date'].min()}")
    print(f"To: {df['Transaction Date'].max()}")
    
    return df

if __name__ == "__main__":
    df = test_csv_processing()
