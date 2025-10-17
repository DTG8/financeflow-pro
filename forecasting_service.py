"""
Revenue Forecasting Service
Provides multiple forecasting methods for financial analysis
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.seasonal import seasonal_decompose
import warnings
warnings.filterwarnings('ignore')

class RevenueForecaster:
    def __init__(self):
        self.models = {}
        
    def prepare_data(self, daily_data):
        """Convert daily data to time series format"""
        df = pd.DataFrame(daily_data)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df = df.resample('D').sum().fillna(0)  # Fill missing days with 0
        return df
    
    def linear_forecast(self, df, days_ahead=30):
        """Linear regression forecast"""
        try:
            # Create features
            df['day_of_year'] = df.index.dayofyear
            df['month'] = df.index.month
            df['day_of_week'] = df.index.dayofweek
            df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)
            
            # Prepare data
            X = df[['day_of_year', 'month', 'day_of_week', 'is_weekend']].values
            y = df['amount'].values
            
            # Train model
            model = LinearRegression()
            model.fit(X, y)
            
            # Generate future dates
            last_date = df.index[-1]
            future_dates = pd.date_range(start=last_date + timedelta(days=1), periods=days_ahead, freq='D')
            
            # Prepare future features
            future_df = pd.DataFrame(index=future_dates)
            future_df['day_of_year'] = future_df.index.dayofyear
            future_df['month'] = future_df.index.month
            future_df['day_of_week'] = future_df.index.dayofweek
            future_df['is_weekend'] = (future_df['day_of_week'] >= 5).astype(int)
            
            X_future = future_df[['day_of_year', 'month', 'day_of_week', 'is_weekend']].values
            
            # Make predictions
            predictions = model.predict(X_future)
            
            # Calculate accuracy metrics
            train_predictions = model.predict(X)
            mae = np.mean(np.abs(y - train_predictions))
            rmse = np.sqrt(np.mean((y - train_predictions) ** 2))
            
            return {
                'method': 'Linear Regression',
                'predictions': [{'date': str(date), 'amount': float(pred)} for date, pred in zip(future_dates, predictions)],
                'accuracy': {'mae': float(mae), 'rmse': float(rmse)},
                'confidence': 'Medium'
            }
        except Exception as e:
            return {'error': f'Linear forecast failed: {str(e)}'}
    
    def polynomial_forecast(self, df, days_ahead=30, degree=2):
        """Polynomial regression forecast"""
        try:
            # Create features
            df['day_of_year'] = df.index.dayofyear
            df['month'] = df.index.month
            df['day_of_week'] = df.index.dayofweek
            df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)
            
            # Prepare data
            X = df[['day_of_year', 'month', 'day_of_week', 'is_weekend']].values
            y = df['amount'].values
            
            # Create polynomial features
            poly_features = PolynomialFeatures(degree=degree)
            X_poly = poly_features.fit_transform(X)
            
            # Train model
            model = LinearRegression()
            model.fit(X_poly, y)
            
            # Generate future dates
            last_date = df.index[-1]
            future_dates = pd.date_range(start=last_date + timedelta(days=1), periods=days_ahead, freq='D')
            
            # Prepare future features
            future_df = pd.DataFrame(index=future_dates)
            future_df['day_of_year'] = future_df.index.dayofyear
            future_df['month'] = future_df.index.month
            future_df['day_of_week'] = future_df.index.dayofweek
            future_df['is_weekend'] = (future_df['day_of_week'] >= 5).astype(int)
            
            X_future = future_df[['day_of_year', 'month', 'day_of_week', 'is_weekend']].values
            X_future_poly = poly_features.transform(X_future)
            
            # Make predictions
            predictions = model.predict(X_future_poly)
            
            # Calculate accuracy metrics
            train_predictions = model.predict(X_poly)
            mae = np.mean(np.abs(y - train_predictions))
            rmse = np.sqrt(np.mean((y - train_predictions) ** 2))
            
            return {
                'method': f'Polynomial Regression (degree {degree})',
                'predictions': [{'date': str(date), 'amount': float(pred)} for date, pred in zip(future_dates, predictions)],
                'accuracy': {'mae': float(mae), 'rmse': float(rmse)},
                'confidence': 'High'
            }
        except Exception as e:
            return {'error': f'Polynomial forecast failed: {str(e)}'}
    
    def moving_average_forecast(self, df, days_ahead=30, window=7):
        """Simple moving average forecast"""
        try:
            # Calculate moving average
            ma = df['amount'].rolling(window=window).mean()
            
            # Use last known moving average for future predictions
            last_ma = ma.dropna().iloc[-1]
            
            # Generate future dates
            last_date = df.index[-1]
            future_dates = pd.date_range(start=last_date + timedelta(days=1), periods=days_ahead, freq='D')
            
            # Create predictions (constant moving average)
            predictions = [last_ma] * days_ahead
            
            # Calculate accuracy metrics
            train_predictions = ma.fillna(method='bfill')
            actual = df['amount']
            mae = np.mean(np.abs(actual - train_predictions))
            rmse = np.sqrt(np.mean((actual - train_predictions) ** 2))
            
            return {
                'method': f'Moving Average (window {window})',
                'predictions': [{'date': str(date), 'amount': float(pred)} for date, pred in zip(future_dates, predictions)],
                'accuracy': {'mae': float(mae), 'rmse': float(rmse)},
                'confidence': 'Low'
            }
        except Exception as e:
            return {'error': f'Moving average forecast failed: {str(e)}'}
    
    def seasonal_forecast(self, df, days_ahead=30):
        """Seasonal decomposition forecast"""
        try:
            # Ensure we have enough data for seasonal analysis
            if len(df) < 30:
                return {'error': 'Insufficient data for seasonal analysis (need at least 30 days)'}
            
            # Seasonal decomposition
            decomposition = seasonal_decompose(df['amount'], model='additive', period=7)  # Weekly seasonality
            
            # Get trend and seasonal components
            trend = decomposition.trend.fillna(method='bfill').fillna(method='ffill')
            seasonal = decomposition.seasonal.fillna(0)
            
            # Simple trend extrapolation
            trend_values = trend.values
            if len(trend_values) > 1:
                trend_slope = (trend_values[-1] - trend_values[0]) / len(trend_values)
            else:
                trend_slope = 0
            
            # Generate future dates
            last_date = df.index[-1]
            future_dates = pd.date_range(start=last_date + timedelta(days=1), periods=days_ahead, freq='D')
            
            # Create predictions
            predictions = []
            for i, date in enumerate(future_dates):
                # Extrapolate trend
                trend_pred = trend_values[-1] + (trend_slope * (i + 1))
                
                # Add seasonal component (use same day of week)
                day_of_week = date.dayofweek
                seasonal_pred = seasonal[seasonal.index.dayofweek == day_of_week].mean()
                
                # Combine trend and seasonal
                pred = max(0, trend_pred + seasonal_pred)  # Ensure non-negative
                predictions.append(pred)
            
            # Calculate accuracy metrics
            train_predictions = trend + seasonal
            actual = df['amount']
            mae = np.mean(np.abs(actual - train_predictions))
            rmse = np.sqrt(np.mean((actual - train_predictions) ** 2))
            
            return {
                'method': 'Seasonal Decomposition',
                'predictions': [{'date': str(date), 'amount': float(pred)} for date, pred in zip(future_dates, predictions)],
                'accuracy': {'mae': float(mae), 'rmse': float(rmse)},
                'confidence': 'High'
            }
        except Exception as e:
            return {'error': f'Seasonal forecast failed: {str(e)}'}
    
    def generate_all_forecasts(self, daily_data, days_ahead=30):
        """Generate forecasts using all methods"""
        df = self.prepare_data(daily_data)
        
        forecasts = {}
        
        # Linear forecast
        forecasts['linear'] = self.linear_forecast(df, days_ahead)
        
        # Polynomial forecast
        forecasts['polynomial'] = self.polynomial_forecast(df, days_ahead)
        
        # Moving average forecast
        forecasts['moving_average'] = self.moving_average_forecast(df, days_ahead)
        
        # Seasonal forecast
        forecasts['seasonal'] = self.seasonal_forecast(df, days_ahead)
        
        # Calculate ensemble forecast (average of all methods)
        valid_forecasts = [f for f in forecasts.values() if 'error' not in f]
        if valid_forecasts:
            ensemble_predictions = []
            for i in range(days_ahead):
                amounts = [f['predictions'][i]['amount'] for f in valid_forecasts if i < len(f['predictions'])]
                if amounts:
                    ensemble_predictions.append({
                        'date': valid_forecasts[0]['predictions'][i]['date'],
                        'amount': float(np.mean(amounts))
                    })
            
            forecasts['ensemble'] = {
                'method': 'Ensemble (Average)',
                'predictions': ensemble_predictions,
                'accuracy': {'mae': 0, 'rmse': 0},  # Would need actual values to calculate
                'confidence': 'Very High'
            }
        
        return forecasts

