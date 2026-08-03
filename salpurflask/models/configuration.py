"""Configuration model for storing editable app settings"""

from salpurflask.extensions import db
from datetime import datetime


class AppConfiguration(db.Model):
    """Store application configuration that can be edited at runtime"""
    __tablename__ = 'app_configuration'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(255))
    data_type = db.Column(db.String(20), default='string')  # string, integer, boolean
    category = db.Column(db.String(50))  # app, company, system, email
    editable = db.Column(db.Boolean, default=True)
    requires_restart = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(100))  # username

    def __repr__(self):
        return f"<AppConfiguration {self.key}={self.value}>"

    @classmethod
    def get_value(cls, key, default=None):
        """Get configuration value by key"""
        config = cls.query.filter_by(key=key).first()
        if config:
            # Convert based on data_type
            if config.data_type == 'boolean':
                return config.value.lower() in ('true', '1', 'yes')
            elif config.data_type == 'integer':
                try:
                    return int(config.value)
                except (ValueError, TypeError):
                    return default
            return config.value
        return default

    @classmethod
    def set_value(cls, key, value, updated_by=None, description=None, data_type='string', category=None, requires_restart=False):
        """Set configuration value"""
        config = cls.query.filter_by(key=key).first()
        if not config:
            config = cls(
                key=key,
                data_type=data_type,
                category=category,
                description=description,
                requires_restart=requires_restart
            )
            db.session.add(config)

        config.value = str(value)
        config.updated_at = datetime.utcnow()
        config.updated_by = updated_by
        db.session.commit()
        return config

    @classmethod
    def get_all_by_category(cls, category):
        """Get all configurations for a category"""
        return cls.query.filter_by(category=category).all()

    @classmethod
    def init_defaults(cls):
        """Initialize default configuration values"""
        defaults = [
            # App settings
            {
                'key': 'APP_NAME',
                'value': 'TradeFlow',
                'description': 'Application Name',
                'category': 'app',
                'data_type': 'string',
                'requires_restart': False
            },
            {
                'key': 'COMPANY_NAME',
                'value': 'Multi Computer Forms',
                'description': 'Company/Business Name',
                'category': 'company',
                'data_type': 'string',
                'requires_restart': False
            },
            {
                'key': 'COMPANY_TAGLINE',
                'value': 'Inventory & Accounts Management',
                'description': 'Company Tagline',
                'category': 'company',
                'data_type': 'string',
                'requires_restart': False
            },
            {
                'key': 'DESIGNED_DEVELOPED',
                'value': 'Sabir Shah (sabir1212@yahoo.com)',
                'description': 'Developer Info',
                'category': 'company',
                'data_type': 'string',
                'requires_restart': False
            },
            # System settings
            {
                'key': 'APP_TIMEZONE',
                'value': 'Asia/Karachi',
                'description': 'Application Timezone',
                'category': 'system',
                'data_type': 'string',
                'requires_restart': True
            },
            {
                'key': 'CURRENCY',
                'value': 'Rs',
                'description': 'Currency Symbol',
                'category': 'system',
                'data_type': 'string',
                'requires_restart': False
            },
            {
                'key': 'FISCAL_YEAR_START_MONTH',
                'value': '7',
                'description': 'Fiscal Year Start Month (1-12)',
                'category': 'system',
                'data_type': 'integer',
                'requires_restart': False
            },
            {
                'key': 'ALLOW_SIGNUP',
                'value': 'false',
                'description': 'Allow User Signup',
                'category': 'system',
                'data_type': 'boolean',
                'requires_restart': False
            },
            # Email settings
            {
                'key': 'MAIL_SERVER',
                'value': 'smtp.gmail.com',
                'description': 'Email Server Address',
                'category': 'email',
                'data_type': 'string',
                'requires_restart': False
            },
            {
                'key': 'MAIL_PORT',
                'value': '587',
                'description': 'Email Server Port',
                'category': 'email',
                'data_type': 'integer',
                'requires_restart': False
            },
            {
                'key': 'MAIL_USERNAME',
                'value': '',
                'description': 'Email Username/Address',
                'category': 'email',
                'data_type': 'string',
                'requires_restart': False
            },
        ]

        for default in defaults:
            existing = cls.query.filter_by(key=default['key']).first()
            if not existing:
                config = cls(**default)
                db.session.add(config)

        db.session.commit()
