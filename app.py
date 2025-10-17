from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import pandas as pd
import os
from datetime import datetime, timedelta, timezone
import re
import io
import uuid
import json
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from forecasting_service import RevenueForecaster
from customer_analytics_service import CustomerAnalytics
from advanced_analytics_service import AdvancedAnalytics

app = Flask(__name__)
app.config['SECRET_KEY'] = 'financeflow-secret-key-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///transactions.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls', 'pdf'}

# Initialize database
db = SQLAlchemy(app)

# Create upload folder
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ==================== DATABASE MODELS ====================

class Transaction(db.Model):
    """Transaction model - simplified and clean"""
    id = db.Column(db.Integer, primary_key=True)
    
    # Transaction info
    date = db.Column(db.DateTime, nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text)
    reference = db.Column(db.String(200))
    
    # Customer info
    customer_name = db.Column(db.String(200), index=True)
    customer_email = db.Column(db.String(200))
    
    # Payment details
    bank = db.Column(db.String(100), index=True)  # Payment processor (Paystack, Providus)
    customer_bank = db.Column(db.String(100))  # Customer's bank
    channel = db.Column(db.String(50), index=True)  # Payment channel (card, bank_transfer, etc.)
    card_type = db.Column(db.String(50))  # Card type (mastercard debit, visa, etc.)
    status = db.Column(db.String(50))  # Transaction status (success, failed, etc.)
    gateway_response = db.Column(db.String(200))  # Gateway response message
    
    # Source info
    file_source = db.Column(db.String(200))
    
    # Metadata
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.isoformat(),
            'amount': self.amount,
            'description': self.description,
            'reference': self.reference,
            'customer_name': self.customer_name,
            'customer_email': self.customer_email,
            'bank': self.bank,
            'customer_bank': self.customer_bank,
            'channel': self.channel,
            'card_type': self.card_type,
            'status': self.status,
            'gateway_response': self.gateway_response,
            'file_source': self.file_source,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# ==================== FILE PROCESSOR ====================

