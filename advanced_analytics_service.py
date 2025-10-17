"""
Advanced Analytics Service
Provides comprehensive financial analytics including seasonality, growth rates, churn analysis, and anomaly detection
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import warnings
warnings.filterwarnings('ignore')

class AdvancedAnalytics:
    def __init__(self):
        pass
    
    def calculate_growth_rates(self, transactions):
        """Calculate MoM, YoY, and CAGR growth rates"""
        if not transactions:
            return {}
        
        try:
            df = pd.DataFrame(transactions)
            df['date'] = pd.to_datetime(df['date'])
            df['month'] = df['date'].dt.to_period('M')
            df['year'] = df['date'].dt.year
            
            # Monthly revenue
            monthly_revenue = df.groupby('month')['amount'].sum()
            
            # Yearly revenue
            yearly_revenue = df.groupby('year')['amount'].sum()
            
            growth_rates = {}
            
            # Month-over-Month growth
            if len(monthly_revenue) >= 2:
                mom_growth = []
                for i in range(1, len(monthly_revenue)):
                    current = monthly_revenue.iloc[i]
                    previous = monthly_revenue.iloc[i-1]
                    if previous > 0:
                        growth = ((current - previous) / previous) * 100
                        mom_growth.append(growth)
                
                if mom_growth:
                    growth_rates['mom'] = {
                        'current': mom_growth[-1] if mom_growth else 0,
                        'average': np.mean(mom_growth) if mom_growth else 0,
                        'trend': 'increasing' if len(mom_growth) > 6 and np.mean(mom_growth[-3:]) > np.mean(mom_growth[:-3]) else 'decreasing' if len(mom_growth) > 6 else 'stable'
                    }
            
            # Year-over-Year growth
            if len(yearly_revenue) >= 2:
                yoy_growth = []
                years = sorted(yearly_revenue.index)
                for i in range(1, len(years)):
                    current = yearly_revenue[years[i]]
                    previous = yearly_revenue[years[i-1]]
                    if previous > 0:
                        growth = ((current - previous) / previous) * 100
                        yoy_growth.append(growth)
                
                if yoy_growth:
                    growth_rates['yoy'] = {
                        'current': yoy_growth[-1] if yoy_growth else 0,
                        'average': np.mean(yoy_growth) if yoy_growth else 0
                    }
            
            # CAGR (Compound Annual Growth Rate)
            if len(yearly_revenue) >= 2:
                years = sorted(yearly_revenue.index)
                first_year_revenue = yearly_revenue[years[0]]
                last_year_revenue = yearly_revenue[years[-1]]
                years_elapsed = years[-1] - years[0]
                
                if first_year_revenue > 0 and years_elapsed > 0:
                    cagr = ((last_year_revenue / first_year_revenue) ** (1 / years_elapsed)) - 1
                    growth_rates['cagr'] = cagr * 100
            
            return growth_rates
            
        except Exception as e:
            print(f"Error in calculate_growth_rates: {e}")
            return {}
    
    def analyze_seasonality(self, transactions):
        """Analyze monthly and quarterly seasonal patterns"""
        if not transactions:
            return {}
        
        try:
            df = pd.DataFrame(transactions)
            df['date'] = pd.to_datetime(df['date'])
            df['month'] = df['date'].dt.month
            df['quarter'] = df['date'].dt.quarter
            df['month_name'] = df['date'].dt.month_name()
            df['quarter_name'] = df['date'].dt.quarter.map({1: 'Q1', 2: 'Q2', 3: 'Q3', 4: 'Q4'})
            
            # Monthly analysis
            monthly_analysis = df.groupby('month').agg({
                'amount': ['sum', 'count', 'mean'],
                'customer_name': 'nunique'
            }).round(2)
            
            monthly_analysis.columns = ['revenue', 'transactions', 'avg_transaction', 'unique_customers']
            monthly_analysis['month_name'] = monthly_analysis.index.map({
                1: 'January', 2: 'February', 3: 'March', 4: 'April',
                5: 'May', 6: 'June', 7: 'July', 8: 'August',
                9: 'September', 10: 'October', 11: 'November', 12: 'December'
            })
            
            # Quarterly analysis
            quarterly_analysis = df.groupby('quarter').agg({
                'amount': ['sum', 'count', 'mean'],
                'customer_name': 'nunique'
            }).round(2)
            
            quarterly_analysis.columns = ['revenue', 'transactions', 'avg_transaction', 'unique_customers']
            quarterly_analysis['quarter_name'] = quarterly_analysis.index.map({1: 'Q1', 2: 'Q2', 3: 'Q3', 4: 'Q4'})
            
            # Calculate seasonality index (average monthly performance vs overall average)
            monthly_revenue = monthly_analysis['revenue']
            overall_avg = monthly_revenue.mean()
            seasonality_index = (monthly_revenue / overall_avg * 100).round(1)
            
            return {
                'monthly': {int(k): v for k, v in monthly_analysis.to_dict('index').items()},
                'quarterly': {int(k): v for k, v in quarterly_analysis.to_dict('index').items()},
                'seasonality_index': {int(k): float(v) for k, v in seasonality_index.to_dict().items()},
                'peak_month': int(monthly_revenue.idxmax()),
                'low_month': int(monthly_revenue.idxmin()),
                'peak_quarter': int(quarterly_analysis['revenue'].idxmax()),
                'low_quarter': int(quarterly_analysis['revenue'].idxmin())
            }
            
        except Exception as e:
            print(f"Error in analyze_seasonality: {e}")
            return {}
    
    def analyze_churn(self, transactions):
        """Analyze customer churn and retention patterns"""
        if not transactions:
            return {}
        
        try:
            df = pd.DataFrame(transactions)
            df['date'] = pd.to_datetime(df['date'])
            
            # Group by customer
            customer_data = df.groupby('customer_name').agg({
                'date': ['min', 'max'],
                'amount': ['sum', 'count']
            }).round(2)
            
            customer_data.columns = ['first_transaction', 'last_transaction', 'total_revenue', 'transaction_count']
            
            # Calculate recency (days since last transaction)
            current_date = datetime.now()
            customer_data['recency'] = (current_date - customer_data['last_transaction']).dt.days
            
            # Define churn categories
            def categorize_churn(recency, transaction_count):
                if recency <= 30:
                    return 'Active'
                elif recency <= 90:
                    return 'At Risk'
                elif recency <= 180:
                    return 'Churned (Recent)'
                else:
                    return 'Churned (Long-term)'
            
            customer_data['churn_category'] = customer_data.apply(
                lambda x: categorize_churn(x['recency'], x['transaction_count']), axis=1
            )
            
            # Calculate retention metrics
            total_customers = len(customer_data)
            active_customers = len(customer_data[customer_data['churn_category'] == 'Active'])
            at_risk_customers = len(customer_data[customer_data['churn_category'] == 'At Risk'])
            churned_customers = len(customer_data[customer_data['churn_category'].str.contains('Churned')])
            
            retention_rate = (active_customers / total_customers * 100) if total_customers > 0 else 0
            churn_rate = (churned_customers / total_customers * 100) if total_customers > 0 else 0
            
            # Calculate cohort analysis (simplified)
            cohort_analysis = self.calculate_cohort_analysis(df)
            
            return {
                'total_customers': total_customers,
                'active_customers': active_customers,
                'at_risk_customers': at_risk_customers,
                'churned_customers': churned_customers,
                'retention_rate': retention_rate,
                'churn_rate': churn_rate,
                'customer_categories': customer_data['churn_category'].value_counts().to_dict(),
                'cohort_analysis': cohort_analysis,
                'avg_recency': customer_data['recency'].mean(),
                'median_recency': customer_data['recency'].median()
            }
            
        except Exception as e:
            print(f"Error in analyze_churn: {e}")
            return {}
    
    def calculate_cohort_analysis(self, df):
        """Calculate customer cohort retention analysis"""
        # Group by customer and get first transaction month
        customer_cohorts = df.groupby('customer_name')['date'].min().dt.to_period('M')
        
        # Create cohort table
        cohorts = {}
        for customer, first_month in customer_cohorts.items():
            if first_month not in cohorts:
                cohorts[first_month] = []
            cohorts[first_month].append(customer)
        
        # Calculate retention for each cohort
        cohort_retention = {}
        for cohort_month, customers in cohorts.items():
            retention_data = {}
            for period in range(12):  # 12 months
                target_month = cohort_month + period
                active_customers = 0
                
                for customer in customers:
                    customer_transactions = df[df['customer_name'] == customer]
                    if not customer_transactions.empty:
                        customer_months = customer_transactions['date'].dt.to_period('M')
                        if target_month in customer_months.values:
                            active_customers += 1
                
                retention_rate = (active_customers / len(customers) * 100) if customers else 0
                retention_data[f'period_{period}'] = retention_rate
            
            cohort_retention[str(cohort_month)] = retention_data
        
        return cohort_retention
    
    def detect_anomalies(self, transactions):
        """Detect unusual transactions using machine learning"""
        if not transactions or len(transactions) < 10:
            return {
                'total_transactions': len(transactions) if transactions else 0,
                'anomalous_transactions': 0,
                'anomaly_rate': 0,
                'anomalies': [],
                'high_risk_customers': {}
            }
        
        try:
            df = pd.DataFrame(transactions)
            df['date'] = pd.to_datetime(df['date'])
            
            # Prepare features for anomaly detection
            features = df[['amount']].copy()
            features['day_of_week'] = df['date'].dt.dayofweek
            features['hour'] = df['date'].dt.hour
            features['day_of_month'] = df['date'].dt.day
            
            # Add customer frequency (transactions per customer)
            customer_freq = df['customer_name'].value_counts()
            features['customer_frequency'] = df['customer_name'].map(customer_freq)
            
            # Handle any NaN values
            features = features.fillna(0)
            
            # Check if we have enough data and variance
            if len(features) < 10 or features['amount'].std() == 0:
                return {
                    'total_transactions': len(df),
                    'anomalous_transactions': 0,
                    'anomaly_rate': 0,
                    'anomalies': [],
                    'high_risk_customers': {}
                }
            
            # Standardize features
            scaler = StandardScaler()
            features_scaled = scaler.fit_transform(features)
            
            # Apply Isolation Forest with error handling
            try:
                iso_forest = IsolationForest(contamination=0.1, random_state=42)
                anomaly_labels = iso_forest.fit_predict(features_scaled)
                anomaly_scores = iso_forest.decision_function(features_scaled)
            except Exception as e:
                print(f"Isolation Forest error: {e}")
                # Fallback: simple statistical anomaly detection
                amount_mean = features['amount'].mean()
                amount_std = features['amount'].std()
                threshold = amount_mean + 2 * amount_std
                anomaly_labels = (features['amount'] > threshold).astype(int)
                anomaly_scores = features['amount'] - amount_mean
            
            # Add results to dataframe
            df['is_anomaly'] = anomaly_labels == -1
            df['anomaly_score'] = anomaly_scores
            
            # Get anomalous transactions
            anomalies = df[df['is_anomaly']].copy()
            
            return {
                'total_transactions': len(df),
                'anomalous_transactions': len(anomalies),
                'anomaly_rate': (len(anomalies) / len(df)) * 100,
                'anomalies': anomalies[['customer_name', 'amount', 'date', 'anomaly_score']].to_dict('records'),
                'high_risk_customers': anomalies['customer_name'].value_counts().head(10).to_dict()
            }
            
        except Exception as e:
            print(f"Error in detect_anomalies: {e}")
            return {
                'total_transactions': len(transactions) if transactions else 0,
                'anomalous_transactions': 0,
                'anomaly_rate': 0,
                'anomalies': [],
                'high_risk_customers': {}
            }
    
    def analyze_cash_flow(self, transactions):
        """Analyze cash flow patterns and trends"""
        if not transactions:
            return {}
        
        try:
            df = pd.DataFrame(transactions)
            df['date'] = pd.to_datetime(df['date'])
            df['month'] = df['date'].dt.to_period('M')
            
            # Daily cash flow
            daily_cashflow = df.groupby(df['date'].dt.date)['amount'].sum()
            
            # Monthly cash flow
            monthly_cashflow = df.groupby('month')['amount'].sum()
            
            # Calculate cash flow metrics
            avg_daily_cashflow = daily_cashflow.mean()
            avg_monthly_cashflow = monthly_cashflow.mean()
            
            # Cash flow volatility (standard deviation)
            daily_volatility = daily_cashflow.std()
            monthly_volatility = monthly_cashflow.std()
            
            # Cash flow trends
            daily_trend = np.polyfit(range(len(daily_cashflow)), daily_cashflow.values, 1)[0] if len(daily_cashflow) > 1 else 0
            monthly_trend = np.polyfit(range(len(monthly_cashflow)), monthly_cashflow.values, 1)[0] if len(monthly_cashflow) > 1 else 0
            
            # Best and worst performing days/months
            best_day = daily_cashflow.idxmax()
            worst_day = daily_cashflow.idxmin()
            best_month = monthly_cashflow.idxmax()
            worst_month = monthly_cashflow.idxmin()
            
            return {
                'daily_cashflow': {
                    'average': float(avg_daily_cashflow),
                    'volatility': float(daily_volatility),
                    'trend': float(daily_trend),
                    'best_day': str(best_day),
                    'worst_day': str(worst_day),
                    'best_day_amount': float(daily_cashflow[best_day]),
                    'worst_day_amount': float(daily_cashflow[worst_day])
                },
                'monthly_cashflow': {
                    'average': float(avg_monthly_cashflow),
                    'volatility': float(monthly_volatility),
                    'trend': float(monthly_trend),
                    'best_month': str(best_month),
                    'worst_month': str(worst_month),
                    'best_month_amount': float(monthly_cashflow[best_month]),
                    'worst_month_amount': float(monthly_cashflow[worst_month])
                },
                'cashflow_data': {
                    'daily': {str(k): float(v) for k, v in daily_cashflow.to_dict().items()},
                    'monthly': {str(k): float(v) for k, v in monthly_cashflow.to_dict().items()}
                }
            }
            
        except Exception as e:
            print(f"Error in analyze_cash_flow: {e}")
            return {}
    
    def calculate_cac(self, transactions, marketing_costs=None):
        """Calculate Customer Acquisition Cost (CAC)"""
        if not transactions:
            return {}
        
        try:
            df = pd.DataFrame(transactions)
            df['date'] = pd.to_datetime(df['date'])
            
            # Get unique customers by their first transaction
            first_transactions = df.groupby('customer_name')['date'].min()
            new_customers_by_month = first_transactions.dt.to_period('M').value_counts().sort_index()
            
            # If marketing costs provided, use them; otherwise estimate
            if marketing_costs:
                total_marketing_cost = sum(marketing_costs.values())
                total_new_customers = len(first_transactions)
                cac = total_marketing_cost / total_new_customers if total_new_customers > 0 else 0
            else:
                # Estimate CAC as 10% of average transaction value
                avg_transaction = df['amount'].mean()
                cac = avg_transaction * 0.1
            
            # Calculate CAC by month
            monthly_cac = {}
            for month, new_customers in new_customers_by_month.items():
                if marketing_costs and str(month) in marketing_costs:
                    monthly_cost = marketing_costs[str(month)]
                else:
                    monthly_cost = new_customers * cac
                monthly_cac[str(month)] = monthly_cost / new_customers if new_customers > 0 else 0
            
            return {
                'overall_cac': float(cac),
                'monthly_cac': monthly_cac,
                'total_new_customers': len(first_transactions),
                'estimated_total_cost': float(cac * len(first_transactions))
            }
            
        except Exception as e:
            print(f"Error in calculate_cac: {e}")
            return {}
    
    def analyze_revenue_concentration(self, transactions):
        """Analyze revenue concentration risk"""
        if not transactions:
            return {}
        
        try:
            df = pd.DataFrame(transactions)
            
            # Revenue by customer
            customer_revenue = df.groupby('customer_name')['amount'].sum().sort_values(ascending=False)
            total_revenue = customer_revenue.sum()
            
            # Calculate concentration metrics
            top_10_revenue = customer_revenue.head(10).sum()
            top_20_revenue = customer_revenue.head(20).sum()
            
            concentration_10 = (top_10_revenue / total_revenue * 100) if total_revenue > 0 else 0
            concentration_20 = (top_20_revenue / total_revenue * 100) if total_revenue > 0 else 0
            
            # Herfindahl-Hirschman Index (HHI) - measure of market concentration
            revenue_shares = (customer_revenue / total_revenue) ** 2
            hhi = revenue_shares.sum() * 10000  # Scale to 0-10000
            
            # Risk assessment
            if hhi < 1500:
                risk_level = 'Low'
            elif hhi < 2500:
                risk_level = 'Moderate'
            else:
                risk_level = 'High'
            
            return {
                'total_customers': len(customer_revenue),
                'total_revenue': float(total_revenue),
                'concentration_10': float(concentration_10),
                'concentration_20': float(concentration_20),
                'hhi_index': float(hhi),
                'risk_level': risk_level,
                'top_customers': customer_revenue.head(10).to_dict(),
                'revenue_distribution': {
                    'top_1_percent': len(customer_revenue) // 100 or 1,
                    'top_1_percent_revenue': float(customer_revenue.head(len(customer_revenue) // 100 or 1).sum()),
                    'bottom_50_percent_revenue': float(customer_revenue.tail(len(customer_revenue) // 2).sum())
                }
            }
            
        except Exception as e:
            print(f"Error in analyze_revenue_concentration: {e}")
            return {}
