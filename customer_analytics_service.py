"""
Customer Analytics Service
Provides customer segmentation, CLV calculation, and behavioral analysis
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

class CustomerAnalytics:
    def __init__(self):
        pass
    
    def calculate_customer_metrics(self, transactions):
        """Calculate comprehensive customer metrics"""
        if not transactions:
            return {}
        
        # Convert to DataFrame for easier analysis
        df = pd.DataFrame(transactions)
        df['date'] = pd.to_datetime(df['date'])
        
        # Group by customer
        customer_metrics = {}
        
        for customer, group in df.groupby('customer_name'):
            if not customer or customer.strip() == '':
                continue
                
            # Basic metrics
            total_revenue = group['amount'].sum()
            transaction_count = len(group)
            avg_transaction = total_revenue / transaction_count if transaction_count > 0 else 0
            
            # Date metrics
            first_transaction = group['date'].min()
            last_transaction = group['date'].max()
            days_active = (last_transaction - first_transaction).days + 1
            transaction_frequency = transaction_count / max(days_active, 1) * 30  # per month
            
            # Recency (days since last transaction)
            recency = (datetime.now() - last_transaction).days
            
            # Customer Lifetime Value (CLV)
            # Simple CLV = Average Transaction Value × Purchase Frequency × Customer Lifespan
            customer_lifespan_months = days_active / 30
            clv = avg_transaction * (transaction_frequency / 30) * customer_lifespan_months
            
            # RFM Analysis
            rfm_score = self.calculate_rfm_score(recency, transaction_frequency, total_revenue)
            
            # Payment patterns
            payment_patterns = self.analyze_payment_patterns(group)
            
            # Bank preferences
            bank_preferences = group['bank'].value_counts().to_dict()
            preferred_bank = max(bank_preferences.items(), key=lambda x: x[1])[0] if bank_preferences else 'Unknown'
            
            customer_metrics[customer] = {
                'total_revenue': float(total_revenue),
                'transaction_count': int(transaction_count),
                'avg_transaction': float(avg_transaction),
                'first_transaction': first_transaction.strftime('%Y-%m-%d'),
                'last_transaction': last_transaction.strftime('%Y-%m-%d'),
                'days_active': int(days_active),
                'transaction_frequency': float(transaction_frequency),
                'recency': int(recency),
                'clv': float(clv),
                'rfm_score': rfm_score,
                'preferred_bank': preferred_bank,
                'bank_preferences': bank_preferences,
                'payment_patterns': payment_patterns,
                'customer_segment': self.determine_customer_segment(clv, transaction_frequency, recency)
            }
        
        return customer_metrics
    
    def calculate_rfm_score(self, recency, frequency, monetary):
        """Calculate RFM (Recency, Frequency, Monetary) score"""
        # Normalize scores (1-5 scale)
        r_score = max(1, min(5, 6 - (recency // 30)))  # Lower recency = higher score
        f_score = max(1, min(5, frequency // 2))  # Higher frequency = higher score
        m_score = max(1, min(5, monetary // 100000))  # Higher monetary = higher score
        
        return {
            'recency': int(r_score),
            'frequency': int(f_score),
            'monetary': int(m_score),
            'combined': int(r_score * 100 + f_score * 10 + m_score)
        }
    
    def analyze_payment_patterns(self, customer_transactions):
        """Analyze customer payment patterns"""
        patterns = {
            'monthly_consistency': 0,
            'preferred_days': [],
            'amount_consistency': 0,
            'growth_trend': 'stable'
        }
        
        if len(customer_transactions) < 2:
            return patterns
        
        # Monthly consistency (transactions per month)
        monthly_counts = customer_transactions.groupby(
            customer_transactions['date'].dt.to_period('M')
        ).size()
        monthly_consistency = 0
        if monthly_counts.mean() > 0:
            consistency = monthly_counts.std() / monthly_counts.mean()
            monthly_consistency = float(consistency) if not pd.isna(consistency) else 0
        patterns['monthly_consistency'] = monthly_consistency
        
        # Preferred days of week
        day_counts = customer_transactions['date'].dt.day_name().value_counts()
        patterns['preferred_days'] = day_counts.head(3).index.tolist()
        
        # Amount consistency (coefficient of variation)
        amounts = customer_transactions['amount']
        amount_consistency = 0
        if amounts.mean() > 0:
            consistency = amounts.std() / amounts.mean()
            amount_consistency = float(consistency) if not pd.isna(consistency) else 0
        patterns['amount_consistency'] = amount_consistency
        
        # Growth trend
        if len(customer_transactions) >= 3:
            monthly_revenue = customer_transactions.groupby(
                customer_transactions['date'].dt.to_period('M')
            )['amount'].sum()
            
            if len(monthly_revenue) >= 2:
                first_half = monthly_revenue[:len(monthly_revenue)//2].mean()
                second_half = monthly_revenue[len(monthly_revenue)//2:].mean()
                
                if second_half > first_half * 1.1:
                    patterns['growth_trend'] = 'growing'
                elif second_half < first_half * 0.9:
                    patterns['growth_trend'] = 'declining'
                else:
                    patterns['growth_trend'] = 'stable'
        
        return patterns
    
    def determine_customer_segment(self, clv, frequency, recency):
        """Determine customer segment based on CLV, frequency, and recency"""
        if clv > 1000000 and frequency > 4 and recency < 30:
            return 'Champions'
        elif clv > 500000 and frequency > 2 and recency < 60:
            return 'Loyal Customers'
        elif clv > 200000 and frequency > 1 and recency < 90:
            return 'Potential Loyalists'
        elif clv > 100000 and frequency > 1:
            return 'New Customers'
        elif recency > 180:
            return 'At Risk'
        elif clv < 50000 and frequency < 1:
            return 'Need Attention'
        else:
            return 'Regular'
    
    def get_segment_analysis(self, customer_metrics):
        """Get analysis by customer segments"""
        segments = defaultdict(list)
        
        for customer, metrics in customer_metrics.items():
            segment = metrics['customer_segment']
            segments[segment].append({
                'customer': customer,
                'clv': metrics['clv'],
                'revenue': metrics['total_revenue'],
                'transactions': metrics['transaction_count'],
                'frequency': metrics['transaction_frequency']
            })
        
        segment_analysis = {}
        for segment, customers in segments.items():
            if not customers:
                continue
                
            total_revenue = sum(c['revenue'] for c in customers)
            total_transactions = sum(c['transactions'] for c in customers)
            avg_clv = np.mean([c['clv'] for c in customers])
            avg_frequency = np.mean([c['frequency'] for c in customers])
            
            segment_analysis[segment] = {
                'customer_count': len(customers),
                'total_revenue': total_revenue,
                'total_transactions': total_transactions,
                'avg_clv': avg_clv,
                'avg_frequency': avg_frequency,
                'revenue_share': 0,  # Will be calculated later
                'customers': customers
            }
        
        # Calculate revenue share
        total_revenue_all = sum(s['total_revenue'] for s in segment_analysis.values())
        for segment in segment_analysis:
            segment_analysis[segment]['revenue_share'] = (
                segment_analysis[segment]['total_revenue'] / total_revenue_all * 100
                if total_revenue_all > 0 else 0
            )
        
        return segment_analysis
    
    def get_top_customers(self, customer_metrics, metric='clv', limit=10):
        """Get top customers by specified metric"""
        sorted_customers = sorted(
            customer_metrics.items(),
            key=lambda x: x[1][metric],
            reverse=True
        )
        
        return sorted_customers[:limit]
    
    def get_arpu_analysis(self, customer_metrics):
        """Calculate Average Revenue Per User (ARPU) metrics"""
        if not customer_metrics:
            return {}
        
        revenues = [m['total_revenue'] for m in customer_metrics.values()]
        transactions = [m['transaction_count'] for m in customer_metrics.values()]
        
        return {
            'total_customers': len(customer_metrics),
            'total_revenue': sum(revenues),
            'arpu': sum(revenues) / len(customer_metrics),
            'avg_transactions_per_customer': sum(transactions) / len(customer_metrics),
            'revenue_distribution': {
                'min': min(revenues),
                'max': max(revenues),
                'median': np.median(revenues),
                'q75': np.percentile(revenues, 75),
                'q25': np.percentile(revenues, 25)
            }
        }