class FileProcessor:
    """Simple, reliable file processor"""
    
    def process(self, filepath, filename):
        """Main processing method"""
        ext = filename.lower().split('.')[-1]
        
        # Load file
        if ext == 'csv':
            df = pd.read_csv(filepath, encoding='utf-8', low_memory=False)
        elif ext in ['xlsx', 'xls']:
            df = pd.read_excel(filepath)
            # Providus exports (or PDF->XLSX) often have banner/header rows like
            # 'PROVIDUS BANK' and many 'Unnamed' columns - re-read with header=None
            headers_text = ' '.join([str(c) for c in list(df.columns)])
            num_unnamed = sum(1 for c in df.columns if str(c).lower().startswith('unnamed'))
            if 'providus' in filename.lower() or 'providus' in headers_text.lower() or (len(df.columns) > 0 and num_unnamed / max(1, len(df.columns)) > 0.5):
                try:
                    df = pd.read_excel(filepath, header=None)
                except Exception:
                    pass
        elif ext == 'pdf':
            # Try extracting tables from PDF into a DataFrame (one big concat)
            import pdfplumber
            tables = []
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    page_tables = page.extract_tables()
                    for tbl in page_tables:
                        # First row as header if it looks like strings
                        if not tbl or len(tbl) < 2:
                            continue
                        headers = [str(h).strip() if h is not None else '' for h in tbl[0]]
                        rows = [[None if c == '' else c for c in r] for r in tbl[1:]]
                        try:
                            tables.append(pd.DataFrame(rows, columns=headers))
                        except Exception:
                            # fallback: unnamed columns
                            tables.append(pd.DataFrame(rows))
            if not tables:
                raise ValueError("No tables found in PDF")
            # Combine all tables; allow different columns by outer join/concat ignore index
            df = pd.concat(tables, ignore_index=True, sort=False)
        else:
            raise ValueError(f"Unsupported format: {ext}")
        
        # Clean dataframe and normalize duplicate/blank headers
        df = df.dropna(how='all')
        df = self._normalize_columns(df)
        # Try to reheader if it's a Providus dump where the first real header row is inside the sheet
        if 'providus' in filename.lower() and not any('date' in str(c).lower() for c in df.columns):
            df = self._try_providus_reheader(df)
            df = self._normalize_columns(df)
        # Ensure unique column names to avoid pandas reindexing errors
        try:
            df = df.loc[:, ~pd.Index(df.columns).duplicated()]
        except Exception:
            pass
        
        print(f"\n{'='*60}")
        print(f"File: {filename}")
        print(f"Rows: {len(df)}")
        print(f"Columns: {df.columns.tolist()}")
        
        # Detect bank type
        bank = self._detect_bank(filename, df.columns.tolist())
        print(f"Bank: {bank}")
        
        # Process based on bank
        if 'paystack' in bank.lower():
            transactions = self._process_paystack(df, filename)
        elif 'providus' in bank.lower():
            transactions = self._process_providus(df, filename)
        elif 'fcmb' in bank.lower():
            transactions = self._process_fcmb(df, filename)
        else:
            transactions = self._process_generic(df, filename, bank)
        
        print(f"Extracted: {len(transactions)} transactions")
        print(f"{'='*60}\n")
        
        return transactions
    
    def _detect_bank(self, filename, columns):
        """Detect bank from filename and column structure"""
        fn = filename.lower()
        cols_lower = [str(c).lower() for c in columns]
        
        # Check filename for payment processors first
        if 'paystack' in fn:
            return 'Paystack'
        elif 'providus' in fn:
            return 'Providus Bank'
        elif 'flutterwave' in fn:
            return 'Flutterwave'
        
        # Check filename for Nigerian banks
        bank_names = {
            'gtbank': 'GTBank',
            'gtb': 'GTBank',
            'guaranty trust': 'GTBank',
            'access': 'Access Bank',
            'zenith': 'Zenith Bank',
            'first bank': 'First Bank',
            'firstbank': 'First Bank',
            'uba': 'UBA',
            'united bank': 'UBA',
            'fidelity': 'Fidelity Bank',
            'union': 'Union Bank',
            'sterling': 'Sterling Bank',
            'stanbic': 'Stanbic IBTC',
            'standard chartered': 'Standard Chartered',
            'wema': 'WEMA Bank',
            'unity': 'Unity Bank',
            'keystone': 'Keystone Bank',
            'fcmb': 'FCMB',
            'ecobank': 'Ecobank',
            'polaris': 'Polaris Bank',
            'kuda': 'Kuda Bank',
            'opay': 'OPay',
            'palmpay': 'PalmPay',
            'carbon': 'Carbon',
            'rubies': 'Rubies Bank'
        }
        
        for key, name in bank_names.items():
            if key in fn:
                return name
        
        # Check column structure for Paystack indicators
        paystack_indicators = ['paystack fees', 'gateway response', 'card type', 'transaction id']
        if any(indicator in ' '.join(cols_lower) for indicator in paystack_indicators):
            return 'Paystack'
        
        # Check for Providus indicators
        if 'post date' in ' '.join(cols_lower) or 'narration' in ' '.join(cols_lower):
            return 'Providus Bank'
        
        # Check for FCMB indicators (Deposit/Withdrawal columns)
        if 'deposit' in cols_lower and 'withdrawal' in cols_lower and 'transaction details' in cols_lower:
            return 'FCMB'
        
        return 'Unknown Bank'
    
    def _process_paystack(self, df, filename):
        """Process Paystack CSV"""
        transactions = []
        
        # Create case-insensitive column mapping
        col_map = {}
        for col in df.columns:
            col_lower = col.lower()
            if 'fullname' in col_lower or 'full name' in col_lower:
                col_map[col] = 'customer_name'
            elif 'email' in col_lower and 'customer' in col_lower:
                col_map[col] = 'customer_email'
            elif 'transaction date' in col_lower:
                col_map[col] = 'date'
            elif 'amount paid' in col_lower or ('amount' in col_lower and 'paid' in col_lower):
                col_map[col] = 'amount'
            elif col_lower == 'status':
                col_map[col] = 'status'
            elif col_lower == 'reference':
                col_map[col] = 'reference'
            elif col_lower == 'channel':
                col_map[col] = 'channel'
            elif 'card bank' in col_lower:
                col_map[col] = 'customer_bank'
            elif 'card type' in col_lower:
                col_map[col] = 'card_type'
            elif 'gateway response' in col_lower:
                col_map[col] = 'gateway_response'
            elif 'description' in col_lower or 'narration' in col_lower:
                col_map[col] = 'description'
        
        # Rename columns
        df.rename(columns=col_map, inplace=True)
        
        # Filter successful transactions only
        if 'status' in df.columns:
            df = df[df['status'].str.lower() == 'success']
            print(f"Filtered to {len(df)} successful transactions")
        
        for _, row in df.iterrows():
            try:
                # Parse date
                date = pd.to_datetime(row['date'])
                if pd.isna(date):
                    continue
                
                # Parse amount
                amount = self._clean_amount(row['amount'])
                if amount <= 0:
                    continue
                
                # Get customer name
                customer_name = str(row.get('customer_name', 'Unknown')).strip()
                if not customer_name or customer_name.lower() == 'nan':
                    customer_name = 'Unknown Customer'
                
                # Get payment details
                channel = str(row.get('channel', '')).strip()
                if channel.lower() == 'nan':
                    channel = ''
                
                customer_bank = str(row.get('customer_bank', '')).strip()
                if customer_bank.lower() == 'nan':
                    customer_bank = ''
                    
                card_type = str(row.get('card_type', '')).strip()
                if card_type.lower() == 'nan':
                    card_type = ''
                
                status = str(row.get('status', 'success')).strip()
                if status.lower() == 'nan':
                    status = 'success'
                
                gateway_response = str(row.get('gateway_response', '')).strip()
                if gateway_response.lower() == 'nan':
                    gateway_response = ''
                
                description = str(row.get('description', '')).strip()
                if description.lower() == 'nan':
                    description = ''
                
                transactions.append({
                    'date': date,
                    'amount': amount,
                    'customer_name': customer_name,
                    'customer_email': str(row.get('customer_email', '')),
                    'reference': str(row.get('reference', '')),
                    'description': description,
                    'bank': 'Paystack',
                    'customer_bank': customer_bank,
                    'channel': channel,
                    'card_type': card_type,
                    'status': status,
                    'gateway_response': gateway_response,
                    'file_source': filename
                })
            except Exception as e:
                continue
        
        return transactions
    
    def _process_providus(self, df, filename):
        """Process Providus Bank statement"""
        transactions = []
        
        # Find columns (Providus PDFs/CSVs often use Value Date + Credit/Debit)
        date_col = self._find_column(df, ['post date', 'value date', 'date', 'transaction date'])
        credit_col = self._find_column(df, ['credit amount', 'credit', 'cr'])
        debit_col = self._find_column(df, ['debit amount', 'debit', 'dr'])
        amount_col = self._find_column(df, ['amount', 'transaction amount'])
        desc_col = self._find_column(df, ['transaction details', 'narration', 'description', 'details'])
        
        if not date_col or not (amount_col or credit_col or debit_col):
            # Try to recover by re-headering from the row that contains the labels
            df = self._try_providus_reheader(df)
            date_col = self._find_column(df, ['post date', 'value date', 'date', 'transaction date'])
            credit_col = self._find_column(df, ['credit amount', 'credit', 'cr'])
            debit_col = self._find_column(df, ['debit amount', 'debit', 'dr'])
            amount_col = self._find_column(df, ['amount', 'transaction amount'])
            desc_col = self._find_column(df, ['transaction details', 'narration', 'description', 'details'])
            if not date_col or not (amount_col or credit_col or debit_col):
                print("ERROR: Could not find required columns")
                return []
        
        print(f"Mapped: date={date_col}, amount={amount_col}, credit={credit_col}, debit={debit_col}, desc={desc_col}")
        
        # Skip last row if it's a footer/total row (often contains "Total" or "DISCLAIMER")
        df_data = df[:-1] if len(df) > 1 else df
        
        seen_keys = set()
        for _, row in df_data.iterrows():
            try:
                date = pd.to_datetime(row[date_col], errors='coerce', dayfirst=True)
                description_text = str(row.get(desc_col, '')) if desc_col else ''
                # Skip only balance brought forward rows
                if description_text and 'balance' in description_text.lower() and 'b/f' in description_text.lower():
                    continue
                # Only process credit transactions (inflows)
                if not credit_col or pd.isna(row.get(credit_col, None)):
                    continue
                
                amount = self._clean_amount(row.get(credit_col, 0))
                
                # Keep only positive amounts with valid dates
                if pd.isna(date) or pd.isna(amount) or amount <= 0:
                    continue
                
                # Deduplicate within this file by stable key
                key = (date.strftime('%Y-%m-%d'), round(float(amount), 2), description_text.strip().lower())
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                
                # Extract customer name/email from description
                extracted_name, extracted_email = self._extract_customer_from_details(description_text)
                
                # Extract customer bank from description (e.g., "From GTBank/..." or "From ACCESS/...")
                customer_bank = self._extract_customer_bank(description_text)
                
                # Infer channel - Providus transactions are typically bank transfers
                channel = 'bank_transfer' if 'transfer' in description_text.lower() or 'from' in description_text.lower() else ''
                
                transactions.append({
                    'date': date,
                    'amount': amount,
                    'customer_name': extracted_name or 'Unknown Customer',
                    'customer_email': extracted_email or '',
                    'reference': '',
                    'description': description_text,
                    'bank': 'Providus Bank',
                    'customer_bank': customer_bank,
                    'channel': channel,
                    'card_type': '',
                    'status': 'success',
                    'gateway_response': description_text,  # Use transaction details as gateway response
                    'file_source': filename
                })
            except:
                continue
        
        return transactions
    
    def _process_fcmb(self, df, filename):
        """Process FCMB bank statement
        
        FCMB format:
        - Columns: SN, Tran Date, Value Date, Reference, Transaction Details, Withdrawal, Deposit, Balance
        - Deposit column contains credit amounts
        - Transaction Details format: "NIP FRM <NAME>-<description>" or "TRF From <NAME>/<description>"
        """
        transactions = []
        
        # Find column mappings (case-insensitive)
        col_map = {}
        for col in df.columns:
            col_lower = str(col).lower().strip()
            if 'tran date' in col_lower or 'transaction date' in col_lower:
                col_map['date'] = col
            elif col_lower == 'deposit':
                col_map['deposit'] = col
            elif 'transaction details' in col_lower or 'narration' in col_lower:
                col_map['details'] = col
            elif col_lower == 'reference':
                col_map['reference'] = col
        
        if not col_map.get('date') or not col_map.get('deposit'):
            print("Warning: Could not find required FCMB columns (Tran Date, Deposit)")
            return transactions
        
        date_col = col_map['date']
        deposit_col = col_map['deposit']
        details_col = col_map.get('details', '')
        reference_col = col_map.get('reference', '')
        
        # Track seen transactions to avoid duplicates
        seen = set()
        
        for idx, row in df.iterrows():
            try:
                # Only process deposits (credits/inflows)
                if pd.isna(row.get(deposit_col, None)):
                    continue
                
                amount = self._clean_amount(row.get(deposit_col, 0))
                if amount <= 0:
                    continue
                
                # Parse date
                date_val = row.get(date_col)
                if pd.isna(date_val):
                    continue
                
                # FCMB date format: "02 Jan 2025"
                try:
                    date = pd.to_datetime(date_val, format='%d %b %Y', errors='coerce')
                    if pd.isna(date):
                        date = pd.to_datetime(date_val, dayfirst=True, errors='coerce')
                except:
                    date = pd.to_datetime(date_val, dayfirst=True, errors='coerce')
                
                if pd.isna(date):
                    continue
                
                # Get transaction details
                details_text = str(row.get(details_col, '')) if details_col else ''
                
                # Skip non-customer transactions (opening/closing balance, charges, etc.)
                skip_keywords = ['opening balance', 'closing balance', 'balance b/f', 'balance c/f', 
                                'emt levy', 'sms charge', 'cot charge', 'stamp duty']
                if any(keyword in details_text.lower() for keyword in skip_keywords):
                    continue
                
                # Extract customer name and email from transaction details
                customer_name, customer_email = self._extract_customer_from_fcmb_details(details_text)
                
                # Get reference
                reference = str(row.get(reference_col, '')) if reference_col else ''
                if reference.lower() == 'nan' or not reference:
                    reference = ''
                
                # Extract customer bank and channel
                customer_bank = self._extract_customer_bank(details_text)
                
                # Infer channel from details
                channel = ''
                details_lower = details_text.lower()
                if 'nip' in details_lower:
                    channel = 'bank_transfer'
                elif 'trf' in details_lower or 'transfer' in details_lower:
                    channel = 'bank_transfer'
                elif 'paystack' in details_lower:
                    channel = 'card'  # Paystack payments through FCMB
                
                # Deduplication key - use reference + description to handle legitimate duplicates
                # (same customer, same amount, same day, but different transaction reference)
                dedup_key = (date.date(), round(amount, 2), reference, details_text)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                
                transactions.append({
                    'date': date,
                    'amount': amount,
                    'customer_name': customer_name,
                    'customer_email': customer_email,
                    'reference': reference,
                    'description': details_text[:200],  # Truncate long descriptions
                    'bank': 'FCMB',
                    'customer_bank': customer_bank,
                    'channel': channel,
                    'card_type': '',
                    'status': 'success',
                    'gateway_response': details_text[:200],  # Use transaction details as gateway response
                    'file_source': filename
                })
            except Exception as e:
                print(f"Error processing FCMB row {idx}: {e}")
                continue
        
        return transactions
    
    def _extract_customer_from_fcmb_details(self, text: str):
        """Extract customer name and email from FCMB transaction details
        
        FCMB formats:
        - "NIP FRM <NAME>-<description>"
        - "TRF From <NAME>/<description>"
        - "web: TRF Frm <NAME>/<description>"
        - "CSH DEPOSIT BY:<NAME>|<branch>"
        - "ZENITH/Chq<number>/<NAME>"
        - "Rsvl:web:TB1c/<description>/<NAME>"
        - "QTMOB/TSF To <account> @ <code>"
        - "FGSA<NAME> for <month>|<description>"
        """
        if not text:
            return ('', '')
        
        original = str(text).strip()
        
        # Extract email if present
        email_match = re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', original)
        email = email_match.group(0) if email_match else ''
        
        name = ''
        
        # Pattern 1: "NIP FRM <NAME>-<description>"
        nip_match = re.search(r'NIP\s+FRM\s+([^-/]+)', original, flags=re.IGNORECASE)
        if nip_match:
            candidate = nip_match.group(1).strip()
            # For Paystack, mark as "Paystack Payment" since we can't identify the end customer
            if 'paystack' in candidate.lower():
                name = 'Paystack Payment'
            elif not self._is_bank_name(candidate):
                name = candidate
        
        # Pattern 2: "TRF From <NAME>/<description>" or "TRF Frm <NAME>/<description>"
        if not name:
            # Check if it's "TRF From App:subscription... /<ACTUAL_NAME>"
            trf_app_match = re.search(r'TRF\s+From\s+App:[^/]+/(.+)', original, flags=re.IGNORECASE)
            if trf_app_match:
                name = trf_app_match.group(1).strip()
            else:
                trf_match = re.search(r'TRF\s+Fro?m\s+([^/]+)', original, flags=re.IGNORECASE)
                if trf_match:
                    candidate = trf_match.group(1).strip()
                    # Skip "App:subscription" type entries
                    if not self._is_bank_name(candidate) and 'app:' not in candidate.lower():
                        name = candidate
        
        # Pattern 3: "CSH DEPOSIT BY:<NAME>|<branch>"
        if not name:
            csh_match = re.search(r'CSH\s+DEPOSIT\s+BY:([^|]+)', original, flags=re.IGNORECASE)
            if csh_match:
                name = csh_match.group(1).strip()
        
        # Pattern 4: "ZENITH/Chq<number>/<NAME>" or "<BANK>/Chq<number>/<NAME>"
        if not name:
            chq_match = re.search(r'/Chq\d+/(.+)', original, flags=re.IGNORECASE)
            if chq_match:
                name = chq_match.group(1).strip()
        
        # Pattern 5: "Rsvl:web:TB1c/<description>/<NAME>" or "TRANSACTION CHARGE-Rsvl:..."
        if not name:
            # All reversals (including transaction charges) go under one customer name
            if 'TRANSACTION CHARGE' in original and ('Rsvl:' in original or 'Rvsl:' in original):
                name = 'Reversal Payments'
            elif 'Rsvl:' in original or 'Rvsl:' in original:
                # All reversals consolidated under one name
                name = 'Reversal Payments'
        
        # Pattern 6: "FGSA<NAME> for <month>|<description>"
        if not name:
            fgsa_match = re.search(r'FGSA(.+?)\s+for\s+', original, flags=re.IGNORECASE)
            if fgsa_match:
                name = fgsa_match.group(1).strip()
        
        # Pattern 7: "QTMOB/TSF To <account>" - Use account number as identifier
        if not name:
            qtmob_match = re.search(r'QTMOB.*?TSF\s+To\s+(\d+)', original, flags=re.IGNORECASE)
            if qtmob_match:
                name = f'QuickTeller Transfer ({qtmob_match.group(1)})'
        
        # Pattern 8: "TRANSFER B/O: <NAME>"
        if not name:
            bo_match = re.search(r'TRANSFER\s+B/O:\s*(.+)', original, flags=re.IGNORECASE)
            if bo_match:
                name = bo_match.group(1).strip()
        
        # Pattern 9: "Transfer from <NAME>;<phone>;<description>"
        if not name:
            tfrom_match = re.search(r'Transfer\s+from\s+([^;]+)', original, flags=re.IGNORECASE)
            if tfrom_match:
                name = tfrom_match.group(1).strip()
        
        # Pattern 10: "<NAME>|MOB: To FCMB|<description>"
        if not name:
            mob_match = re.search(r'^(.+?)\|MOB:', original, flags=re.IGNORECASE)
            if mob_match:
                name = mob_match.group(1).strip()
        
        # Pattern 11: "CDB <NAME> / <account>"
        if not name:
            cdb_match = re.search(r'CDB\s+(.+?)\s*/\s*\d+', original, flags=re.IGNORECASE)
            if cdb_match:
                name = cdb_match.group(1).strip()
        
        # Pattern 12: "Txn Chrg Rvsl:" - Transaction charge reversal (consolidate with other reversals)
        if not name:
            if re.search(r'Txn\s+Chrg\s+Rvsl:', original, flags=re.IGNORECASE):
                name = 'Reversal Payments'
        
        # Pattern 13: "Mbanking Trf: <BANK>/<ref>;;NAME" or "Mbanking Trf: <BANK>/<ref>;;NXG :TRF<desc>FRM <NAME>"
        if not name:
            mbank_match = re.search(r'Mbanking\s+Trf:.*?;;(.+)', original, flags=re.IGNORECASE)
            if mbank_match:
                candidate = mbank_match.group(1).strip()
                # Check if it's a NXG transfer
                if candidate.upper().startswith('NXG'):
                    # Pattern: "NXG :TRF<description>FRM <NAME>" where description might be attached to FRM
                    nxg_match = re.search(r'NXG\s*:TRF(.+?)FRM\s+(.+)', candidate, flags=re.IGNORECASE)
                    if nxg_match:
                        desc_part = nxg_match.group(1).strip()
                        name_part = nxg_match.group(2).strip()
                        # If name_part is very short (1-2 chars), it might be part of description
                        # Try to extract the actual name from the description
                        if len(name_part) <= 2:
                            # Look for a proper name in the description part
                            # E.g., "AHMEDFRM A" should extract "AHMED"
                            desc_name_match = re.search(r'([A-Z][A-Za-z]+)FRM', desc_part + 'FRM', flags=re.IGNORECASE)
                            if desc_name_match:
                                name = desc_name_match.group(1).strip()
                            else:
                                name = 'NextGen Transfer'
                        else:
                            name = name_part
                    else:
                        # NXG without FRM - it's just a description like "NXG :TRFSUBSCRIPTI" or "NXG :TRFINTERNET R"
                        # These are descriptions, not customer names
                        name = 'NextGen Transfer'
                # Check if it's "AT124_TRF|<ref>" (system reference, extract unique ID)
                elif re.match(r'AT\d+_TRF\|', candidate):
                    # Extract the reference code to create a unique identifier
                    ref_match = re.search(r'AT\d+_TRF\|([A-Za-z0-9]+)', candidate)
                    if ref_match:
                        ref_code = ref_match.group(1)
                        name = f'Mobile Transfer {ref_code}'
                    else:
                        name = 'Mobile Banking Transfer'
                # Check if it's just a single letter or very short
                elif len(candidate) <= 2:
                    name = 'Mobile Banking Transfer'
                # Check if it's a description (contains keywords like subscription, payment, internet, etc.)
                elif any(kw in candidate.lower() for kw in ['subscrip', 'internet', 'payment', 'renewal', 'installation', 'wifi']):
                    # Use the description itself as the customer name (it's better than nothing)
                    # Capitalize it properly
                    name = candidate.title() if len(candidate) <= 30 else f'Mobile: {candidate[:25]}'
                else:
                    # It's a direct name
                    name = candidate
        
        # Pattern 14: "NXG :TRF<description>FRM <NAME>" or "NXG :TRF<description>" (standalone, not in Mbanking)
        if not name:
            # Try to match NXG with FRM pattern
            nxg_match = re.search(r'NXG\s*:TRF(.+?)FRM\s+(.+)', original, flags=re.IGNORECASE)
            if nxg_match:
                desc_part = nxg_match.group(1).strip()
                name_part = nxg_match.group(2).strip()
                # If name is single letter, try to extract from description
                if len(name_part) <= 2:
                    # Look for capitalized words in description that might be the name
                    desc_words = [w for w in desc_part.split() if w and len(w) > 2 and w[0].isupper()]
                    if desc_words:
                        name = ' '.join(desc_words[:3])  # Take first 3 words
                    else:
                        name = 'NextGen Transfer'
                else:
                    name = name_part
            elif re.search(r'NXG\s*:TRF', original, flags=re.IGNORECASE):
                # NXG transfer with no FRM - it's just a description, not a customer name
                name = 'NextGen Transfer'
        
        # Pattern 15: "FGSATRANSFER TO <NAME>" (FGSA without space)
        if not name:
            fgsa_no_space = re.search(r'FGSATRANSFER\s+TO\s+(.+)', original, flags=re.IGNORECASE)
            if fgsa_no_space:
                name = fgsa_no_space.group(1).strip()
        
        # Pattern 16: "COP FRM <NAME>" - Cash On Pickup
        if not name:
            cop_match = re.search(r'COP\s+FRM\s+(.+)', original, flags=re.IGNORECASE)
            if cop_match:
                name = cop_match.group(1).strip()
        
        # Pattern 17: "<DATE> NIP_FROM <NAME>" - Date prefix with underscore
        if not name:
            nip_from_match = re.search(r'\d+[A-Za-z]+\d+\s+NIP_FROM\s+(.+)', original, flags=re.IGNORECASE)
            if nip_from_match:
                name = nip_from_match.group(1).strip()
        
        # Pattern 18: "FGSA<month>|<NAME>" - FGSA with month but no "for"
        if not name:
            fgsa_month = re.search(r'FGSA[A-Za-z]+\|(.+)', original, flags=re.IGNORECASE)
            if fgsa_month:
                name = fgsa_month.group(1).strip()
        
        # Pattern 19: Handle "<NAME> TO" patterns that weren't caught earlier
        if not name:
            # Try to extract name before " TO" or " to"
            to_pattern_match = re.search(r'^([A-Z][A-Za-z\s]{2,}?)\s+[Tt][Oo]\s*$', original)
            if to_pattern_match:
                candidate = to_pattern_match.group(1).strip()
                # Make sure it's not a system keyword
                if not any(keyword in candidate.lower() for keyword in ['transfer', 'deposit', 'reversal', 'payment']):
                    name = candidate
        
        # Pattern 20: Generic single-word descriptions (WiFi, subscription, etc.) - Last resort
        if not name:
            # Check if it's an AT124_TRF reference (system code) - extract unique ID
            at_match = re.match(r'AT\d+_TRF\|([A-Za-z0-9]+)', original)
            if at_match:
                ref_code = at_match.group(1)
                name = f'Mobile Transfer {ref_code}'
            # If it's a very short description with no clear sender, use a descriptive name
            elif len(original) < 30 and not any(kw in original.lower() for kw in ['nip', 'trf', 'transfer', 'deposit', 'from']):
                # Try to extract any capitalized words as potential name
                words = [w for w in original.split() if w and w[0].isupper() and len(w) > 2]
                if words and len(' '.join(words)) > 3:
                    name = ' '.join(words)[:50]
                else:
                    name = f'Generic Payment ({original[:20]})'
        
        # Final fallback: if still no name, use generic
        if not name or name.strip() == '':
            name = 'Unknown Customer'
        
        # Clean up name
        if name:
            # Remove extra whitespace
            name = re.sub(r'\s{2,}', ' ', name)
            # Normalize casing if all uppercase
            if name.isupper() and len(name) > 3:
                name = name.title()
            # Truncate if too long
            if len(name) > 80:
                name = name[:80] + '...'
        
        return (name, email)
    
    def _process_generic(self, df, filename, bank):
        """Process generic bank statement"""
        transactions = []
        
        # Find columns
        date_col = self._find_column(df, ['date'])
        amount_col = self._find_column(df, ['amount', 'credit', 'debit'])
        
        if not date_col or not amount_col:
            print("ERROR: Could not identify required columns")
            return []
        
        print(f"Using: date={date_col}, amount={amount_col}")
        
        for _, row in df.iterrows():
            try:
                date = pd.to_datetime(row[date_col])
                amount = self._clean_amount(row[amount_col])
                
                if pd.isna(date) or amount <= 0:
                    continue
                
                transactions.append({
                    'date': date,
                    'amount': amount,
                    'customer_name': 'Unknown Customer',
                    'customer_email': '',
                    'reference': '',
                    'description': '',
                    'bank': bank,
                    'customer_bank': '',
                    'channel': '',
                    'card_type': '',
                    'status': 'success',
                    'gateway_response': '',
                    'file_source': filename
                })
            except:
                continue
        
        return transactions
    
    def _find_column(self, df, keywords):
        """Find column matching any keyword"""
        for col in df.columns:
            col_lower = str(col).lower().strip()
            if any(kw in col_lower for kw in keywords):
                return col
        return None
    
    def _clean_amount(self, value):
        """Clean and convert amount to float, preserving sign if present."""
        try:
            cleaned = str(value)
            # Standardize minus and remove currency symbols/commas/spaces
            cleaned = cleaned.replace('₦', '').replace('NGN', '').replace('$', '').replace(',', '').strip()
            # Handle parentheses for negatives e.g. (1,234.50)
            if cleaned.startswith('(') and cleaned.endswith(')'):
                cleaned = '-' + cleaned[1:-1]
            return float(cleaned)
        except:
            return 0.0

    def _get_bank_names(self):
        """Get list of Nigerian bank names and aliases for detection"""
        return {
            'gtbank': 'GTBank',
            'gt bank': 'GTBank',
            'guaranty trust': 'GTBank',
            'access': 'Access Bank',
            'access bank': 'Access Bank',
            'zenith': 'Zenith Bank',
            'zenith bank': 'Zenith Bank',
            'first bank': 'First Bank',
            'firstbank': 'First Bank',
            'fbn': 'First Bank',
            'uba': 'UBA',
            'united bank': 'UBA',
            'fidelity': 'Fidelity Bank',
            'fidelity bank': 'Fidelity Bank',
            'union': 'Union Bank',
            'union bank': 'Union Bank',
            'stanbic': 'Stanbic IBTC',
            'stanbic ibtc': 'Stanbic IBTC',
            'sterling': 'Sterling Bank',
            'sterling bank': 'Sterling Bank',
            'wema': 'Wema Bank',
            'wema bank': 'Wema Bank',
            'polaris': 'Polaris Bank',
            'polaris bank': 'Polaris Bank',
            'ecobank': 'Ecobank',
            'fcmb': 'FCMB',
            'opay': 'OPay',
            'kuda': 'Kuda Bank',
            'kuda bank': 'Kuda Bank',
            'palmpay': 'PalmPay'
        }
    
    def _is_bank_name(self, text: str):
        """Check if text is a bank name"""
        if not text:
            return False
        text_lower = text.lower().strip()
        banks = self._get_bank_names()
        
        # Check if the text itself is a bank name
        if text_lower in banks:
            return True
        
        # Check if any bank name is in the text
        for bank_key in banks.keys():
            if bank_key == text_lower or text_lower.startswith(bank_key + ' ') or text_lower.endswith(' ' + bank_key):
                return True
        
        return False
    
    def _extract_customer_bank(self, text: str):
        """Extract customer's bank from transaction details (e.g., 'From GTBank/...')"""
        if not text:
            return ''
        
        banks = self._get_bank_names()
        text_lower = text.lower()
        
        # Try to extract bank name after "From" keyword
        from_match = re.search(r'from\s+([A-Za-z][A-Za-z\s]*?)[\s/\-]', text_lower)
        if from_match:
            bank_candidate = from_match.group(1).strip()
            for key, value in banks.items():
                if key in bank_candidate:
                    return value
        
        # Fallback: search for any bank name in the text
        for key, value in banks.items():
            if key in text_lower:
                return value
        
        return ''
    
    def _extract_customer_from_details(self, text: str):
        """Extract a customer name and email from common bank narrations.
        Handles formats like:
        - "... From GTBank/ADEYINKA MICHAEL OLALEKAN/..."
        - "... FROM WEMA/ JOHNSON OLATUNJI OLADEJI- ..."
        - "... NEFT CR FROM JOHN DOE - 0123 - ACCESS BANK ..."
        Returns (name, email).
        """
        if not text:
            return ('', '')
        original = str(text)
        
        # Extract email if present
        email_match = re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', original)
        email = email_match.group(0) if email_match else ''
        
        # Primary: after "from"
        name = ''
        m = re.search(r'from\s+(.*)', original, flags=re.IGNORECASE)
        if m:
            tail = m.group(1)
            # If slash-delimited, the token after the first '/' is usually the payer name
            if '/' in tail:
                tokens = [t.strip() for t in tail.split('/') if t.strip()]
                # Find the first token that contains letters (not just numbers) and is NOT a bank name
                candidate = ''
                for token in tokens:
                    # Skip tokens that are purely numeric or reference-like
                    if re.search(r'[A-Za-z]', token) and not re.match(r'^[\d\s]+$', token):
                        # Take the part before any dash or hyphen for company names
                        parts = re.split(r'\s+-\s+|\s*-\s*', token)
                        test_candidate = parts[0].strip()
                        
                        # Skip if this is a bank name
                        if not self._is_bank_name(test_candidate):
                            candidate = test_candidate
                            break
                
                if not candidate and tokens:
                    # Last resort: take first non-bank token
                    for token in tokens:
                        if not self._is_bank_name(token):
                            candidate = token
                            break
            else:
                # Otherwise, take up to the first dash or end
                candidate = re.split(r'\s-\s|[-]|;', tail)[0].strip()
            
            # Clean candidate of trailing non-letters/numbers
            candidate = re.sub(r'\s{2,}', ' ', candidate)
            # Guard against bank/system words, bank names, and purely numeric
            bad_words = {'providus','transfer','credit','reversal','charge','fee','branch'}
            if candidate and not any(w in candidate.lower() for w in bad_words) and not self._is_bank_name(candidate) and re.search(r'[A-Za-z]', candidate):
                name = candidate
        
        # Fallback: pick the longest title-cased 2–6 word chunk
        if not name:
            tokens = [t for t in re.split(r'\s+', re.sub(r'[^A-Za-z\s]', ' ', original)) if t]
            cur = []
            cands = []
            for t in tokens:
                if t[:1].isalpha() and (t.istitle() or t.isupper()):
                    cur.append(t)
                else:
                    if 2 <= len(cur) <= 6:
                        cands.append(' '.join(cur))
                    cur = []
            if 2 <= len(cur) <= 6:
                cands.append(' '.join(cur))
            cands.sort(key=lambda s: sum(ch.isalpha() for ch in s), reverse=True)
            name = cands[0] if cands else ''
        
        # Normalize length and casing
        name = name.strip()
        if name and name.isupper():
            name = name.title()
        if len(name) > 80:
            name = name[:80]
        if any(bad in name.lower() for bad in ['providus', 'bank', 'reversal', 'charge', 'fee']):
            name = ''
        
        return (name, email)

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure column names are strings and unique to avoid pandas reindexing errors.
        Also trims whitespace and replaces empty names with placeholders.
        """
        seen = {}
        normalized = []
        for c in list(df.columns):
            name = str(c).strip() if c is not None else 'column'
            if name == '' or name.lower() in ('none', 'nan'):
                name = 'column'
            base = name
            if base in seen:
                seen[base] += 1
                name = f"{base}_{seen[base]}"
            else:
                seen[base] = 1
            normalized.append(name)
        df.columns = normalized
        return df

    def _try_providus_reheader(self, df: pd.DataFrame) -> pd.DataFrame:
        """For messy Providus exports (PDF->XLSX), find the header row that contains
        expected labels and rebuild the DataFrame with that row as header.
        """
        candidate_row = None
        max_scan = min(600, len(df))
        wanted = [
            'transaction date', 'actual transaction date', 'value date',
            'transaction details', 'narration', 'description',
            'credit amount', 'debit amount', 'current balance', 'dr/cr'
        ]
        for i in range(max_scan):
            row = df.iloc[i].astype(str).str.lower().str.strip().tolist()
            # score row by number of header tokens found
            score = sum(1 for w in wanted if any(w in cell for cell in row))
            if score >= 4 and any('transaction date' in cell for cell in row) and any('transaction details' in cell for cell in row):
                candidate_row = i
                break
        if candidate_row is not None:
            headers = [str(h).strip() for h in df.iloc[candidate_row].tolist()]
            rebuilt = df.iloc[candidate_row + 1:].copy()
            rebuilt.columns = headers
            rebuilt = self._normalize_columns(rebuilt)
            try:
                rebuilt = rebuilt.loc[:, ~pd.Index(rebuilt.columns).duplicated()]
            except Exception:
                pass
            # Drop columns that are entirely NaN/empty after header row
            rebuilt = rebuilt.dropna(axis=1, how='all')
            return rebuilt
        return df

# ==================== HELPER FUNCTIONS ====================

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ==================== ROUTES ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    """Handle file upload"""
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('index'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('index'))
    
    if not allowed_file(file.filename):
        flash('Invalid file type. Please upload CSV, Excel, or PDF files.', 'error')
        return redirect(url_for('index'))

    try:
        # Save file
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Process file
        processor = FileProcessor()
        transactions = processor.process(filepath, filename)

        if not transactions:
            flash('No valid transactions found in file', 'warning')
            return redirect(url_for('index'))

        # Check if file was already processed
        existing = Transaction.query.filter_by(file_source=filename).first()
        if existing:
            flash(f'File "{filename}" has already been processed. Please delete old data first or upload a different file.', 'warning')
            return redirect(url_for('index'))

        # Save to database
        saved = 0
        for trans_data in transactions:
            transaction = Transaction(
                date=trans_data['date'],
                amount=trans_data['amount'],
                customer_name=trans_data['customer_name'],
                customer_email=trans_data['customer_email'],
                reference=trans_data['reference'],
                description=trans_data['description'],
                bank=trans_data['bank'],
                customer_bank=trans_data.get('customer_bank', ''),
                channel=trans_data.get('channel', ''),
                card_type=trans_data.get('card_type', ''),
                status=trans_data.get('status', 'success'),
                gateway_response=trans_data.get('gateway_response', ''),
                file_source=trans_data['file_source']
            )
            db.session.add(transaction)
            saved += 1
        
        db.session.commit()
        flash(f'Successfully processed {saved} transactions from {filename}', 'success')
    
    except Exception as e:
        flash(f'Error processing file: {str(e)}', 'error')
        print(f"Error: {e}")
    
    return redirect(url_for('index'))

@app.route('/customers')
def customers():
    return render_template('customers.html')

@app.route('/customer/<path:customer_id>')
def customer_detail(customer_id):
    """Customer detail page"""
    # Decode customer ID (can be email or name)
    from urllib.parse import unquote
    customer_identifier = unquote(customer_id)
    return render_template('customer_detail.html', customer_identifier=customer_identifier)

@app.route('/search')
def search():
    return render_template('search.html')

@app.route('/reports')
def reports():
    return render_template('reports.html')

@app.route('/database/clear', methods=['POST'])
def clear_database():
    """Clear all transactions from database"""
    try:
        Transaction.query.delete()
        db.session.commit()
        flash('Database cleared successfully', 'success')
    except Exception as e:
        flash(f'Error clearing database: {str(e)}', 'error')
    return redirect(url_for('index'))

@app.route('/database')
def database():
    return render_template('database.html')

# ==================== API ENDPOINTS ====================

@app.route('/api/stats')
def api_stats():
    """Get dashboard statistics"""
    total_transactions = Transaction.query.count()
    total_amount = db.session.query(db.func.sum(Transaction.amount)).scalar() or 0
    
    # Unique customers (excluding Unknown)
    unique_customers = db.session.query(Transaction.customer_name).distinct().filter(
        Transaction.customer_name != 'Unknown Customer'
    ).count()
    
    # Unique banks
    unique_banks = db.session.query(Transaction.bank).distinct().count()
    
    # Top 5 customers
    top_customers = db.session.query(
        Transaction.customer_name,
        db.func.sum(Transaction.amount).label('total'),
        db.func.count(Transaction.id).label('count')
    ).filter(
        Transaction.customer_name != 'Unknown Customer'
    ).group_by(Transaction.customer_name).order_by(
        db.func.sum(Transaction.amount).desc()
    ).limit(5).all()
    
    return jsonify({
        'total_transactions': total_transactions,
        'total_amount': float(total_amount),
        'unique_customers': unique_customers,
        'unique_banks': unique_banks,
        'top_customers': [{
            'name': c[0],
            'total': float(c[1]),
            'count': c[2]
        } for c in top_customers]
    })

@app.route('/api/transactions')
def api_transactions():
    """Get all transactions"""
    transactions = Transaction.query.order_by(Transaction.date.desc()).all()
    return jsonify([t.to_dict() for t in transactions])

@app.route('/api/customers')
def api_customers():
    """Get customer analytics with optional filters"""
    bank_filter = request.args.get('bank', '')
    file_filter = request.args.get('file', '')
    
    # Build query
    query = db.session.query(
        Transaction.customer_name,
        Transaction.customer_email,
        db.func.count(Transaction.id).label('transaction_count'),
        db.func.sum(Transaction.amount).label('total_amount'),
        db.func.avg(Transaction.amount).label('avg_amount'),
        db.func.min(Transaction.date).label('first_transaction'),
        db.func.max(Transaction.date).label('last_transaction')
    ).filter(
        Transaction.customer_name.notin_(['Unknown Customer'])
    )
    
    # Apply filters
    if bank_filter:
        query = query.filter(Transaction.bank == bank_filter)
    if file_filter:
        query = query.filter(Transaction.file_source == file_filter)
    
    customers = query.group_by(
        Transaction.customer_name, Transaction.customer_email
    ).order_by(
        Transaction.customer_name.asc()
    ).all()
    
    # Get available banks and files for filter dropdowns
    banks = db.session.query(Transaction.bank).distinct().all()
    files = db.session.query(Transaction.file_source).distinct().all()
    
    return jsonify({
        'customers': [{
            'name': c[0],
            'email': c[1],
            'transaction_count': c[2],
            'total_amount': float(c[3]),
            'avg_amount': float(c[4]),
            'first_transaction': c[5].isoformat() if c[5] else None,
            'last_transaction': c[6].isoformat() if c[6] else None
        } for c in customers],
        'available_banks': [b[0] for b in banks if b[0]],
        'available_files': [f[0] for f in files if f[0]]
    })

@app.route('/api/customer/<path:customer_identifier>/transactions')
def api_customer_transactions(customer_identifier):
    """Get all transactions for a specific customer"""
    from urllib.parse import unquote
    identifier = unquote(customer_identifier)
    
    # Get filters from query params
    channel = request.args.get('channel', '')
    bank = request.args.get('bank', '')
    customer_bank = request.args.get('customer_bank', '')
    
    # Build query - try email first, then name
    query = Transaction.query.filter(
        (Transaction.customer_email == identifier) | (Transaction.customer_name == identifier)
    )
    
    if channel:
        query = query.filter(Transaction.channel == channel)
    if bank:
        query = query.filter(Transaction.bank == bank)
    if customer_bank:
        query = query.filter(Transaction.customer_bank == customer_bank)
    
    transactions = query.order_by(Transaction.date.desc()).all()
    
    return jsonify({
        'transactions': [t.to_dict() for t in transactions],
        'summary': {
            'total_transactions': len(transactions),
            'total_amount': sum(t.amount for t in transactions),
            'avg_amount': sum(t.amount for t in transactions) / len(transactions) if transactions else 0
        }
    })

@app.route('/api/search')
def api_search():
    """Search transactions"""
    query = request.args.get('q', '').strip()
    
    if not query:
        return jsonify([])
    
    transactions = Transaction.query.filter(
        db.or_(
            Transaction.customer_name.ilike(f'%{query}%'),
            Transaction.customer_email.ilike(f'%{query}%'),
            Transaction.reference.ilike(f'%{query}%'),
            Transaction.description.ilike(f'%{query}%')
        )
    ).order_by(Transaction.date.desc()).limit(100).all()
    
    return jsonify([t.to_dict() for t in transactions])

@app.route('/api/reports')
def api_reports():
    """Get reports data"""
    # Get time filter from query params (default to 30 days)
    days = request.args.get('days', '30')
    try:
        days = int(days)
    except:
        days = 30
    
    # Calculate filter date (0 or negative means all time)
    if days > 0:
        filter_date = datetime.now(timezone.utc) - timedelta(days=days)
        daily_data = db.session.query(
            db.func.date(Transaction.date).label('date'),
            db.func.sum(Transaction.amount).label('amount'),
            db.func.count(Transaction.id).label('count')
        ).filter(
            Transaction.date >= filter_date
        ).group_by(
            db.func.date(Transaction.date)
        ).order_by('date').all()
        
        # Total for filtered period
        total_revenue = db.session.query(
            db.func.sum(Transaction.amount)
        ).filter(Transaction.date >= filter_date).scalar() or 0
        
        total_transactions = Transaction.query.filter(Transaction.date >= filter_date).count()
    else:
        # All time data
        daily_data = db.session.query(
            db.func.date(Transaction.date).label('date'),
            db.func.sum(Transaction.amount).label('amount'),
            db.func.count(Transaction.id).label('count')
        ).group_by(
            db.func.date(Transaction.date)
        ).order_by('date').all()
        
        total_revenue = db.session.query(db.func.sum(Transaction.amount)).scalar() or 0
        total_transactions = Transaction.query.count()
    
    # Bank breakdown (always all time)
    bank_data = db.session.query(
        Transaction.bank,
        db.func.sum(Transaction.amount).label('amount'),
        db.func.count(Transaction.id).label('count')
    ).group_by(Transaction.bank).order_by(Transaction.bank).all()
    
    # Channel breakdown (payment method)
    channel_data = db.session.query(
        Transaction.channel,
        db.func.sum(Transaction.amount).label('amount'),
        db.func.count(Transaction.id).label('count')
    ).filter(
        Transaction.channel != ''
    ).group_by(Transaction.channel).all()
    
    # Customer banks breakdown (top 10)
    customer_bank_data = db.session.query(
        Transaction.customer_bank,
        db.func.sum(Transaction.amount).label('amount'),
        db.func.count(Transaction.id).label('count')
    ).filter(
        Transaction.customer_bank != ''
    ).group_by(Transaction.customer_bank).order_by(
        db.func.sum(Transaction.amount).desc()
    ).limit(10).all()
    
    # Calculate average transaction
    avg_transaction = total_revenue / total_transactions if total_transactions > 0 else 0
    
    return jsonify({
        'total_revenue': float(total_revenue),
        'total_transactions': total_transactions,
        'avg_transaction': float(avg_transaction),
        'daily': [{
            'date': str(d[0]),
            'amount': float(d[1]),
            'count': d[2]
        } for d in daily_data],
        'banks': [{
            'bank': b[0],
            'amount': float(b[1]),
            'count': b[2]
        } for b in bank_data],
        'channels': [{
            'channel': c[0],
            'amount': float(c[1]),
            'count': c[2]
        } for c in channel_data],
        'customer_banks': [{
            'bank': cb[0],
            'amount': float(cb[1]),
            'count': cb[2]
        } for cb in customer_bank_data]
    })

@app.route('/api/database/files')
def api_database_files():
    """Get uploaded files"""
    files = db.session.query(
        Transaction.file_source,
        Transaction.bank,
        db.func.count(Transaction.id).label('count'),
        db.func.sum(Transaction.amount).label('total'),
        db.func.min(Transaction.date).label('first_date'),
        db.func.max(Transaction.date).label('last_date')
    ).group_by(
        Transaction.file_source, Transaction.bank
    ).all()
    
    return jsonify([{
        'filename': f[0],
        'bank': f[1],
        'count': f[2],
        'total': float(f[3]),
        'first_date': f[4].isoformat() if f[4] else None,
        'last_date': f[5].isoformat() if f[5] else None
    } for f in files])

@app.route('/api/database/clear', methods=['POST'])
def api_database_clear():
    """Clear all transactions"""
    try:
        count = Transaction.query.delete()
        db.session.commit()
        return jsonify({'success': True, 'deleted': count})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/database/delete-file', methods=['POST'])
def api_database_delete_file():
    """Delete transactions from specific file"""
    try:
        filename = request.json.get('filename')
        count = Transaction.query.filter_by(file_source=filename).delete()
        db.session.commit()
        return jsonify({'success': True, 'deleted': count})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== INITIALIZATION ====================

# ==================== EXPORT ROUTES ====================

@app.route('/export/customer/<path:customer_identifier>/<format>')
def export_customer_transactions(customer_identifier, format):
    """Export customer transactions to CSV, Excel, or PDF"""
    from urllib.parse import unquote
    identifier = unquote(customer_identifier)
    
    transactions = Transaction.query.filter(
        (Transaction.customer_email == identifier) | (Transaction.customer_name == identifier)
    ).order_by(Transaction.date.desc()).all()
    
    if not transactions:
        flash('No transactions found for this customer', 'warning')
        return redirect(url_for('customers'))
    
    customer_name = transactions[0].customer_name
    
    if format == 'csv':
        # Create CSV
        df = pd.DataFrame([{
            'Date': t.date.strftime('%Y-%m-%d %H:%M:%S'),
            'Amount': t.amount,
            'Reference': t.reference,
            'Description': t.description,
            'Payment Processor': t.bank,
            'Customer Bank': t.customer_bank,
            'Channel': t.channel,
            'Card Type': t.card_type,
            'File Source': t.file_source
        } for t in transactions])
        
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename={customer_name}_transactions.csv'
        response.headers['Content-Type'] = 'text/csv'
        return response
    
    elif format == 'excel':
        # Create Excel
        df = pd.DataFrame([{
            'Date': t.date.strftime('%Y-%m-%d %H:%M:%S'),
            'Amount': t.amount,
            'Reference': t.reference,
            'Description': t.description,
            'Payment Processor': t.bank,
            'Customer Bank': t.customer_bank,
            'Channel': t.channel,
            'Card Type': t.card_type,
            'File Source': t.file_source
        } for t in transactions])
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Transactions')
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename={customer_name}_transactions.xlsx'
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return response
    
    elif format == 'pdf':
        # Create PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        elements = []
        
        styles = getSampleStyleSheet()
        title = Paragraph(f"<b>Transaction Report: {customer_name}</b>", styles['Title'])
        elements.append(title)
        elements.append(Spacer(1, 0.3*inch))
        
        # Summary
        total_amount = sum(t.amount for t in transactions)
        summary_text = f"<b>Total Transactions:</b> {len(transactions)}<br/><b>Total Amount:</b> ₦{total_amount:,.2f}"
        summary = Paragraph(summary_text, styles['Normal'])
        elements.append(summary)
        elements.append(Spacer(1, 0.3*inch))
        
        # Table data
        data = [['Date', 'Amount', 'Bank', 'Channel', 'Reference']]
        for t in transactions[:50]:  # Limit to 50 for PDF
            data.append([
                t.date.strftime('%Y-%m-%d'),
                f"₦{t.amount:,.2f}",
                t.bank,
                t.channel or '-',
                t.reference[:15] if t.reference else '-'
            ])
        
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(table)
        
        doc.build(elements)
        buffer.seek(0)
        
        return send_file(buffer, as_attachment=True, download_name=f'{customer_name}_transactions.pdf', mimetype='application/pdf')
    
    return redirect(url_for('customers'))

@app.route('/export/reports/<format>')
def export_reports(format):
    """Export financial reports to CSV, Excel, or PDF"""
    transactions = Transaction.query.order_by(Transaction.date.desc()).all()
    
    if not transactions:
        flash('No transactions found', 'warning')
        return redirect(url_for('reports'))
    
    if format == 'csv':
        # Create CSV
        df = pd.DataFrame([{
            'Date': t.date.strftime('%Y-%m-%d %H:%M:%S'),
            'Customer Name': t.customer_name,
            'Customer Email': t.customer_email,
            'Amount': t.amount,
            'Reference': t.reference,
            'Description': t.description,
            'Payment Processor': t.bank,
            'Customer Bank': t.customer_bank,
            'Channel': t.channel,
            'Card Type': t.card_type,
            'File Source': t.file_source
        } for t in transactions])
        
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=financial_report_{datetime.now().strftime("%Y%m%d")}.csv'
        response.headers['Content-Type'] = 'text/csv'
        return response
    
    elif format == 'excel':
        # Create Excel with multiple sheets
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # All transactions sheet
            df_transactions = pd.DataFrame([{
                'Date': t.date.strftime('%Y-%m-%d %H:%M:%S'),
                'Customer Name': t.customer_name,
                'Customer Email': t.customer_email,
                'Amount': t.amount,
                'Reference': t.reference,
                'Payment Processor': t.bank,
                'Customer Bank': t.customer_bank,
                'Channel': t.channel,
                'Card Type': t.card_type
            } for t in transactions])
            df_transactions.to_excel(writer, index=False, sheet_name='All Transactions')
            
            # Summary by customer
            customer_summary = db.session.query(
                Transaction.customer_name,
                db.func.count(Transaction.id).label('count'),
                db.func.sum(Transaction.amount).label('total')
            ).filter(Transaction.customer_name != 'Unknown Customer').group_by(Transaction.customer_name).all()
            
            df_customer_summary = pd.DataFrame([{
                'Customer Name': c[0],
                'Total Transactions': c[1],
                'Total Amount': c[2]
            } for c in customer_summary])
            df_customer_summary.to_excel(writer, index=False, sheet_name='Customer Summary')
            
            # Summary by channel
            channel_summary = db.session.query(
                Transaction.channel,
                db.func.count(Transaction.id).label('count'),
                db.func.sum(Transaction.amount).label('total')
            ).filter(Transaction.channel != '').group_by(Transaction.channel).all()
            
            df_channel_summary = pd.DataFrame([{
                'Channel': c[0],
                'Total Transactions': c[1],
                'Total Amount': c[2]
            } for c in channel_summary])
            df_channel_summary.to_excel(writer, index=False, sheet_name='Channel Summary')
        
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=financial_report_{datetime.now().strftime("%Y%m%d")}.xlsx'
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return response
    
    elif format == 'pdf':
        # Create PDF summary report
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        elements = []
        
        styles = getSampleStyleSheet()
        title = Paragraph("<b>Financial Report</b>", styles['Title'])
        elements.append(title)
        elements.append(Spacer(1, 0.2*inch))
        
        # Summary statistics
        total_amount = sum(t.amount for t in transactions)
        total_count = len(transactions)
        avg_amount = total_amount / total_count if total_count > 0 else 0
        
        summary_text = f"""
        <b>Report Generated:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}<br/>
        <b>Total Transactions:</b> {total_count:,}<br/>
        <b>Total Revenue:</b> ₦{total_amount:,.2f}<br/>
        <b>Average Transaction:</b> ₦{avg_amount:,.2f}
        """
        summary = Paragraph(summary_text, styles['Normal'])
        elements.append(summary)
        elements.append(Spacer(1, 0.3*inch))
        
        # Recent transactions table
        elements.append(Paragraph("<b>Recent Transactions (Last 30)</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.1*inch))
        
        data = [['Date', 'Customer', 'Amount', 'Channel', 'Bank']]
        for t in transactions[:30]:
            data.append([
                t.date.strftime('%Y-%m-%d'),
                t.customer_name[:20] if t.customer_name else '-',
                f"₦{t.amount:,.0f}",
                t.channel or '-',
                t.customer_bank[:15] if t.customer_bank else '-'
            ])
        
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(table)
        
        doc.build(elements)
        buffer.seek(0)
        
        return send_file(buffer, as_attachment=True, download_name=f'financial_report_{datetime.now().strftime("%Y%m%d")}.pdf', mimetype='application/pdf')
    
    return redirect(url_for('reports'))

@app.route('/export/monthly-customer-report')
def export_monthly_customer_report():
    """Export monthly customer payment report with summary and individual sheets"""
    from datetime import datetime
    from calendar import month_name
    
    # Get all transactions
    transactions = Transaction.query.order_by(Transaction.customer_name, Transaction.date).all()
    
    if not transactions:
        flash('No transactions found', 'warning')
        return redirect(url_for('reports'))
    
    # Group transactions by customer and month
    customer_data = {}
    for t in transactions:
        customer = t.customer_name
        if customer not in customer_data:
            customer_data[customer] = {}
        
        # Get year-month key (e.g., "2025-01" for Jan 2025)
        month_key = t.date.strftime('%Y-%m')
        
        if month_key not in customer_data[customer]:
            customer_data[customer][month_key] = {
                'amount': 0,
                'count': 0,
                'transactions': []
            }
        
        customer_data[customer][month_key]['amount'] += t.amount
        customer_data[customer][month_key]['count'] += 1
        customer_data[customer][month_key]['transactions'].append(t)
    
    # Create Excel with multiple sheets
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        
        # SHEET 1: SUMMARY - All customers with monthly breakdown
        summary_rows = []
        
        # Get all unique months across all transactions
        all_months = sorted(set(
            t.date.strftime('%Y-%m') 
            for t in transactions
        ))
        
        # Create summary data
        for customer, months_data in sorted(customer_data.items()):
            row = {'Customer Name': customer}
            total = 0
            
            for month_key in all_months:
                # Format month as "Jan 2025", "Feb 2025", etc.
                year, month = month_key.split('-')
                month_label = f"{month_name[int(month)]} {year}"
                
                if month_key in months_data:
                    amount = months_data[month_key]['amount']
                    row[month_label] = amount
                    total += amount
                else:
                    row[month_label] = 0
            
            row['Total'] = total
            summary_rows.append(row)
        
        df_summary = pd.DataFrame(summary_rows)
        df_summary.to_excel(writer, index=False, sheet_name='Summary')
        
        # Format the summary sheet
        workbook = writer.book
        summary_sheet = writer.sheets['Summary']
        
        # Format currency columns
        from openpyxl.styles import numbers
        for row in summary_sheet.iter_rows(min_row=2, min_col=2):
            for cell in row:
                cell.number_format = '#,##0.00'
        
        # SHEET 2+: Individual customer sheets (top 50 customers by revenue)
        # Sort customers by total revenue
        sorted_customers = sorted(
            customer_data.items(),
            key=lambda x: sum(m['amount'] for m in x[1].values()),
            reverse=True
        )[:50]  # Limit to top 50 to avoid too many sheets
        
        for customer, months_data in sorted_customers:
            # Clean sheet name (Excel has 31 char limit and no special chars: / \ : * ? [ ])
            sheet_name = customer[:31]
            # Replace all invalid Excel sheet name characters
            invalid_chars = ['/', '\\', ':', '*', '?', '[', ']']
            for char in invalid_chars:
                sheet_name = sheet_name.replace(char, '-')
            
            # Create detailed transaction list for this customer
            customer_rows = []
            for month_key in sorted(months_data.keys()):
                year, month = month_key.split('-')
                month_label = f"{month_name[int(month)]} {year}"
                
                for t in months_data[month_key]['transactions']:
                    customer_rows.append({
                        'Date': t.date.strftime('%Y-%m-%d'),
                        'Month': month_label,
                        'Amount': t.amount,
                        'Reference': t.reference,
                        'Payment Processor': t.bank,
                        'Customer Bank': t.customer_bank,
                        'Channel': t.channel,
                        'Gateway Response': t.gateway_response[:100] if t.gateway_response else ''
                    })
            
            df_customer = pd.DataFrame(customer_rows)
            
            # Add summary row at the bottom
            total_amount = sum(row['Amount'] for row in customer_rows)
            summary_row = pd.DataFrame([{
                'Date': '',
                'Month': 'TOTAL',
                'Amount': total_amount,
                'Reference': '',
                'Payment Processor': '',
                'Customer Bank': '',
                'Channel': '',
                'Gateway Response': ''
            }])
            
            df_customer = pd.concat([df_customer, summary_row], ignore_index=True)
            df_customer.to_excel(writer, index=False, sheet_name=sheet_name)
            
            # Format the customer sheet
            customer_sheet = writer.sheets[sheet_name]
            for row in customer_sheet.iter_rows(min_row=2, min_col=3, max_col=3):
                for cell in row:
                    cell.number_format = '#,##0.00'
    
    output.seek(0)
    
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename=monthly_customer_report_{datetime.now().strftime("%Y%m%d")}.xlsx'
    response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return response

# ==================== MRC ANALYZER ====================

class MRCExcelCrossReferenceProcessor:
    """Process Excel file with customer names and transactions for cross-reference analysis"""
    
    def __init__(self):
        self.customers = {}
        self.transactions = []
    
    def process_file(self, filepath):
        """Process the Excel file and cross-reference customers with transactions"""
        try:
            # Read both sheets
            customers_df = pd.read_excel(filepath, sheet_name=0)  # First sheet - customers
            transactions_df = pd.read_excel(filepath, sheet_name=1)  # Second sheet - transactions
            
            # Clean and process customer names
            customer_names = self._extract_customer_names(customers_df)
            
            # Process transactions
            processed_transactions = self._process_transactions(transactions_df)
            
            # Cross-reference customers with transactions
            cross_referenced_data = self._cross_reference(customer_names, processed_transactions)
            
            return {
                'success': True,
                'data': cross_referenced_data
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _extract_customer_names(self, customers_df):
        """Extract and clean customer names from the customers sheet"""
        customer_names = []
        
        # Get names from the 'Names' column
        if 'Names' in customers_df.columns:
            for name in customers_df['Names'].dropna():
                # First try to extract names from transaction descriptions
                extracted_names = self._extract_names_from_description(str(name))
                if extracted_names:
                    for extracted_name in extracted_names:
                        cleaned_name = self._clean_customer_name(extracted_name)
                        if cleaned_name and len(cleaned_name) > 2 and self._is_valid_customer_name(cleaned_name):
                            customer_names.append(cleaned_name)
                else:
                    # If no names extracted, treat as direct customer name
                    cleaned_name = self._clean_customer_name(str(name))
                    if cleaned_name and len(cleaned_name) > 2 and self._is_valid_customer_name(cleaned_name):
                        customer_names.append(cleaned_name)
        
        return list(set(customer_names))  # Remove duplicates
    
    def _extract_names_from_description(self, text):
        """Extract customer names from transaction descriptions"""
        extracted_names = []
        text = str(text).strip()
        
        # Pattern 1: "Transfer from [NAME]"
        transfer_pattern = r'Transfer from\s+([A-Z][A-Z\s]+?)(?:\d|$)'
        matches = re.findall(transfer_pattern, text, re.IGNORECASE)
        for match in matches:
            name = match.strip()
            if len(name) > 3 and any(c.isalpha() for c in name):
                extracted_names.append(name)
        
        # Pattern 2: "INWARD TRANSFER... FROM [NAME]"
        inward_pattern = r'INWARD TRANSFER[^(]*FROM\s+([A-Z][A-Z\s]+?)(?:\d|$)'
        matches = re.findall(inward_pattern, text, re.IGNORECASE)
        for match in matches:
            name = match.strip()
            if len(name) > 3 and any(c.isalpha() for c in name):
                extracted_names.append(name)
        
        # Pattern 3: Look for capitalized words that look like names
        # This catches names that might be in other formats
        name_pattern = r'\b([A-Z][A-Z\s]{2,20})\b'
        potential_names = re.findall(name_pattern, text)
        for name in potential_names:
            name = name.strip()
            # Skip if it's clearly a transaction term
            if not any(term in name.lower() for term in ['transfer', 'banking', 'trf', 'inward', 'nip', 'fcmb', 'acb', 'sky']):
                if len(name) > 3 and any(c.isalpha() for c in name):
                    extracted_names.append(name)
        
        return extracted_names
    
    def _is_valid_customer_name(self, name):
        """Check if the cleaned name is actually a customer name, not a transaction description"""
        name_lower = name.lower()
        
        # Filter out transaction descriptions and bank names
        transaction_indicators = [
            'mbanking trf', 'mobile banking', 'trf from', 'transfer from',
            'nip frm', 'inward transfer', 'app:', 'fcmb/', 'acb/',
            'paystack', 'providus', 'banking', 'trf:', 'transfer'
        ]
        
        # Common bank names to filter out
        bank_names = ['uba', 'fcmb', 'acb', 'gtb', 'access', 'zenith', 'first bank', 'firstbank', 'wema', 'union', 'sterling', 'polaris', 'kuda', 'opay', 'palmpay']
        
        # If it contains transaction indicators, it's not a customer name
        for indicator in transaction_indicators:
            if indicator in name_lower:
                return False
        
        # If it's a known bank name, it's not a customer name
        for bank in bank_names:
            if bank in name_lower:
                return False
        
        # Must be at least 3 characters and contain letters
        if len(name) < 3 or not any(c.isalpha() for c in name):
            return False
        
        # Must have at least 2 words (first name + last name)
        if len(name.split()) < 2:
            return False
            
        return True
    
    def _process_transactions(self, transactions_df):
        """Process transactions from the transactions sheet"""
        processed = []
        
        for _, row in transactions_df.iterrows():
            try:
                # Parse date
                date_str = str(row['Value Date']).strip()
                if date_str and date_str != 'nan':
                    # Convert date format (e.g., "01 Jan 2025" to datetime)
                    date_obj = pd.to_datetime(date_str, format='%d %b %Y', errors='coerce')
                    if pd.isna(date_obj):
                        # Try alternative format
                        date_obj = pd.to_datetime(date_str, errors='coerce')
                    
                    if not pd.isna(date_obj):
                        # Get transaction details
                        details = str(row['Transaction Details']).strip() if pd.notna(row['Transaction Details']) else ''
                        
                        # Get amount
                        amount = 0
                        if pd.notna(row['Deposit']) and str(row['Deposit']).strip() != 'nan':
                            try:
                                amount = float(str(row['Deposit']).replace(',', ''))
                            except:
                                amount = 0
                        
                        if amount > 0 and details:
                            processed.append({
                                'date': date_obj,
                                'details': details,
                                'amount': amount,
                                'month': date_obj.strftime('%B'),
                                'year': date_obj.year
                            })
            except Exception as e:
                continue  # Skip problematic rows
        
        return processed
    
    def _cross_reference(self, customer_names, transactions):
        """Cross-reference customer names with transactions"""
        customers_data = {}
        
        # Initialize customer data
        for customer in customer_names:
            customers_data[customer] = {
                'total_mrc': 0,
                'total_nrc': 0,
                'transaction_count': 0,
                'monthly_breakdown': {},
                'transactions': []
            }
        
        # Match transactions to customers
        for transaction in transactions:
            matched_customer = self._find_matching_customer(transaction['details'], customer_names)
            
            if matched_customer:
                # Determine if it's MRC or NRC based on transaction details
                transaction_type = self._classify_transaction(transaction['details'])
                
                # Add to customer data
                customers_data[matched_customer]['transactions'].append({
                    'date': transaction['date'].strftime('%Y-%m-%d'),
                    'month': transaction['month'],
                    'amount': transaction['amount'],
                    'type': transaction_type,
                    'details': transaction['details']
                })
                
                customers_data[matched_customer]['transaction_count'] += 1
                
                # Add to monthly breakdown
                month = transaction['month']
                if month not in customers_data[matched_customer]['monthly_breakdown']:
                    customers_data[matched_customer]['monthly_breakdown'][month] = 0
                customers_data[matched_customer]['monthly_breakdown'][month] += transaction['amount']
                
                # Add to totals
                if transaction_type == 'MRC':
                    customers_data[matched_customer]['total_mrc'] += transaction['amount']
                elif transaction_type == 'NRC':
                    customers_data[matched_customer]['total_nrc'] += transaction['amount']
        
        # Calculate summary statistics
        total_customers = len(customers_data)
        total_entries = len(customer_names)  # Total entries before deduplication
        total_mrc = sum(customer['total_mrc'] for customer in customers_data.values())
        total_nrc = sum(customer['total_nrc'] for customer in customers_data.values())
        total_transactions = sum(customer['transaction_count'] for customer in customers_data.values())
        
        return {
            'customers': customers_data,
            'summary': {
                'total_customers': total_customers,
                'total_entries': total_entries,
                'total_mrc': total_mrc,
                'total_nrc': total_nrc,
                'total_transactions': total_transactions
            }
        }
    
    def _find_matching_customer(self, transaction_details, customer_names):
        """Find which customer a transaction belongs to"""
        details_lower = transaction_details.lower()
        
        for customer in customer_names:
            customer_lower = customer.lower()
            
            # Direct name match
            if customer_lower in details_lower:
                return customer
            
            # Extract name from common patterns
            extracted_name = self._extract_name_from_transaction(transaction_details)
            if extracted_name and extracted_name.lower() in customer_lower:
                return customer
        
        return None
    
    def _extract_name_from_transaction(self, transaction_details):
        """Extract customer name from transaction details"""
        # Common patterns in transaction details
        patterns = [
            r'NIP FRM ([^-]+)-',  # NIP FRM NAME-
            r'TRF From ([^/]+)/',  # TRF From NAME/
            r'From ([^/]+)/',  # From NAME/
            r'INWARD TRANSFER[^(]*\([^)]*\)\s*From[^/]*/([^/]+)/',  # INWARD TRANSFER From Bank/NAME/
        ]
        
        for pattern in patterns:
            match = re.search(pattern, transaction_details, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                return self._clean_customer_name(name)
        
        return None
    
    def _classify_transaction(self, transaction_details):
        """Classify transaction as MRC or NRC based on details"""
        details_lower = transaction_details.lower()
        
        # NRC indicators
        nrc_keywords = ['installation', 'install', 'setup', 'nrc']
        if any(keyword in details_lower for keyword in nrc_keywords):
            return 'NRC'
        
        # Default to MRC for subscription payments
        return 'MRC'
    
    def _clean_customer_name(self, name):
        """Clean and normalize customer name"""
        if not name or name.strip() == '' or str(name).lower() == 'nan':
            return None
        
        name = str(name).strip()
        
        # Remove common prefixes/suffixes
        name = re.sub(r'^(NIP FRM|TRF FROM|FROM)\s*', '', name, flags=re.IGNORECASE)
        name = re.sub(r'-[^-]*$', '', name)  # Remove everything after last dash
        name = re.sub(r'/[^/]*$', '', name)  # Remove everything after last slash
        
        # Clean up whitespace and special characters but keep more characters
        name = re.sub(r'\s+', ' ', name.strip())
        # Keep alphanumeric, spaces, hyphens, and common business characters
        name = re.sub(r'[^\w\s\-&.,()]', '', name)
        
        # Remove extra whitespace
        name = re.sub(r'\s+', ' ', name.strip())
        
        # Return None if name is too short or empty after cleaning
        if len(name) < 2:
            return None
            
        return name

class MRCProcessor:
    """Process MRC Excel files and extract customer revenue data"""
    
    def process_mrc_file(self, filepath):
        """Process MRC file and return customer analysis"""
        import pandas as pd
        import re
        
        xl_file = pd.ExcelFile(filepath)
        all_customers = {}  # {customer_name: {monthly: {}, transactions: []}}
        
        for sheet_name in xl_file.sheet_names:
            df = pd.read_excel(filepath, sheet_name=sheet_name, header=None)
            
            # Detect format based on sheet
            if sheet_name in ['August', 'September']:
                # Format: account_name (col 1), NRC (col 2), MRC (col 3)
                # Start from row 2 (after headers)
                for i in range(2, len(df)):
                    row = df.iloc[i]
                    customer_name = str(row[1]).strip() if pd.notna(row[1]) else None
                    mrc = row[3] if pd.notna(row[3]) else 0
                    nrc = row[2] if pd.notna(row[2]) else 0
                    
                    if not customer_name or customer_name.upper() in ['NAN', 'NAME', '']:
                        continue
                    
                    # Clean customer name
                    customer_name = self._clean_customer_name(customer_name)
                    
                    if customer_name not in all_customers:
                        all_customers[customer_name] = {
                            'monthly': {},
                            'transactions': [],
                            'total_mrc': 0
                        }
                    
                    # Add monthly MRC data (recurring payment only)
                    if sheet_name not in all_customers[customer_name]['monthly']:
                        all_customers[customer_name]['monthly'][sheet_name] = 0
                    all_customers[customer_name]['monthly'][sheet_name] += float(mrc) if mrc else 0
                    all_customers[customer_name]['total_mrc'] += float(mrc) if mrc else 0
                    
                    # Add transaction
                    all_customers[customer_name]['transactions'].append({
                        'date': None,
                        'month': sheet_name,
                        'amount': float(mrc) if mrc else 0,
                        'details': '(No transaction details available)',
                        'entity_number': None,
                        'nrc': float(nrc) if nrc else 0  # Show NRC as it appears in document
                    })
            
            else:
                # Format for Jan-July: date (col 1), account_name (col 2), transaction_details (col 3), 
                # entity_number (col 4), NRC (col 5), MRC (col 6)
                # Start from row 3 (after header rows)
                for i in range(3, len(df)):
                    row = df.iloc[i]
                    date = row[1] if pd.notna(row[1]) else None
                    transaction_details = str(row[3]).strip() if pd.notna(row[3]) else ''
                    entity_number = str(row[4]).strip() if pd.notna(row[4]) else None
                    nrc = row[5] if pd.notna(row[5]) else 0
                    mrc = row[6] if pd.notna(row[6]) else 0
                    
                    # Skip invalid rows
                    if not transaction_details or transaction_details.upper() in ['NAN', 'TRANSACTION_DETAILS', '']:
                        continue
                    
                    # Extract customer name from transaction details
                    customer_name = self._extract_customer_name(transaction_details)
                    
                    if not customer_name:
                        continue
                    
                    if customer_name not in all_customers:
                        all_customers[customer_name] = {
                            'monthly': {},
                            'transactions': [],
                            'total_mrc': 0
                        }
                    
                    # Add monthly MRC data (recurring payment only)
                    if sheet_name not in all_customers[customer_name]['monthly']:
                        all_customers[customer_name]['monthly'][sheet_name] = 0
                    all_customers[customer_name]['monthly'][sheet_name] += float(mrc) if mrc else 0
                    all_customers[customer_name]['total_mrc'] += float(mrc) if mrc else 0
                    
                    # Add transaction - NRC shows exactly as in document (only when actually paid)
                    all_customers[customer_name]['transactions'].append({
                        'date': date,
                        'month': sheet_name,
                        'amount': float(mrc) if mrc else 0,
                        'details': transaction_details,
                        'entity_number': entity_number,
                        'nrc': float(nrc) if nrc else 0  # Shows only in month it was actually paid
                    })
        
        return all_customers
    
    def _extract_customer_name(self, details):
        """Extract customer name from transaction details"""
        import re
        
        if not details:
            return None
        
        details = str(details).strip()
        
        # Pattern 1: "NIP FRM CUSTOMER_NAME-description"
        match = re.search(r'NIP FRM\s+([A-Z][A-Za-z\s]+?)(?:-|$)', details, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        # Pattern 2: "INWARD TRANSFER(H) From Bank/CUSTOMER_NAME/description"
        match = re.search(r'From\s+[A-Za-z]+/([A-Z][A-Za-z\s]+?)/', details, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        # Pattern 3: "TRF From CUSTOMER_NAME/App:"
        match = re.search(r'TRF From\s+([A-Z][A-Za-z\s]+?)/', details, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        # Pattern 4: "Mbanking Trf: REF;;CUSTOMER_NAME"
        match = re.search(r'Mbanking Trf:.*?;;([A-Z][A-Za-z\s]+)', details, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        # Pattern 5: Plain name (if it's all caps or title case and 2-5 words)
        words = details.split()
        if 1 <= len(words) <= 6:
            # Check if it looks like a name (starts with capital letters)
            if all(w[0].isupper() for w in words if w):
                return details.strip()
        
        return None
    
    def _clean_customer_name(self, name):
        """Clean and normalize customer name"""
        if not name:
            return None
        
        name = str(name).strip()
        
        # Remove extra whitespace
        name = re.sub(r'\s{2,}', ' ', name)
        
        # Title case if all uppercase
        if name.isupper() and len(name) > 3:
            name = name.title()
        
        # Limit length
        if len(name) > 80:
            name = name[:80]
        
        return name

@app.route('/mrc-analyzer')
def mrc_analyzer():
    """MRC Analyzer upload page"""
    return render_template('mrc_analyzer.html')

@app.route('/mrc-analyzer/upload', methods=['POST'])
def mrc_analyzer_upload():
    """Handle MRC file upload and processing"""
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('mrc_analyzer'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('mrc_analyzer'))
    
    if not allowed_file(file.filename):
        flash('Invalid file type. Please upload Excel files only.', 'error')
        return redirect(url_for('mrc_analyzer'))
    
    try:
        # Save file
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Process MRC file
        processor = MRCProcessor()
        customers_data = processor.process_mrc_file(filepath)
        
        if not customers_data:
            flash('No customer data found in file', 'warning')
            return redirect(url_for('mrc_analyzer'))
        
        # Store in session (or use a temporary storage mechanism)
        import json
        import uuid
        session_id = str(uuid.uuid4())
        
        # Save to temporary file for export later
        temp_file = os.path.join(app.config['UPLOAD_FOLDER'], f'mrc_analysis_{session_id}.json')
        with open(temp_file, 'w') as f:
            # Convert to JSON-serializable format
            json_data = {}
            for name, data in customers_data.items():
                json_data[name] = {
                    'monthly': data['monthly'],
                    'total_mrc': data['total_mrc'],
                    'transactions': data['transactions'],
                    'transaction_count': len(data['transactions'])
                }
            json.dump(json_data, f)
        
        # Prepare data for template
        customers_list = []
        for name, data in customers_data.items():
            customers_list.append({
                'name': name,
                'total_mrc': data['total_mrc'],
                'monthly': data['monthly'],
                'transaction_count': len(data['transactions'])
            })
        
        # Sort by total MRC descending
        customers_list.sort(key=lambda x: x['total_mrc'], reverse=True)
        
        # Calculate summary stats
        total_mrc = sum(c['total_mrc'] for c in customers_list)
        total_customers = len(customers_list)
        avg_mrc = total_mrc / total_customers if total_customers > 0 else 0
        total_transactions = sum(c['transaction_count'] for c in customers_list)
        
        return render_template('mrc_results.html',
                             customers=customers_list,
                             total_mrc=total_mrc,
                             total_customers=total_customers,
                             avg_mrc=avg_mrc,
                             total_transactions=total_transactions,
                             period='January - September 2025',
                             session_id=session_id)
    
    except Exception as e:
        flash(f'Error processing file: {str(e)}', 'error')
        print(f"MRC Error: {e}")
        import traceback
        traceback.print_exc()
        return redirect(url_for('mrc_analyzer'))

@app.route('/mrc-analyzer/results/<session_id>')
def mrc_analyzer_results(session_id):
    """View MRC analysis results from session"""
    try:
        import json
        
        # Load analysis data
        temp_file = os.path.join(app.config['UPLOAD_FOLDER'], f'mrc_analysis_{session_id}.json')
        if not os.path.exists(temp_file):
            flash('Analysis session expired. Please upload the file again.', 'error')
            return redirect(url_for('mrc_analyzer'))
        
        with open(temp_file, 'r') as f:
            customers_data = json.load(f)
        
        # Prepare data for template
        customers_list = []
        for name, data in customers_data.items():
            customers_list.append({
                'name': name,
                'total_mrc': data['total_mrc'],
                'monthly': data['monthly'],
                'transaction_count': data['transaction_count']
            })
        
        # Sort by total MRC descending
        customers_list.sort(key=lambda x: x['total_mrc'], reverse=True)
        
        # Calculate summary stats
        total_mrc = sum(c['total_mrc'] for c in customers_list)
        total_customers = len(customers_list)
        avg_mrc = total_mrc / total_customers if total_customers > 0 else 0
        total_transactions = sum(c['transaction_count'] for c in customers_list)
        
        return render_template('mrc_results.html',
                             customers=customers_list,
                             total_mrc=total_mrc,
                             total_customers=total_customers,
                             avg_mrc=avg_mrc,
                             total_transactions=total_transactions,
                             period='January - September 2025',
                             session_id=session_id)
    except Exception as e:
        flash(f'Error loading results: {str(e)}', 'error')
        return redirect(url_for('mrc_analyzer'))

@app.route('/mrc-analyzer/customer/<session_id>/<path:customer_name>')
def mrc_customer_detail(session_id, customer_name):
    """View individual customer MRC breakdown"""
    try:
        import json
        from urllib.parse import unquote
        
        customer_name = unquote(customer_name)
        
        # Load analysis data
        temp_file = os.path.join(app.config['UPLOAD_FOLDER'], f'mrc_analysis_{session_id}.json')
        if not os.path.exists(temp_file):
            flash('Analysis session expired. Please upload the file again.', 'error')
            return redirect(url_for('mrc_analyzer'))
        
        with open(temp_file, 'r') as f:
            customers_data = json.load(f)
        
        if customer_name not in customers_data:
            flash('Customer not found', 'error')
            return redirect(url_for('mrc_analyzer_results', session_id=session_id))
        
        customer_data = customers_data[customer_name]
        
        # Get initials
        name_parts = customer_name.split()
        initials = ''.join([p[0] for p in name_parts[:2] if p]).upper()
        
        # Calculate stats
        total_mrc = customer_data['total_mrc']
        transaction_count = customer_data['transaction_count']
        avg_amount = total_mrc / transaction_count if transaction_count > 0 else 0
        active_months = len([m for m, amt in customer_data['monthly'].items() if amt > 0])
        
        # Calculate total NRC from transactions (sum of all NRC payments)
        total_nrc = sum(txn.get('nrc', 0) for txn in customer_data['transactions'])
        
        return render_template('mrc_customer_detail.html',
                             customer_name=customer_name,
                             initials=initials,
                             total_mrc=total_mrc,
                             total_nrc=total_nrc,
                             transaction_count=transaction_count,
                             avg_amount=avg_amount,
                             active_months=active_months,
                             monthly=customer_data['monthly'],
                             transactions=customer_data['transactions'],
                             session_id=session_id)
    except Exception as e:
        flash(f'Error loading customer details: {str(e)}', 'error')
        print(f"Customer Detail Error: {e}")
        import traceback
        traceback.print_exc()
        return redirect(url_for('mrc_analyzer'))

@app.route('/api/forecast')
def api_forecast():
    """Get revenue forecasts"""
    days_ahead = request.args.get('days', '30')
    try:
        days_ahead = int(days_ahead)
    except:
        days_ahead = 30
    
    # Get daily revenue data
    daily_data = db.session.query(
        db.func.date(Transaction.date).label('date'),
        db.func.sum(Transaction.amount).label('amount'),
        db.func.count(Transaction.id).label('count')
    ).group_by(
        db.func.date(Transaction.date)
    ).order_by('date').all()
    
    # Convert to format expected by forecaster
    daily_list = [{
        'date': str(d[0]),
        'amount': float(d[1]),
        'count': d[2]
    } for d in daily_data]
    
    # Generate forecasts
    forecaster = RevenueForecaster()
    forecasts = forecaster.generate_all_forecasts(daily_list, days_ahead)
    
    return jsonify(forecasts)

@app.route('/forecasting')
def forecasting():
    """Revenue forecasting page"""
    return render_template('forecasting.html')

@app.route('/advanced-kpis')
def advanced_kpis():
    """Advanced KPIs dashboard page"""
    return render_template('advanced_kpis.html')

@app.route('/reports/automated')
def automated_reports():
    """Automated reports page"""
    return render_template('automated_reports.html')

@app.route('/advanced-analytics')
def advanced_analytics():
    """Advanced analytics dashboard page"""
    return render_template('advanced_analytics.html')

@app.route('/api/generate-report')
def api_generate_report():
    """Generate automated PDF report"""
    report_type = request.args.get('type', 'monthly')
    month = request.args.get('month', '')
    year = request.args.get('year', '')
    
    try:
        # Generate report based on type
        if report_type == 'monthly':
            report_data = generate_monthly_report(month, year)
        elif report_type == 'quarterly':
            report_data = generate_quarterly_report(month, year)
        elif report_type == 'annual':
            report_data = generate_annual_report(year)
        else:
            return jsonify({'error': 'Invalid report type'})
        
        return jsonify(report_data)
    except Exception as e:
        return jsonify({'error': str(e)})

def generate_monthly_report(month, year):
    """Generate monthly report data"""
    if not month or not year:
        # Use current month/year
        now = datetime.now()
        month = now.month
        year = now.year
    
    # Get transactions for the month
    start_date = datetime(int(year), int(month), 1)
    if int(month) == 12:
        end_date = datetime(int(year) + 1, 1, 1)
    else:
        end_date = datetime(int(year), int(month) + 1, 1)
    
    transactions = Transaction.query.filter(
        Transaction.date >= start_date,
        Transaction.date < end_date
    ).all()
    
    if not transactions:
        return {'error': 'No data found for the specified month'}
    
    # Calculate metrics
    total_revenue = sum(t.amount for t in transactions)
    total_transactions = len(transactions)
    unique_customers = len(set(t.customer_name for t in transactions))
    
    # Bank breakdown
    bank_data = {}
    for t in transactions:
        bank = t.bank or 'Unknown'
        if bank not in bank_data:
            bank_data[bank] = {'revenue': 0, 'count': 0}
        bank_data[bank]['revenue'] += t.amount
        bank_data[bank]['count'] += 1
    
    # Channel breakdown
    channel_data = {}
    for t in transactions:
        channel = t.channel or 'Unknown'
        if channel not in channel_data:
            channel_data[channel] = {'revenue': 0, 'count': 0}
        channel_data[channel]['revenue'] += t.amount
        channel_data[channel]['count'] += 1
    
    # Top customers
    customer_revenue = {}
    for t in transactions:
        customer = t.customer_name or 'Unknown'
        if customer not in customer_revenue:
            customer_revenue[customer] = 0
        customer_revenue[customer] += t.amount
    
    top_customers = sorted(customer_revenue.items(), key=lambda x: x[1], reverse=True)[:10]
    
    return {
        'type': 'monthly',
        'period': f"{month:02d}/{year}",
        'summary': {
            'total_revenue': total_revenue,
            'total_transactions': total_transactions,
            'unique_customers': unique_customers,
            'avg_transaction': total_revenue / total_transactions if total_transactions > 0 else 0,
            'arpu': total_revenue / unique_customers if unique_customers > 0 else 0
        },
        'bank_breakdown': bank_data,
        'channel_breakdown': channel_data,
        'top_customers': top_customers,
        'transactions': [{
            'date': t.date.strftime('%Y-%m-%d'),
            'customer': t.customer_name,
            'amount': t.amount,
            'bank': t.bank,
            'channel': t.channel
        } for t in transactions]
    }

def generate_quarterly_report(quarter, year):
    """Generate quarterly report data"""
    if not quarter or not year:
        now = datetime.now()
        quarter = (now.month - 1) // 3 + 1
        year = now.year
    
    quarter = int(quarter)
    year = int(year)
    
    # Calculate quarter date range
    start_month = (quarter - 1) * 3 + 1
    start_date = datetime(year, start_month, 1)
    
    if quarter == 4:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, start_month + 3, 1)
    
    transactions = Transaction.query.filter(
        Transaction.date >= start_date,
        Transaction.date < end_date
    ).all()
    
    if not transactions:
        return {'error': 'No data found for the specified quarter'}
    
    # Similar calculations as monthly report
    total_revenue = sum(t.amount for t in transactions)
    total_transactions = len(transactions)
    unique_customers = len(set(t.customer_name for t in transactions))
    
    # Monthly breakdown within quarter
    monthly_breakdown = {}
    for t in transactions:
        month_key = t.date.strftime('%Y-%m')
        if month_key not in monthly_breakdown:
            monthly_breakdown[month_key] = {'revenue': 0, 'count': 0}
        monthly_breakdown[month_key]['revenue'] += t.amount
        monthly_breakdown[month_key]['count'] += 1
    
    return {
        'type': 'quarterly',
        'period': f"Q{quarter}/{year}",
        'summary': {
            'total_revenue': total_revenue,
            'total_transactions': total_transactions,
            'unique_customers': unique_customers,
            'avg_transaction': total_revenue / total_transactions if total_transactions > 0 else 0,
            'arpu': total_revenue / unique_customers if unique_customers > 0 else 0
        },
        'monthly_breakdown': monthly_breakdown,
        'transactions': [{
            'date': t.date.strftime('%Y-%m-%d'),
            'customer': t.customer_name,
            'amount': t.amount,
            'bank': t.bank,
            'channel': t.channel
        } for t in transactions]
    }

def generate_annual_report(year):
    """Generate annual report data"""
    if not year:
        year = datetime.now().year
    
    year = int(year)
    
    start_date = datetime(year, 1, 1)
    end_date = datetime(year + 1, 1, 1)
    
    transactions = Transaction.query.filter(
        Transaction.date >= start_date,
        Transaction.date < end_date
    ).all()
    
    if not transactions:
        return {'error': 'No data found for the specified year'}
    
    # Similar calculations as monthly report
    total_revenue = sum(t.amount for t in transactions)
    total_transactions = len(transactions)
    unique_customers = len(set(t.customer_name for t in transactions))
    
    # Monthly breakdown for the year
    monthly_breakdown = {}
    for t in transactions:
        month_key = t.date.strftime('%Y-%m')
        if month_key not in monthly_breakdown:
            monthly_breakdown[month_key] = {'revenue': 0, 'count': 0}
        monthly_breakdown[month_key]['revenue'] += t.amount
        monthly_breakdown[month_key]['count'] += 1
    
    return {
        'type': 'annual',
        'period': str(year),
        'summary': {
            'total_revenue': total_revenue,
            'total_transactions': total_transactions,
            'unique_customers': unique_customers,
            'avg_transaction': total_revenue / total_transactions if total_transactions > 0 else 0,
            'arpu': total_revenue / unique_customers if unique_customers > 0 else 0
        },
        'monthly_breakdown': monthly_breakdown,
        'transactions': [{
            'date': t.date.strftime('%Y-%m-%d'),
            'customer': t.customer_name,
            'amount': t.amount,
            'bank': t.bank,
            'channel': t.channel
        } for t in transactions]
    }

@app.route('/api/customer-analytics')
def api_customer_analytics():
    """Get customer analytics data"""
    # Get all transactions
    transactions = db.session.query(Transaction).all()
    
    # Convert to format expected by analytics service
    transaction_list = [{
        'customer_name': t.customer_name,
        'amount': float(t.amount),
        'date': t.date.strftime('%Y-%m-%d'),
        'bank': t.bank,
        'channel': t.channel
    } for t in transactions]
    
    # Calculate analytics
    analytics = CustomerAnalytics()
    customer_metrics = analytics.calculate_customer_metrics(transaction_list)
    segment_analysis = analytics.get_segment_analysis(customer_metrics)
    arpu_analysis = analytics.get_arpu_analysis(customer_metrics)
    
    # Get top customers
    top_by_clv = analytics.get_top_customers(customer_metrics, 'clv', 10)
    top_by_revenue = analytics.get_top_customers(customer_metrics, 'total_revenue', 10)
    top_by_frequency = analytics.get_top_customers(customer_metrics, 'transaction_frequency', 10)
    
    # Clean NaN values from the response
    def clean_nan_values(obj):
        if isinstance(obj, dict):
            return {k: clean_nan_values(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [clean_nan_values(item) for item in obj]
        elif isinstance(obj, float) and (obj != obj):  # Check for NaN
            return 0
        else:
            return obj
    
    response_data = {
        'customer_metrics': customer_metrics,
        'segment_analysis': segment_analysis,
        'arpu_analysis': arpu_analysis,
        'top_customers': {
            'by_clv': [{'customer': c[0], **c[1]} for c in top_by_clv],
            'by_revenue': [{'customer': c[0], **c[1]} for c in top_by_revenue],
            'by_frequency': [{'customer': c[0], **c[1]} for c in top_by_frequency]
        }
    }
    
    # Clean the response data
    cleaned_data = clean_nan_values(response_data)
    
    return jsonify(cleaned_data)

@app.route('/api/advanced-kpis')
def api_advanced_kpis():
    """Get advanced KPIs data"""
    # Get time filter
    days = request.args.get('days', '30')
    try:
        days = int(days)
    except:
        days = 30
    
    # Calculate filter date
    if days > 0:
        filter_date = datetime.now(timezone.utc) - timedelta(days=days)
        transactions = Transaction.query.filter(Transaction.date >= filter_date).all()
    else:
        transactions = Transaction.query.all()
    
    if not transactions:
        return jsonify({'error': 'No transactions found'})
    
    # Convert to DataFrame for analysis
    df = pd.DataFrame([{
        'amount': t.amount,
        'date': t.date,
        'bank': t.bank,
        'channel': t.channel,
        'customer_name': t.customer_name
    } for t in transactions])
    
    df['date'] = pd.to_datetime(df['date'])
    df['month'] = df['date'].dt.to_period('M')
    df['day_of_week'] = df['date'].dt.day_name()
    
    # Calculate KPIs
    kpis = {}
    
    # Revenue KPIs
    kpis['revenue'] = {
        'total': float(df['amount'].sum()),
        'avg_daily': float(df.groupby(df['date'].dt.date)['amount'].sum().mean()),
        'growth_rate': calculate_growth_rate(df, 'amount'),
        'monthly_breakdown': get_monthly_breakdown(df, 'amount')
    }
    
    # Transaction KPIs
    kpis['transactions'] = {
        'total_count': len(df),
        'avg_daily': float(df.groupby(df['date'].dt.date).size().mean()),
        'growth_rate': calculate_growth_rate(df, 'count'),
        'monthly_breakdown': get_monthly_breakdown(df, 'count')
    }
    
    # Customer KPIs
    unique_customers = df['customer_name'].nunique()
    kpis['customers'] = {
        'total_unique': unique_customers,
        'new_customers': calculate_new_customers(df),
        'retention_rate': calculate_retention_rate(df),
        'arpu': float(df['amount'].sum() / unique_customers) if unique_customers > 0 else 0
    }
    
    # Channel Performance
    kpis['channels'] = {}
    for channel in df['channel'].unique():
        if pd.isna(channel) or channel == '':
            continue
        channel_data = df[df['channel'] == channel]
        kpis['channels'][channel] = {
            'revenue': float(channel_data['amount'].sum()),
            'transactions': len(channel_data),
            'avg_transaction': float(channel_data['amount'].mean()),
            'revenue_share': float(channel_data['amount'].sum() / df['amount'].sum() * 100)
        }
    
    # Bank Performance
    kpis['banks'] = {}
    for bank in df['bank'].unique():
        if pd.isna(bank) or bank == '':
            continue
        bank_data = df[df['bank'] == bank]
        kpis['banks'][bank] = {
            'revenue': float(bank_data['amount'].sum()),
            'transactions': len(bank_data),
            'avg_transaction': float(bank_data['amount'].mean()),
            'revenue_share': float(bank_data['amount'].sum() / df['amount'].sum() * 100)
        }
    
    # Day of Week Analysis
    dow_analysis = df.groupby('day_of_week')['amount'].agg(['sum', 'count', 'mean']).reset_index()
    kpis['day_of_week'] = {
        'revenue': dow_analysis.set_index('day_of_week')['sum'].to_dict(),
        'transactions': dow_analysis.set_index('day_of_week')['count'].to_dict(),
        'avg_transaction': dow_analysis.set_index('day_of_week')['mean'].to_dict()
    }
    
    # Top Customers
    top_customers = df.groupby('customer_name')['amount'].agg(['sum', 'count']).reset_index()
    top_customers = top_customers.sort_values('sum', ascending=False).head(10)
    kpis['top_customers'] = {
        'by_revenue': top_customers[['customer_name', 'sum']].to_dict('records'),
        'by_transactions': top_customers.sort_values('count', ascending=False)[['customer_name', 'count']].head(10).to_dict('records')
    }
    
    return jsonify(kpis)

def calculate_growth_rate(df, metric):
    """Calculate month-over-month growth rate"""
    if metric == 'count':
        monthly_data = df.groupby(df['date'].dt.to_period('M')).size()
    else:
        monthly_data = df.groupby(df['date'].dt.to_period('M'))[metric].sum()
    
    if len(monthly_data) < 2:
        return 0
    
    latest = monthly_data.iloc[-1]
    previous = monthly_data.iloc[-2]
    
    if previous == 0:
        return 0
    
    return float((latest - previous) / previous * 100)

def get_monthly_breakdown(df, metric):
    """Get monthly breakdown of metric"""
    if metric == 'count':
        monthly_data = df.groupby(df['date'].dt.to_period('M')).size()
    else:
        monthly_data = df.groupby(df['date'].dt.to_period('M'))[metric].sum()
    
    return {str(month): float(value) for month, value in monthly_data.items()}

def calculate_new_customers(df):
    """Calculate new customers in the period"""
    if len(df) == 0:
        return 0
    
    # Get first transaction date for each customer
    first_transactions = df.groupby('customer_name')['date'].min()
    
    # Count customers whose first transaction is in the current period
    period_start = df['date'].min()
    new_customers = (first_transactions >= period_start).sum()
    
    return int(new_customers)

def calculate_retention_rate(df):
    """Calculate customer retention rate"""
    if len(df) == 0:
        return 0
    
    # Get all unique customers
    all_customers = set(df['customer_name'].unique())
    
    if len(all_customers) == 0:
        return 0
    
    # Get customers who transacted in the last 30 days
    recent_date = df['date'].max() - timedelta(days=30)
    recent_customers = set(df[df['date'] >= recent_date]['customer_name'].unique())
    
    # Calculate retention rate
    retention_rate = len(recent_customers) / len(all_customers) * 100
    
    return float(retention_rate)

@app.route('/api/advanced-analytics')
def api_advanced_analytics():
    """Get comprehensive advanced analytics data"""
    # Get time filter
    days = request.args.get('days', '30')
    try:
        days = int(days)
    except:
        days = 30
    
    # Calculate filter date
    if days > 0:
        filter_date = datetime.now(timezone.utc) - timedelta(days=days)
        transactions = Transaction.query.filter(Transaction.date >= filter_date).all()
    else:
        transactions = Transaction.query.all()
    
    if not transactions:
        return jsonify({'error': 'No transactions found'})
    
    # Convert to format expected by analytics service
    transaction_list = [{
        'customer_name': t.customer_name,
        'amount': float(t.amount),
        'date': t.date.strftime('%Y-%m-%d'),
        'bank': t.bank,
        'channel': t.channel
    } for t in transactions]
    
    # Calculate advanced analytics
    analytics = AdvancedAnalytics()
    
    results = {
        'growth_rates': analytics.calculate_growth_rates(transaction_list),
        'seasonality': analytics.analyze_seasonality(transaction_list),
        'churn_analysis': analytics.analyze_churn(transaction_list),
        'anomaly_detection': analytics.detect_anomalies(transaction_list),
        'cash_flow': analytics.analyze_cash_flow(transaction_list),
        'cac_analysis': analytics.calculate_cac(transaction_list),
        'revenue_concentration': analytics.analyze_revenue_concentration(transaction_list)
    }
    
    return jsonify(results)

@app.route('/api/seasonality-analysis')
def api_seasonality_analysis():
    """Get detailed seasonality analysis"""
    transactions = Transaction.query.all()
    
    if not transactions:
        return jsonify({'error': 'No transactions found'})
    
    transaction_list = [{
        'customer_name': t.customer_name,
        'amount': float(t.amount),
        'date': t.date.strftime('%Y-%m-%d'),
        'bank': t.bank,
        'channel': t.channel
    } for t in transactions]
    
    analytics = AdvancedAnalytics()
    seasonality = analytics.analyze_seasonality(transaction_list)
    
    return jsonify(seasonality)

@app.route('/api/growth-analysis')
def api_growth_analysis():
    """Get growth rate analysis"""
    transactions = Transaction.query.all()
    
    if not transactions:
        return jsonify({'error': 'No transactions found'})
    
    transaction_list = [{
        'customer_name': t.customer_name,
        'amount': float(t.amount),
        'date': t.date.strftime('%Y-%m-%d'),
        'bank': t.bank,
        'channel': t.channel
    } for t in transactions]
    
    analytics = AdvancedAnalytics()
    growth_rates = analytics.calculate_growth_rates(transaction_list)
    
    return jsonify(growth_rates)

@app.route('/api/anomaly-detection')
def api_anomaly_detection():
    """Get anomaly detection results"""
    transactions = Transaction.query.all()
    
    if not transactions:
        return jsonify({'error': 'No transactions found'})
    
    transaction_list = [{
        'customer_name': t.customer_name,
        'amount': float(t.amount),
        'date': t.date.strftime('%Y-%m-%d'),
        'bank': t.bank,
        'channel': t.channel
    } for t in transactions]
    
    analytics = AdvancedAnalytics()
    anomalies = analytics.detect_anomalies(transaction_list)
    
    return jsonify(anomalies)

@app.route('/api/comparative-analysis')
def api_comparative_analysis():
    """Get comparative analysis (period-over-period)"""
    period_type = request.args.get('period', 'monthly')  # monthly, quarterly, yearly
    current_periods = int(request.args.get('current', '3'))  # number of current periods
    previous_periods = int(request.args.get('previous', '3'))  # number of previous periods
    
    # Get all transactions
    transactions = Transaction.query.all()
    
    if not transactions:
        return jsonify({'error': 'No transactions found'})
    
    # Convert to DataFrame for easier analysis
    df = pd.DataFrame([{
        'customer_name': t.customer_name,
        'amount': float(t.amount),
        'date': t.date,
        'bank': t.bank,
        'channel': t.channel
    } for t in transactions])
    
    df['date'] = pd.to_datetime(df['date'])
    
    # Group by period
    if period_type == 'monthly':
        df['period'] = df['date'].dt.to_period('M')
    elif period_type == 'quarterly':
        df['period'] = df['date'].dt.to_period('Q')
    elif period_type == 'yearly':
        df['period'] = df['date'].dt.to_period('Y')
    
    # Calculate period metrics
    period_metrics = df.groupby('period').agg({
        'amount': ['sum', 'count', 'mean'],
        'customer_name': 'nunique'
    }).round(2)
    
    period_metrics.columns = ['revenue', 'transactions', 'avg_transaction', 'unique_customers']
    period_metrics = period_metrics.sort_index()
    
    # Get current and previous periods
    periods = period_metrics.index.tolist()
    if len(periods) < current_periods + previous_periods:
        return jsonify({'error': 'Insufficient data for comparison'})
    
    current_periods_data = period_metrics.tail(current_periods)
    previous_periods_data = period_metrics.iloc[-(current_periods + previous_periods):-current_periods]
    
    # Calculate comparisons
    current_avg_revenue = current_periods_data['revenue'].mean()
    previous_avg_revenue = previous_periods_data['revenue'].mean()
    
    current_avg_transactions = current_periods_data['transactions'].mean()
    previous_avg_transactions = previous_periods_data['transactions'].mean()
    
    current_avg_customers = current_periods_data['unique_customers'].mean()
    previous_avg_customers = previous_periods_data['unique_customers'].mean()
    
    # Calculate percentage changes
    revenue_change = ((current_avg_revenue - previous_avg_revenue) / previous_avg_revenue * 100) if previous_avg_revenue > 0 else 0
    transaction_change = ((current_avg_transactions - previous_avg_transactions) / previous_avg_transactions * 100) if previous_avg_transactions > 0 else 0
    customer_change = ((current_avg_customers - previous_avg_customers) / previous_avg_customers * 100) if previous_avg_customers > 0 else 0
    
    # Bank-wise comparison
    bank_comparison = {}
    for bank in df['bank'].unique():
        bank_data = df[df['bank'] == bank]
        bank_periods = bank_data.groupby('period')['amount'].sum()
        
        if len(bank_periods) >= current_periods + previous_periods:
            current_bank_revenue = bank_periods.tail(current_periods).mean()
            previous_bank_revenue = bank_periods.iloc[-(current_periods + previous_periods):-current_periods].mean()
            bank_change = ((current_bank_revenue - previous_bank_revenue) / previous_bank_revenue * 100) if previous_bank_revenue > 0 else 0
            
            bank_comparison[bank] = {
                'current_revenue': float(current_bank_revenue),
                'previous_revenue': float(previous_bank_revenue),
                'change_percent': float(bank_change)
            }
    
    return jsonify({
        'period_type': period_type,
        'current_periods': current_periods,
        'previous_periods': previous_periods,
        'summary': {
            'revenue': {
                'current': float(current_avg_revenue),
                'previous': float(previous_avg_revenue),
                'change_percent': float(revenue_change),
                'change_amount': float(current_avg_revenue - previous_avg_revenue)
            },
            'transactions': {
                'current': float(current_avg_transactions),
                'previous': float(previous_avg_transactions),
                'change_percent': float(transaction_change),
                'change_amount': float(current_avg_transactions - previous_avg_transactions)
            },
            'customers': {
                'current': float(current_avg_customers),
                'previous': float(previous_avg_customers),
                'change_percent': float(customer_change),
                'change_amount': float(current_avg_customers - previous_avg_customers)
            }
        },
        'bank_comparison': bank_comparison,
        'period_data': {
            'current': current_periods_data.to_dict('index'),
            'previous': previous_periods_data.to_dict('index')
        }
    })

@app.route('/mrc-analyzer/export/<session_id>')
def mrc_analyzer_export(session_id):
    """Export MRC analysis to Excel with multiple sheets"""
    try:
        import json
        import pandas as pd
        from io import BytesIO
        
        # Load analysis data
        temp_file = os.path.join(app.config['UPLOAD_FOLDER'], f'mrc_analysis_{session_id}.json')
        if not os.path.exists(temp_file):
            flash('Analysis session expired. Please upload the file again.', 'error')
            return redirect(url_for('mrc_analyzer'))
        
        with open(temp_file, 'r') as f:
            customers_data = json.load(f)
        
        # Create Excel writer
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            
            # Sheet 1: SUMMARY
            summary_data = []
            for name, data in customers_data.items():
                row = {
                    'Customer Name': name,
                    'Total MRC': data['total_mrc'],
                    'January': data['monthly'].get('January', 0),
                    'February': data['monthly'].get('February', 0),
                    'March': data['monthly'].get('March', 0),
                    'April': data['monthly'].get('April', 0),
                    'May': data['monthly'].get('May', 0),
                    'June': data['monthly'].get('June', 0),
                    'July': data['monthly'].get('July', 0),
                    'August': data['monthly'].get('August', 0),
                    'September': data['monthly'].get('September', 0),
                    'Transaction Count': data['transaction_count']
                }
                summary_data.append(row)
            
            df_summary = pd.DataFrame(summary_data)
            df_summary = df_summary.sort_values('Total MRC', ascending=False)
            df_summary.to_excel(writer, sheet_name='SUMMARY', index=False)
            
            # Sheets 2-N: Individual customer sheets
            for name, data in customers_data.items():
                # Clean sheet name (Excel has 31 char limit and doesn't allow special chars)
                sheet_name = name[:31].replace('/', '-').replace('\\', '-').replace('*', '-').replace('?', '-').replace(':', '-').replace('[', '(').replace(']', ')')
                
                # Create customer transaction data
                customer_transactions = []
                for txn in data['transactions']:
                    customer_transactions.append({
                        'Date': txn['date'] if txn['date'] else 'N/A',
                        'Month': txn['month'],
                        'Amount (MRC)': txn['amount'],
                        'Transaction Details': txn['details'],
                        'Entity Number': txn['entity_number'] if txn['entity_number'] else 'N/A',
                        'NRC': txn['nrc']
                    })
                
                # Add total row
                customer_transactions.append({
                    'Date': '',
                    'Month': 'TOTAL',
                    'Amount (MRC)': data['total_mrc'],
                    'Transaction Details': '',
                    'Entity Number': '',
                    'NRC': ''
                })
                
                df_customer = pd.DataFrame(customer_transactions)
                df_customer.to_excel(writer, sheet_name=sheet_name, index=False)
        
        output.seek(0)
        
        # Note: We keep the temp file so users can navigate back to results
        # Temp files can be cleaned up manually or by a cleanup job
        
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=MRC_Analysis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return response
    
    except Exception as e:
        flash(f'Error exporting file: {str(e)}', 'error')
        print(f"Export Error: {e}")
        import traceback
        traceback.print_exc()
        return redirect(url_for('mrc_analyzer'))

@app.route('/mrc-analyzer/excel-cross-reference')
def mrc_excel_cross_reference():
    """Cross-reference customers from Excel file with transactions and generate detailed report"""
    return render_template('mrc_excel_cross_reference.html')

@app.route('/mrc-analyzer/excel-cross-reference/upload', methods=['POST'])
def mrc_excel_cross_reference_upload():
    """Process Excel file for cross-reference analysis"""
    try:
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(url_for('mrc_excel_cross_reference'))
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('mrc_excel_cross_reference'))
        
        if file and allowed_file(file.filename):
            # Save file
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Process the Excel file
            processor = MRCExcelCrossReferenceProcessor()
            result = processor.process_file(filepath)
            
            if result['success']:
                # Save results to session file
                session_id = str(uuid.uuid4())
                result_file = os.path.join(app.config['UPLOAD_FOLDER'], f'excel_cross_ref_{session_id}.json')
                with open(result_file, 'w') as f:
                    json.dump(result['data'], f)
                
                return redirect(url_for('mrc_excel_cross_reference_results', session_id=session_id))
            else:
                flash(f'Error processing file: {result["error"]}', 'error')
                return redirect(url_for('mrc_excel_cross_reference'))
        else:
            flash('Invalid file type. Please upload an Excel file.', 'error')
            return redirect(url_for('mrc_excel_cross_reference'))
    
    except Exception as e:
        flash(f'Error processing file: {str(e)}', 'error')
        print(f"Excel Cross-Reference Upload Error: {e}")
        import traceback
        traceback.print_exc()
        return redirect(url_for('mrc_excel_cross_reference'))

@app.route('/mrc-analyzer/excel-cross-reference/results/<session_id>')
def mrc_excel_cross_reference_results(session_id):
    """Display cross-reference results"""
    try:
        result_file = os.path.join(app.config['UPLOAD_FOLDER'], f'excel_cross_ref_{session_id}.json')
        if not os.path.exists(result_file):
            flash('Results not found. Please upload the file again.', 'error')
            return redirect(url_for('mrc_excel_cross_reference'))
        
        with open(result_file, 'r') as f:
            data = json.load(f)
        
        return render_template('mrc_excel_cross_reference_results.html', 
                             data=data, session_id=session_id)
    
    except Exception as e:
        flash(f'Error loading results: {str(e)}', 'error')
        return redirect(url_for('mrc_excel_cross_reference'))

@app.route('/mrc-analyzer/excel-cross-reference/export/<session_id>')
def mrc_excel_cross_reference_export(session_id):
    """Export cross-reference results to Excel"""
    try:
        import json
        import pandas as pd
        from io import BytesIO
        from datetime import datetime
        
        # Load results
        result_file = os.path.join(app.config['UPLOAD_FOLDER'], f'excel_cross_ref_{session_id}.json')
        if not os.path.exists(result_file):
            flash('Results not found. Please upload the file again.', 'error')
            return redirect(url_for('mrc_excel_cross_reference'))
        
        with open(result_file, 'r') as f:
            data = json.load(f)
        
        # Create Excel file
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Summary sheet
            summary_data = []
            total_mrc = 0
            total_nrc = 0
            
            for customer, info in data['customers'].items():
                summary_data.append({
                    'Customer Name': customer,
                    'Total MRC': info['total_mrc'],
                    'Total NRC': info['total_nrc'],
                    'Transaction Count': info['transaction_count'],
                    'Jan': info['monthly_breakdown'].get('January', 0),
                    'Feb': info['monthly_breakdown'].get('February', 0),
                    'Mar': info['monthly_breakdown'].get('March', 0),
                    'Apr': info['monthly_breakdown'].get('April', 0),
                    'May': info['monthly_breakdown'].get('May', 0),
                    'Jun': info['monthly_breakdown'].get('June', 0),
                    'Jul': info['monthly_breakdown'].get('July', 0),
                    'Aug': info['monthly_breakdown'].get('August', 0),
                    'Sep': info['monthly_breakdown'].get('September', 0),
                    'Oct': info['monthly_breakdown'].get('October', 0),
                    'Nov': info['monthly_breakdown'].get('November', 0),
                    'Dec': info['monthly_breakdown'].get('December', 0)
                })
                total_mrc += info['total_mrc']
                total_nrc += info['total_nrc']
            
            # Add totals row
            summary_data.append({
                'Customer Name': 'TOTAL',
                'Total MRC': total_mrc,
                'Total NRC': total_nrc,
                'Transaction Count': sum(row['Transaction Count'] for row in summary_data[:-1]),
                'Jan': sum(row['Jan'] for row in summary_data[:-1]),
                'Feb': sum(row['Feb'] for row in summary_data[:-1]),
                'Mar': sum(row['Mar'] for row in summary_data[:-1]),
                'Apr': sum(row['Apr'] for row in summary_data[:-1]),
                'May': sum(row['May'] for row in summary_data[:-1]),
                'Jun': sum(row['Jun'] for row in summary_data[:-1]),
                'Jul': sum(row['Jul'] for row in summary_data[:-1]),
                'Aug': sum(row['Aug'] for row in summary_data[:-1]),
                'Sep': sum(row['Sep'] for row in summary_data[:-1]),
                'Oct': sum(row['Oct'] for row in summary_data[:-1]),
                'Nov': sum(row['Nov'] for row in summary_data[:-1]),
                'Dec': sum(row['Dec'] for row in summary_data[:-1])
            })
            
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_excel(writer, sheet_name='SUMMARY', index=False)
            
            # Individual customer sheets
            for customer, info in data['customers'].items():
                customer_data = []
                for transaction in info['transactions']:
                    customer_data.append({
                        'Date': transaction['date'],
                        'Month': transaction['month'],
                        'Amount': transaction['amount'],
                        'Type': transaction['type'],
                        'Transaction Details': transaction['details'],
                        'Bank': transaction.get('bank', ''),
                        'Reference': transaction.get('reference', '')
                    })
                
                customer_df = pd.DataFrame(customer_data)
                sheet_name = customer[:31]  # Excel sheet name limit
                customer_df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=MRC_Excel_Cross_Reference_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return response
        
    except Exception as e:
        flash(f'Error exporting file: {str(e)}', 'error')
        print(f"Excel Cross-Reference Export Error: {e}")
        import traceback
        traceback.print_exc()
        return redirect(url_for('mrc_excel_cross_reference'))

@app.route('/mrc-analyzer/cross-reference/<session_id>')
def mrc_cross_reference(session_id):
    """Cross-reference MRC customers with existing database and generate Excel report"""
    try:
        import json
        import pandas as pd
        from io import BytesIO
        from datetime import datetime
        
        # Load MRC data
        temp_file = os.path.join(app.config['UPLOAD_FOLDER'], f'mrc_analysis_{session_id}.json')
        if not os.path.exists(temp_file):
            flash('Analysis session expired. Please upload the file again.', 'error')
            return redirect(url_for('mrc_analyzer'))
        
        with open(temp_file, 'r') as f:
            mrc_data = json.load(f)
        
        # Extract customer names from MRC data
        mrc_customers = list(mrc_data.keys())
        
        # Query database for matching customers (Jan-Sep 2025)
        start_date = datetime(2025, 1, 1)
        end_date = datetime(2025, 9, 30)
        
        matching_transactions = db.session.query(Transaction).filter(
            Transaction.customer_name.in_(mrc_customers),
            Transaction.date >= start_date,
            Transaction.date <= end_date
        ).order_by(Transaction.customer_name, Transaction.date).all()
        
        # Group transactions by customer
        customer_transactions = {}
        for transaction in matching_transactions:
            customer = transaction.customer_name
            if customer not in customer_transactions:
                customer_transactions[customer] = []
            
            customer_transactions[customer].append({
                'date': transaction.date.strftime('%Y-%m-%d'),
                'time': transaction.date.strftime('%H:%M:%S') if hasattr(transaction.date, 'time') else '',
                'amount': float(transaction.amount),
                'bank': transaction.bank,
                'channel': transaction.channel,
                'details': transaction.description or '',
                'month': transaction.date.strftime('%B')
            })
        
        # Create Excel file
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Summary sheet
            summary_data = []
            grand_total = 0
            
            for customer in mrc_customers:
                if customer in customer_transactions:
                    transactions = customer_transactions[customer]
                    total_amount = sum(t['amount'] for t in transactions)
                    grand_total += total_amount
                    
                    # Calculate monthly breakdown
                    monthly_breakdown = {}
                    for transaction in transactions:
                        month = transaction['month']
                        if month not in monthly_breakdown:
                            monthly_breakdown[month] = 0
                        monthly_breakdown[month] += transaction['amount']
                    
                    summary_data.append({
                        'Customer Name': customer,
                        'Total Amount': total_amount,
                        'Transaction Count': len(transactions),
                        'Jan': monthly_breakdown.get('January', 0),
                        'Feb': monthly_breakdown.get('February', 0),
                        'Mar': monthly_breakdown.get('March', 0),
                        'Apr': monthly_breakdown.get('April', 0),
                        'May': monthly_breakdown.get('May', 0),
                        'Jun': monthly_breakdown.get('June', 0),
                        'Jul': monthly_breakdown.get('July', 0),
                        'Aug': monthly_breakdown.get('August', 0),
                        'Sep': monthly_breakdown.get('September', 0),
                        'Banks': ', '.join(set(t['bank'] for t in transactions))
                    })
                else:
                    summary_data.append({
                        'Customer Name': customer,
                        'Total Amount': 0,
                        'Transaction Count': 0,
                        'Jan': 0, 'Feb': 0, 'Mar': 0, 'Apr': 0, 'May': 0,
                        'Jun': 0, 'Jul': 0, 'Aug': 0, 'Sep': 0,
                        'Banks': 'No transactions found'
                    })
            
            # Add grand total row
            summary_data.append({
                'Customer Name': 'GRAND TOTAL',
                'Total Amount': grand_total,
                'Transaction Count': sum(len(customer_transactions.get(c, [])) for c in mrc_customers),
                'Jan': sum(row['Jan'] for row in summary_data[:-1]),
                'Feb': sum(row['Feb'] for row in summary_data[:-1]),
                'Mar': sum(row['Mar'] for row in summary_data[:-1]),
                'Apr': sum(row['Apr'] for row in summary_data[:-1]),
                'May': sum(row['May'] for row in summary_data[:-1]),
                'Jun': sum(row['Jun'] for row in summary_data[:-1]),
                'Jul': sum(row['Jul'] for row in summary_data[:-1]),
                'Aug': sum(row['Aug'] for row in summary_data[:-1]),
                'Sep': sum(row['Sep'] for row in summary_data[:-1]),
                'Banks': 'All'
            })
            
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_excel(writer, sheet_name='CROSS_REFERENCE_SUMMARY', index=False)
            
            # Individual customer sheets
            for customer in mrc_customers:
                if customer in customer_transactions:
                    transactions = customer_transactions[customer]
                    customer_data = []
                    
                    for transaction in transactions:
                        customer_data.append({
                            'Date': transaction['date'],
                            'Time': transaction['time'],
                            'Month': transaction['month'],
                            'Amount': transaction['amount'],
                            'Bank': transaction['bank'],
                            'Channel': transaction['channel'],
                            'Details': transaction['details']
                        })
                    
                    customer_df = pd.DataFrame(customer_data)
                    sheet_name = customer[:31]  # Excel sheet name limit
                    customer_df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=MRC_Cross_Reference_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return response
        
    except Exception as e:
        flash(f'Error generating cross-reference report: {str(e)}', 'error')
        print(f"Cross-reference Error: {e}")
        import traceback
        traceback.print_exc()
        return redirect(url_for('mrc_analyzer'))

# ==================== DATABASE INITIALIZATION ====================

    with app.app_context():
        db.create_all()
print("Database initialized successfully")

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
