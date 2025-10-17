# FinanceFlow - Advanced Financial Analytics Platform

A comprehensive financial analytics platform built with Flask, featuring advanced forecasting, customer analytics, and automated reporting capabilities.

## 🚀 Features

### Core Analytics
- **Revenue Forecasting** - Multiple ML models (Linear Regression, Polynomial, Seasonal Decomposition, Ensemble)
- **Customer Analytics** - CLV calculation, RFM analysis, customer segmentation
- **Advanced KPIs** - Comprehensive performance metrics and insights
- **MRC Analyzer** - Specialized tool for Monthly Recurring Charge analysis
- **Automated Reports** - Monthly, quarterly, and annual PDF report generation

### Data Management
- **Multi-format Support** - Excel, CSV file uploads
- **Real-time Processing** - Live data analysis and visualization
- **Database Management** - SQLite with upgrade path to PostgreSQL
- **File Management** - Secure upload and processing system

### Visualization
- **Interactive Charts** - Chart.js powered visualizations
- **Time Series Analysis** - Advanced date handling and trend analysis
- **Responsive Design** - Mobile-friendly Bootstrap 5 interface
- **Real-time Updates** - Live data refresh capabilities

## 🛠️ Technology Stack

- **Backend**: Flask (Python)
- **Database**: SQLite (production-ready PostgreSQL support)
- **Frontend**: HTML5, CSS3, JavaScript, Bootstrap 5
- **Charts**: Chart.js with time adapter
- **ML/Analytics**: scikit-learn, statsmodels, pandas, numpy
- **File Processing**: pandas, openpyxl
- **PDF Generation**: ReportLab

## 📦 Installation

### Local Development

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd projectAlpha
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the application**
   ```bash
   python app.py
   ```

5. **Access the application**
   Open your browser and navigate to `http://127.0.0.1:5000`

### Production Deployment

#### Option 1: Heroku (Recommended)
1. **Install Heroku CLI**
2. **Login to Heroku**
   ```bash
   heroku login
   ```
3. **Create Heroku app**
   ```bash
   heroku create your-app-name
   ```
4. **Deploy**
   ```bash
   git add .
   git commit -m "Initial deployment"
   git push heroku main
   ```

#### Option 2: Railway
1. **Connect GitHub repository**
2. **Deploy automatically** - Railway will detect Flask app and deploy

#### Option 3: DigitalOcean App Platform
1. **Create new app**
2. **Connect GitHub repository**
3. **Configure build settings**
4. **Deploy**

## 🔧 Configuration

### Environment Variables
Create a `.env` file for production:
```env
FLASK_ENV=production
SECRET_KEY=your-secret-key
DATABASE_URL=postgresql://user:password@host:port/database
```

### Database Upgrade (Production)
For production, upgrade to PostgreSQL:
1. **Install PostgreSQL**
2. **Update DATABASE_URL** in environment
3. **Run migrations** (if needed)

## 📊 Usage

### 1. Upload Data
- Navigate to **Database** section
- Upload Excel/CSV files with transaction data
- Files are automatically processed and stored

### 2. View Analytics
- **Reports**: Basic revenue and transaction analytics
- **Forecasting**: ML-powered revenue predictions
- **Customer Analytics**: CLV, segmentation, and behavioral insights
- **Advanced KPIs**: Comprehensive performance metrics
- **MRC Analyzer**: Specialized recurring charge analysis

### 3. Generate Reports
- **Auto Reports**: Generate monthly, quarterly, or annual PDF reports
- **Export Data**: Download analysis results as Excel files

## 🏗️ Architecture

```
projectAlpha/
├── app.py                 # Main Flask application
├── forecasting_service.py # Revenue forecasting algorithms
├── customer_analytics_service.py # Customer analytics engine
├── templates/            # HTML templates
│   ├── base.html
│   ├── reports.html
│   ├── forecasting.html
│   ├── customer_analytics.html
│   ├── advanced_kpis.html
│   ├── automated_reports.html
│   └── mrc_analyzer.html
├── uploads/              # File upload directory
├── requirements.txt      # Python dependencies
├── Procfile             # Heroku deployment config
└── README.md
```

## 🔒 Security Features

- **File Upload Validation** - Secure file type and size checking
- **SQL Injection Protection** - SQLAlchemy ORM protection
- **XSS Prevention** - Template escaping
- **CSRF Protection** - Flask-WTF integration ready

## 📈 Performance

- **Optimized Queries** - Efficient database queries
- **Caching Ready** - Redis integration ready
- **Async Processing** - Background task support
- **Scalable Architecture** - Microservices ready

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For support and questions:
- Create an issue in the repository
- Contact: [your-email@domain.com]

## 🎯 Roadmap

- [ ] Real-time notifications
- [ ] Advanced fraud detection
- [ ] API endpoints for external integrations
- [ ] Mobile app support
- [ ] Advanced machine learning models
- [ ] Multi-tenant support
- [ ] Advanced security features

---

**FinanceFlow** - Empowering financial decisions with data-driven insights.